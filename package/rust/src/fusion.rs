use crate::{hs, ortho};
use memmap2::{Mmap, MmapOptions};
use nalgebra::{Matrix3, Point3, Vector3};
use numpy::{ndarray::Array2, IntoPyArray, PyArray1, PyArray2, PyArray3, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use std::fs::File;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::Mutex;
use rayon::prelude::*;
use pyo3::types::PyBytes;
const BLOCK_INTERVALS: usize = 64;
struct Swath {
    index: i32,
    ignore_value: Option<f32>,
    times: Vec<f64>,
    header: hs::EnviMetadata,
    data: Mmap,
    frames: Vec<Option<Frame>>,
    blocks: Vec<Block>,
}
#[derive(Clone, Copy)]
struct Frame {
    center: Point3<f64>,
    world_to_camera: Matrix3<f64>,
}
#[derive(Clone, Copy)]
struct Block {
    first: usize,
    last: usize, // exclusive end of interval indices
    coefficients: [(f64, f64); 4], // q_y = a*x + b*y + c*z + d
    min_time: f64,
    max_time: f64,
}
#[derive(Clone)]
struct Candidate {
    swath: usize,
    line: usize,
    sample: usize,
    angle: f64,
    depth: f64,
    error: f64,
}
fn bad(message: impl ToString) -> PyErr { PyValueError::new_err(message.to_string()) }
fn pose_at(time: f64, trajectory: &[ortho::ExteriorOrientation],
           io: &ortho::InteriorOrientation) -> Option<Frame> {
    let idx = trajectory.partition_point(|eo| eo.gps_seconds_of_week < time);
    if idx == 0 || idx == trajectory.len() { return None; }
    let eo = ortho::interpolate_eo(time, &trajectory[idx-1], &trajectory[idx]);
    let rot = ortho::calculate_rotation_matrix(&eo, io);
    let arm = rot * Vector3::new(io.lever_arm_x, io.lever_arm_y, io.lever_arm_z);
    Some(Frame {
        center: Point3::new(eo.easting+arm.x, eo.northing+arm.y, eo.altitude+arm.z),
        world_to_camera: rot.transpose(),
    })
}
fn project(point: &Point3<f64>, frame: Frame) -> Option<(Vector3<f64>, f64)> {
    let q = frame.world_to_camera * (point-frame.center);
    let range = q.norm();
    if range.is_finite() && q.z < 0.0 && range >= 1e-5 { Some((q,range)) } else { None }
}
fn plane(frame: Frame) -> [f64; 4] {
    let r = frame.world_to_camera.row(1);
    let (a,b,c) = (r[0],r[1],r[2]);
    [a,b,c,-(a*frame.center.x+b*frame.center.y+c*frame.center.z)]
}
fn blocks(frames: &[Option<Frame>], times: &[f64]) -> Vec<Block> {
    let mut result = Vec::new();
    for first in (0..frames.len().saturating_sub(1)).step_by(BLOCK_INTERVALS) {
        let last = (first+BLOCK_INTERVALS).min(frames.len()-1);
        let mut lower = [f64::INFINITY;4];
        let mut upper = [f64::NEG_INFINITY;4];
        for frame in frames[first..=last].iter().flatten() {
            let coeff = plane(*frame);
            for j in 0..4 {
                lower[j] = lower[j].min(coeff[j]);
                upper[j] = upper[j].max(coeff[j]);
            }
        }
        result.push(Block {
            first,last,min_time:times[first],max_time:times[last],
            coefficients: std::array::from_fn(|j| (lower[j],upper[j])),
        });
    }
    result
}
fn could_cross(block: &Block, p: &Point3<f64>, cross_tol: f64) -> bool {
    // Evaluate interval enclosure of all discrete endpoint q_y values.
    // Every sign-changing interval has both endpoints inside this bound.
    if block.coefficients.iter().any(|(lo,hi)| !lo.is_finite() || !hi.is_finite()) { return true; }
    let mut low=0.0;
    let mut high=0.0;
    for (v,(lo,hi)) in [p.x,p.y,p.z,1.0].iter().zip(block.coefficients.iter()) {
        low += (v*lo).min(v*hi);
        high += (v*lo).max(v*hi);
    }
    // Slack absorbs a few ulps of arithmetic drift for large projected coords.
    let slack = cross_tol + 1e-8*(1.0 + p.coords.norm());
    !(low > slack || high < -slack)
}
fn check_header(path: &str) -> PyResult<()> {
    let text=std::fs::read_to_string(path).map_err(|e| PyIOError::new_err(e.to_string()))?;
    let mut dtype=None;
    let mut order=None;
    let mut offset=None;
    for line in text.lines() {
        if let Some((k,v))=line.split_once('=') {
            let v=v.trim().trim_matches(|c| c=='{' || c=='}').trim();
            match k.trim().to_ascii_lowercase().as_str() {
                "data type" => dtype=v.parse::<u32>().ok(),
                "byte order" => order=v.parse::<u32>().ok(),
                "header offset" => offset=v.parse::<u64>().ok(),
                _ => (),
            }
        }
    }
    if dtype!=Some(4) || order!=Some(0) || offset.unwrap_or(0)!=0 {
        return Err(bad(format!("Expected ENVI little-endian float32, zero header offset: {path}")));
    }
    Ok(())
}
fn ignore_value(path:&str)->PyResult<Option<f32>> {
    let text=std::fs::read_to_string(path).map_err(|e|PyIOError::new_err(e.to_string()))?;
    for line in text.lines() {
        if let Some((key,value))=line.split_once('=') {
            if key.trim().eq_ignore_ascii_case("data ignore value") {
                let value=value.trim().trim_matches(|c|c=='{' || c=='}').trim();
                let parsed=value.parse::<f32>().map_err(|_|bad("Invalid ENVI data ignore value"))?;
                return Ok(Some(parsed));
            }
        }
    }
    Ok(None)
}
fn prepare(io:ortho::InteriorOrientation, trajectory:Vec<ortho::ExteriorOrientation>,
           descriptors:Vec<(i32,String,String,Vec<f64>)>)
    ->PyResult<(ortho::InteriorOrientation,Vec<ortho::ExteriorOrientation>,Vec<Swath>)> {
    if io.spatial_pixels<2 || !io.field_of_view_rad.is_finite() || io.field_of_view_rad<=0.0 || io.field_of_view_rad>=std::f64::consts::PI {
        return Err(bad("Invalid camera settings"));
    }
    if [io.boresight_roll,io.boresight_pitch,io.boresight_yaw,io.lever_arm_x,io.lever_arm_y,io.lever_arm_z].iter().any(|v|!v.is_finite()) {return Err(bad("Non-finite calibration"));}
    if trajectory.len()<2 || trajectory.iter().any(|e|[e.gps_seconds_of_week,e.easting,e.northing,e.altitude,e.roll,e.pitch,e.yaw].iter().any(|v|!v.is_finite())) || trajectory.windows(2).any(|w|w[1].gps_seconds_of_week<=w[0].gps_seconds_of_week) {return Err(bad("Invalid trajectory/time ordering"));}
    if descriptors.is_empty() {return Err(bad("No swaths"));}
    let mut swaths=Vec::new();
    let mut expected_bands=None;
    for (index,hdr_path,bin_path,times) in descriptors {
        check_header(&hdr_path)?;
        let ignore=ignore_value(&hdr_path)?;
        let header=hs::parse_envi_header(&hdr_path).map_err(|e|PyIOError::new_err(e.to_string()))?;
        if header.samples!=io.spatial_pixels || header.bands==0 || header.lines==0 || header.lines>i32::MAX as usize || header.samples>i32::MAX as usize {return Err(bad("Camera/image dimensions mismatch"));}
        if !["bil","bip","bsq"].contains(&header.interleave.as_str()) {return Err(bad("Unsupported ENVI interleave"));}
        if expected_bands.replace(header.bands).is_some_and(|n|n!=header.bands) {return Err(bad("Swath band counts differ"));}
        if times.len()!=header.lines || times.iter().any(|t|!t.is_finite()) || times.windows(2).any(|w|w[1]<=w[0]) {return Err(bad("Image line/time mismatch"));}
        if times[times.len()-1]<trajectory[0].gps_seconds_of_week || times[0]>trajectory[trajectory.len()-1].gps_seconds_of_week {return Err(bad("Swath outside trajectory time range"));}
        let file=File::open(&bin_path).map_err(|e|PyIOError::new_err(e.to_string()))?;
        let expected=(header.samples as u64).checked_mul(header.lines as u64)
            .and_then(|n|n.checked_mul(header.bands as u64)).and_then(|n|n.checked_mul(4))
            .ok_or_else(||bad("Cube size overflow"))?;
        if file.metadata().map_err(|e|PyIOError::new_err(e.to_string()))?.len()!=expected {return Err(bad("Cube size mismatch"));}
        let data=unsafe {MmapOptions::new().map(&file)}.map_err(|e|PyIOError::new_err(e.to_string()))?;
        let frames=times.iter().map(|t|pose_at(*t,&trajectory,&io)).collect::<Vec<_>>();
        let interval_blocks=blocks(&frames,&times);
        swaths.push(Swath {index,ignore_value:ignore,times,header,data,frames,blocks:interval_blocks});
    }
    swaths.sort_by_key(|s|s.index);
    if swaths.windows(2).any(|w|w[0].index==w[1].index) {return Err(bad("Duplicate swath identifiers"));}
    Ok((io,trajectory,swaths))
}
fn candidates(point:&Point3<f64>,swaths:&[Swath],trajectory:&[ortho::ExteriorOrientation],
              io:&ortho::InteriorOrientation,max_gap:f64,cross_tol:f64,at_time:Option<f64>)->Vec<Candidate> {
    let mut out=Vec::new();
    let center_sample=(io.spatial_pixels as f64-1.0)/2.0;
    let ifov=io.field_of_view_rad/io.spatial_pixels as f64;
    for (si,sw) in swaths.iter().enumerate() {
        for block in &sw.blocks {
            if at_time.is_some_and(|t| t < block.min_time-max_gap || t > block.max_time+max_gap) { continue; }
            if !could_cross(block,point,cross_tol) { continue; }
            for i in block.first..block.last {
                let (Some(pa),Some(pb))=(sw.frames[i],sw.frames[i+1]) else { continue; };
                let (Some((qa,_)),Some((qb,_)))=(project(point,pa),project(point,pb)) else { continue; };
                let gap=sw.times[i+1]-sw.times[i];
                if gap<=0.0 || gap>max_gap ||
                    !((qa.y<=0.0 && qb.y>=0.0)||(qa.y>=0.0 && qb.y<=0.0)) { continue; }
                if at_time.is_some_and(|t| t < sw.times[i]-gap || t > sw.times[i+1]+gap) { continue; }
                if qa.y==0.0 && qb.y==0.0 { continue; }
                let (mut lo,mut hi)=(sw.times[i],sw.times[i+1]);
                let left_sign=qa.y<=0.0;
                let mut root_ok=true;
                for _ in 0..14 {
                    let mid=(lo+hi)*0.5;
                    let Some(frame)=pose_at(mid,trajectory,io) else { root_ok=false; break; };
                    let Some((q,_))=project(point,frame) else { root_ok=false; break; };
                    if (q.y<=0.0)==left_sign { lo=mid; } else { hi=mid; }
                }
                if !root_ok { continue; }
                let t=(lo+hi)*0.5;
                let line=if t-sw.times[i]<sw.times[i+1]-t { i } else { i+1 };
                if at_time.is_some_and(|time| (sw.times[line]-time).abs()>gap*0.5+1e-9) { continue; }
                let Some((q,range))=project(point,sw.frames[line].unwrap()) else { continue; };
                let error=q.y.abs();
                let sample_f=q.x.atan2(-q.z)/ifov+center_sample;
                let sample_r=sample_f.round();
                if error<=cross_tol && sample_r>=0.0 && sample_r<sw.header.samples as f64 {
                    let sample=sample_r as usize;
                    let expected=ortho::get_sensor_vector(sample,io);
                    let residual=(q/range).dot(&expected).clamp(-1.0,1.0).acos();
                    if residual<=ifov*0.75+cross_tol/range {
                        let angle=q.x.atan2(-q.z).abs();
                        out.push(Candidate { swath:si,line,sample,angle,depth:range,error });
                    }
                }
            }
        }
    }
    out
}
// Candidate spool v1: u32 row count, then per row u32 candidate count
// followed by 36-byte records (three u32 indices, three f64 metrics).
const RECORD_BYTES:usize=36;
fn next_u32(input:&[u8],pos:&mut usize)->PyResult<u32> {
    let end=pos.checked_add(4).ok_or_else(||bad("Spool offset overflow"))?;
    let bytes=input.get(*pos..end).ok_or_else(||bad("Truncated candidate spool"))?;
    *pos=end;
    Ok(u32::from_le_bytes(bytes.try_into().unwrap()))
}
fn next_f64(input:&[u8],pos:&mut usize)->PyResult<f64> {
    let end=pos.checked_add(8).ok_or_else(||bad("Spool offset overflow"))?;
    let bytes=input.get(*pos..end).ok_or_else(||bad("Truncated candidate spool"))?;
    *pos=end;
    Ok(f64::from_le_bytes(bytes.try_into().unwrap()))
}
fn store_candidate(out:&mut Vec<u8>,c:&Candidate)->PyResult<()> {
    for v in [c.swath,c.line,c.sample] {
        out.extend_from_slice(&u32::try_from(v).map_err(|_|bad("Cell index exceeds u32"))?.to_le_bytes());
    }
    for v in [c.angle,c.depth,c.error] {out.extend_from_slice(&v.to_le_bytes());}
    Ok(())
}
fn decode_candidate(data:&[u8])->Candidate {
    let mut pos=0;
    Candidate {
        swath:next_u32(data,&mut pos).unwrap() as usize,
        line:next_u32(data,&mut pos).unwrap() as usize,
        sample:next_u32(data,&mut pos).unwrap() as usize,
        angle:next_f64(data,&mut pos).unwrap(),
        depth:next_f64(data,&mut pos).unwrap(),
        error:next_f64(data,&mut pos).unwrap(),
    }
}
fn validate_spool(data:&[u8],n:usize,swaths:&[Swath])->PyResult<Vec<(usize,usize)>> {
    let mut pos=0;
    if next_u32(data,&mut pos)? as usize!=n {return Err(bad("Spool row count mismatch"));}
    let mut spans=Vec::with_capacity(n);
    for _ in 0..n {
        let count=next_u32(data,&mut pos)? as usize;
        let end=pos.checked_add(count.checked_mul(RECORD_BYTES)
            .ok_or_else(||bad("Candidate spool size overflow"))?)
            .ok_or_else(||bad("Candidate spool size overflow"))?;
        if end>data.len() {return Err(bad("Truncated candidate spool"));}
        for record in data[pos..end].chunks_exact(RECORD_BYTES) {
            let mut at=0;
            let sw=next_u32(record,&mut at)? as usize;
            let line=next_u32(record,&mut at)? as usize;
            let sample=next_u32(record,&mut at)? as usize;
            if sw>=swaths.len() || line>=swaths[sw].header.lines || sample>=swaths[sw].header.samples {
                return Err(bad("Spool cell is outside cube"));
            }
        }
        spans.push((pos,end));
        pos=end;
    }
    if pos!=data.len() {return Err(bad("Trailing bytes in candidate spool"));}
    Ok(spans)
}
type Cell=(usize,usize,usize);
struct Observation {
    spectra: Vec<f32>, valid: u8, source: i32, line: i32, sample: i32, angle: f32,
}
impl Observation {
    fn empty(bands:usize)->Self {
        Self { spectra:vec![f32::NAN;bands],valid:0,source:-1,line:-1,sample:-1,angle:f32::NAN }
    }
}
#[pyclass]
pub struct Flight {
    io: ortho::InteriorOrientation,
    trajectory: Vec<ortho::ExteriorOrientation>,
    swaths: Vec<Swath>,
    min_depth: Mutex<Arc<HashMap<Cell,f64>>>,
    groups: Vec<(usize,usize)>,
}
fn validate(max_gap:f64,cross_tol:f64,occlusion_radius:f64,depth_tol:f64)->PyResult<()> {
    if [max_gap,cross_tol,occlusion_radius,depth_tol].iter().any(|v| !v.is_finite() || *v<=0.0) {
        return Err(bad("Tolerances must be positive and finite"));
    }
    Ok(())
}
fn point_rows(xyz:PyReadonlyArray2<'_,f64>)->PyResult<Vec<[f64;3]>> {
    let v=xyz.as_array();
    if v.ncols()!=3 { return Err(bad("Expected Nx3 XYZ array")); }
    Ok(v.rows().into_iter().map(|r| [r[0],r[1],r[2]]).collect())
}
fn time_rows(times:Option<PyReadonlyArray1<'_,f64>>,n:usize)->PyResult<Option<Vec<f64>>> {
    times.map(|a| {
        let v=a.as_array();
        if v.len()!=n { return Err(bad("GPS time length mismatch")); }
        if v.iter().any(|t| !t.is_finite()) { return Err(bad("Non-finite GPS time")); }
        Ok(v.iter().copied().collect())
    }).transpose()
}
impl Flight {
    fn group_value(&self,sw:&Swath,line:usize,sample:usize,output_band:usize)->f32 {
        let (a,b)=self.groups[output_band];
        let read=|band:usize|hs::read_spectrum_value(&sw.data,&sw.header.interleave,
            line,sample,band,sw.header.lines,sw.header.samples,sw.header.bands);
        let va=read(a);
        let vb=if a==b {va} else {read(b)};
        if !va.is_finite() || !vb.is_finite() || sw.ignore_value.is_some_and(|n|va==n || vb==n) {
            return f32::NAN;
        }
        ((va as f64+vb as f64)*0.5) as f32
    }
    fn list(&self,xyz:[f64;3],at_time:Option<f64>,max_gap:f64,cross_tol:f64)->Vec<Candidate> {
        if !xyz.iter().all(|v| v.is_finite()) { return Vec::new(); }
        candidates(&Point3::new(xyz[0],xyz[1],xyz[2]),&self.swaths,
            &self.trajectory,&self.io,max_gap,cross_tol,at_time)
    }
    fn choose(&self,xyz:[f64;3],at_time:Option<f64>,max_gap:f64,cross_tol:f64,
              depth_tol:f64,minima:&HashMap<Cell,f64>)->Observation {
        let bands=self.groups.len();
        let mut obs=Observation::empty(bands);
        let list=self.list(xyz,at_time,max_gap,cross_tol);
        let best=list.iter().filter(|c| minima.get(&(c.swath,c.line,c.sample))
            .is_some_and(|min| c.depth<=min+depth_tol))
            .min_by(|a,b| a.angle.total_cmp(&b.angle).then(a.error.total_cmp(&b.error))
                .then(a.swath.cmp(&b.swath)).then(a.line.cmp(&b.line))
                .then(a.sample.cmp(&b.sample)));
        if let Some(c)=best {
            let sw=&self.swaths[c.swath];
            for (band,v) in obs.spectra.iter_mut().enumerate() {
                *v=self.group_value(sw,c.line,c.sample,band);
            }
            if obs.spectra.iter().all(|v| v.is_finite()) {
                obs.valid=1; obs.source=sw.index; obs.line=c.line as i32;
                obs.sample=c.sample as i32; obs.angle=c.angle.to_degrees() as f32;
            } else { obs.spectra.fill(f32::NAN); }
        }
        obs
    }
}
#[pymethods]
impl Flight {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(trajectory:PyReadonlyArray2<'_,f64>,descriptors:Vec<(i32,String,String,Vec<f64>)>,
           samples:usize,field_of_view_rad:f64,roll:f64,pitch:f64,yaw:f64,
           lx:f64,ly:f64,lz:f64,groups:Vec<(usize,usize)>)->PyResult<Self> {
        let view=trajectory.as_array();
        if view.ncols()!=7 {return Err(bad("Trajectory requires seven columns"));}
        let trajectory=view.rows().into_iter().map(|r|ortho::ExteriorOrientation {
            gps_seconds_of_week:r[0],easting:r[1],northing:r[2],altitude:r[3],roll:r[4],pitch:r[5],yaw:r[6],
        }).collect();
        let io=ortho::InteriorOrientation {spatial_pixels:samples,field_of_view_rad,
            boresight_roll:roll.to_radians(),boresight_pitch:pitch.to_radians(),boresight_yaw:yaw.to_radians(),
            lever_arm_x:lx,lever_arm_y:ly,lever_arm_z:lz};
        let (io,trajectory,swaths)=prepare(io,trajectory,descriptors)?;
        let source_bands=swaths[0].header.bands;
        let groups=if groups.is_empty() {(0..source_bands).map(|b|(b,b)).collect()} else {groups};
        if groups.iter().any(|(a,b)|*a>=source_bands || *b>=source_bands) {
            return Err(bad("Spectral group index outside source cube"));
        }
        Ok(Self {io,trajectory,swaths,groups,min_depth:Mutex::new(Arc::new(HashMap::new()))})
    }
    #[getter]
    fn source_bands(&self)->usize {self.swaths[0].header.bands}
    #[allow(clippy::too_many_arguments)]
    fn mosaic_chunk<'py>(&self,py:Python<'py>,dsm:PyReadonlyArray2<'py,f64>,
                        dsm_min_x:f64,dsm_max_y:f64,resolution:f64,
                        chunk_min_x:f64,chunk_max_y:f64,chunk_cols:usize,chunk_rows:usize,
                        bands:Vec<usize>)->PyResult<&'py PyArray3<f32>> {
        if bands.is_empty() || bands.iter().any(|b|*b>=self.groups.len()) {return Err(bad("Invalid output band selection"));}
        if !resolution.is_finite() || resolution<=0.0 || chunk_cols==0 || chunk_rows==0 {return Err(bad("Invalid grid"));}
        let view=dsm.as_array();
        let (rows,cols)=(view.nrows(),view.ncols());
        let surface=dsm.as_slice()?;
        let cells=chunk_rows.checked_mul(chunk_cols).ok_or_else(||bad("Tile size overflow"))?;
        let size=cells.checked_mul(bands.len()).ok_or_else(||bad("Band size overflow"))?;
        let mut grid=vec![f32::NAN;size];
        let mut quality=vec![f64::MAX;cells];
        py.allow_threads(|| {
            for sw in &self.swaths {
                let pixels=ortho::orthorectify_swath_chunked(&sw.times,&self.trajectory,&self.io,
                    surface,cols,rows,dsm_min_x,dsm_max_y,chunk_min_x,chunk_max_y,
                    chunk_cols,chunk_rows,resolution,resolution);
                for px in pixels {
                    let spectrum=bands.iter().map(|b|self.group_value(sw,px.line_idx,px.sample_idx,*b)).collect::<Vec<_>>();
                    if spectrum.iter().any(|v|!v.is_finite()) {continue;}
                    let c=((px.utm_x-chunk_min_x)/resolution).floor() as isize;
                    let r=((chunk_max_y-px.utm_y)/resolution).floor() as isize;
                    let center=sw.header.samples as f64/2.0;
                    let deviation=(px.sample_idx as f64-center).abs()/center;
                    let base=px.nadir_distance*(1.0+5.0*deviation*deviation);
                    let limit=(1.5*resolution).powi(2);
                    for dr in -1isize..=1 {
                        for dc in -1isize..=1 {
                            let (rr,cc)=(r+dr,c+dc);
                            if rr<0 || cc<0 || rr>=chunk_rows as isize || cc>=chunk_cols as isize {continue;}
                            let x=chunk_min_x+(cc as f64+0.5)*resolution;
                            let y=chunk_max_y-(rr as f64+0.5)*resolution;
                            let distance=(px.utm_x-x).powi(2)+(px.utm_y-y).powi(2);
                            if distance>limit {continue;}
                            let cell=rr as usize*chunk_cols+cc as usize;
                            let score=base*(1.0+distance/limit);
                            if score<quality[cell] {
                                quality[cell]=score;
                                for (band,value) in spectrum.iter().enumerate() {grid[band*cells+cell]=*value;}
                            }
                        }
                    }
                }
            }
        });
        let array=numpy::ndarray::Array3::from_shape_vec((bands.len(),chunk_rows,chunk_cols),grid)
            .map_err(|e|bad(e.to_string()))?;
        Ok(array.into_pyarray(py))
    }
    #[getter]
    fn bands(&self)->usize { self.groups.len() }
    #[getter]
    fn swath_ids(&self)->Vec<i32> { self.swaths.iter().map(|s| s.index).collect() }
    #[pyo3(signature = (xyz, times, max_gap, cross_tol, occlusion_radius, depth_tol))]
    #[allow(clippy::too_many_arguments)]
    fn accumulate(&self,py:Python<'_>,xyz:PyReadonlyArray2<'_,f64>,
        times:Option<PyReadonlyArray1<'_,f64>>,max_gap:f64,cross_tol:f64,
        occlusion_radius:f64,depth_tol:f64)->PyResult<()> {
        validate(max_gap,cross_tol,occlusion_radius,depth_tol)?;
        let points=point_rows(xyz)?;
        let times=time_rows(times,points.len())?;
        let local:HashMap<Cell,f64>=py.allow_threads(|| {
            points.par_iter().enumerate().fold(HashMap::new,|mut map,(i,p)| {
                for c in self.list(*p,times.as_ref().map(|t| t[i]),max_gap,cross_tol) {
                    let slot=map.entry((c.swath,c.line,c.sample)).or_insert(c.depth);
                    *slot=(*slot).min(c.depth);
                }
                map
            }).reduce(HashMap::new,|mut a,b| {
                for (k,depth) in b {
                    let slot=a.entry(k).or_insert(depth);
                    *slot=(*slot).min(depth);
                }
                a
            })
        });
        let mut global=self.min_depth.lock().map_err(|_| bad("Depth lock poisoned"))?;
        let target=Arc::make_mut(&mut *global);
        for (k,depth) in local {
            let slot=target.entry(k).or_insert(depth);
            *slot=(*slot).min(depth);
        }
        Ok(())
    }
    #[pyo3(signature = (xyz, times, max_gap, cross_tol, occlusion_radius, depth_tol))]
    #[allow(clippy::too_many_arguments)]
    fn paint<'py>(&self,py:Python<'py>,xyz:PyReadonlyArray2<'_,f64>,
        times:Option<PyReadonlyArray1<'_,f64>>,max_gap:f64,cross_tol:f64,
        occlusion_radius:f64,depth_tol:f64)
        ->PyResult<(&'py PyArray2<f32>,&'py PyArray1<u8>,&'py PyArray1<i32>,
                   &'py PyArray1<i32>,&'py PyArray1<i32>,&'py PyArray1<f32>)> {
        validate(max_gap,cross_tol,occlusion_radius,depth_tol)?;
        let points=point_rows(xyz)?;
        let times=time_rows(times,points.len())?;
        let global=Arc::clone(&*self.min_depth.lock().map_err(|_| bad("Depth lock poisoned"))?);
        let rows=py.allow_threads(|| points.par_iter().enumerate().map(|(i,p)|
            self.choose(*p,times.as_ref().map(|t| t[i]),max_gap,cross_tol,depth_tol,global.as_ref())
        ).collect::<Vec<_>>());
        let mut spectra=Vec::with_capacity(rows.len()*self.groups.len());
        let mut valid=Vec::with_capacity(rows.len());
        let mut source=Vec::with_capacity(rows.len());
        let mut line=Vec::with_capacity(rows.len());
        let mut sample=Vec::with_capacity(rows.len());
        let mut angle=Vec::with_capacity(rows.len());
        let n=rows.len();
        for row in rows {
            spectra.extend(row.spectra);
            valid.push(row.valid); source.push(row.source); line.push(row.line);
            sample.push(row.sample); angle.push(row.angle);
        }
        let spectra=Array2::from_shape_vec((n,self.groups.len()),spectra)
            .map_err(|e| bad(e.to_string()))?;
        Ok((spectra.into_pyarray(py),valid.into_pyarray(py),source.into_pyarray(py),
            line.into_pyarray(py),sample.into_pyarray(py),angle.into_pyarray(py)))
    }
    #[pyo3(signature = (xyz, times, max_gap, cross_tol, occlusion_radius, depth_tol))]
    #[allow(clippy::too_many_arguments)]
    fn accumulate_spooled<'py>(&self,py:Python<'py>,xyz:PyReadonlyArray2<'_,f64>,
        times:Option<PyReadonlyArray1<'_,f64>>,max_gap:f64,cross_tol:f64,
        occlusion_radius:f64,depth_tol:f64)->PyResult<(&'py PyBytes,HashMap<String,f64>)> {
        validate(max_gap,cross_tol,occlusion_radius,depth_tol)?;
        let began=std::time::Instant::now();
        let points=point_rows(xyz)?;
        let times=time_rows(times,points.len())?;
        let search=std::time::Instant::now();
        let lists:Vec<Vec<Candidate>>=py.allow_threads(||
            points.par_iter().enumerate().map(|(i,p)|
                self.list(*p,times.as_ref().map(|v|v[i]),max_gap,cross_tol)
            ).collect());
        let search_s=search.elapsed().as_secs_f64();
        let compact=std::time::Instant::now();
        let n=u32::try_from(points.len()).map_err(|_|bad("Too many points per spool chunk"))?;
        let candidate_count:usize=lists.iter().map(|list|list.len()).sum();
        let bytes_needed=4usize.checked_add(points.len().checked_mul(4)
            .ok_or_else(||bad("Candidate spool overflow"))?)
            .and_then(|v|candidate_count.checked_mul(RECORD_BYTES).and_then(|b|v.checked_add(b)))
            .ok_or_else(||bad("Candidate spool overflow"))?;
        if bytes_needed>512*1024*1024 {return Err(bad("Candidate spool chunk exceeds 512 MiB: reduce point_chunk"));}
        let mut bytes=Vec::with_capacity(bytes_needed);
        bytes.extend_from_slice(&n.to_le_bytes());
        let mut local:HashMap<Cell,f64>=HashMap::new();
        for list in &lists {
            bytes.extend_from_slice(&u32::try_from(list.len())
                .map_err(|_|bad("Too many candidates in a row"))?.to_le_bytes());
            for c in list {
                store_candidate(&mut bytes,c)?;
                let v=local.entry((c.swath,c.line,c.sample)).or_insert(c.depth);
                *v=(*v).min(c.depth);
            }
        }
        let compact_s=compact.elapsed().as_secs_f64();
        let merge=std::time::Instant::now();
        let mut global=self.min_depth.lock().map_err(|_|bad("Depth lock poisoned"))?;
        let target=Arc::make_mut(&mut *global);
        for (key,depth) in local {
            let v=target.entry(key).or_insert(depth);
            *v=(*v).min(depth);
        }
        let global_count=target.len();
        drop(global);
        let merge_s=merge.elapsed().as_secs_f64();
        let output=PyBytes::new(py,&bytes);
        let mut profile=HashMap::new();
        profile.insert("candidate_search_s".into(),search_s);
        profile.insert("serialization_s".into(),compact_s);
        profile.insert("depth_merge_s".into(),merge_s);
        profile.insert("candidates".into(),candidate_count as f64);
        profile.insert("bytes".into(),bytes.len() as f64);
        profile.insert("global_cells".into(),global_count as f64);
        profile.insert("rust_total_s".into(),began.elapsed().as_secs_f64());
        Ok((output,profile))
    }
    #[pyo3(signature = (data, expected_rows, depth_tol))]
    fn paint_spooled<'py>(&self,py:Python<'py>,data:&PyBytes,expected_rows:usize,depth_tol:f64)
        ->PyResult<(&'py PyArray2<f32>,&'py PyArray1<u8>,&'py PyArray1<i32>,
                   &'py PyArray1<i32>,&'py PyArray1<i32>,&'py PyArray1<f32>,
                   HashMap<String,f64>)> {
        if !depth_tol.is_finite() || depth_tol<=0.0 {return Err(bad("Invalid depth tolerance"));}
        let began=std::time::Instant::now();
        let buffer=data.as_bytes().to_vec();
        let spans=validate_spool(&buffer,expected_rows,&self.swaths)?;
        let validation_s=began.elapsed().as_secs_f64();
        let global=Arc::clone(&*self.min_depth.lock().map_err(|_|bad("Depth lock poisoned"))?);
        let select=std::time::Instant::now();
        let selected:Vec<Option<Candidate>>=py.allow_threads(||
            spans.par_iter().map(|(first,last)|
                buffer[*first..*last].chunks_exact(RECORD_BYTES)
                    .map(decode_candidate)
                    .filter(|c|global.get(&(c.swath,c.line,c.sample))
                        .is_some_and(|min|c.depth<=min+depth_tol))
                    .min_by(|a,b|a.angle.total_cmp(&b.angle).then(a.error.total_cmp(&b.error))
                        .then(a.swath.cmp(&b.swath)).then(a.line.cmp(&b.line))
                        .then(a.sample.cmp(&b.sample)))
            ).collect());
        let selection_s=select.elapsed().as_secs_f64();
        let allocate=std::time::Instant::now();
        let bands=self.groups.len();
        let length=expected_rows.checked_mul(bands).ok_or_else(||bad("Spectrum allocation overflow"))?;
        let mut spectra=vec![f32::NAN;length];
        let mut metadata=vec![(0u8,-1i32,-1i32,-1i32,f32::NAN);expected_rows];
        let allocation_s=allocate.elapsed().as_secs_f64();
        let load=std::time::Instant::now();
        py.allow_threads(|| {
            spectra.par_chunks_mut(bands).zip(metadata.par_iter_mut())
                .zip(selected.par_iter()).for_each(|((row,meta),winner)| {
                    let Some(c)=winner else {return;};
                    let sw=&self.swaths[c.swath];
                    let mut finite=true;
                    for (b,v) in row.iter_mut().enumerate() {
                        *v=self.group_value(sw,c.line,c.sample,b);
                        finite &= v.is_finite();
                    }
                    if finite {
                        *meta=(1,sw.index,c.line as i32,c.sample as i32,c.angle.to_degrees() as f32);
                    } else {row.fill(f32::NAN);}
                });
        });
        let spectral_s=load.elapsed().as_secs_f64();
        let mut valid=Vec::with_capacity(expected_rows);
        let mut source=Vec::with_capacity(expected_rows);
        let mut lines=Vec::with_capacity(expected_rows);
        let mut samples=Vec::with_capacity(expected_rows);
        let mut angles=Vec::with_capacity(expected_rows);
        for (v,s,l,p,a) in metadata {
            valid.push(v);source.push(s);lines.push(l);samples.push(p);angles.push(a);
        }
        let arr=Array2::from_shape_vec((expected_rows,bands),spectra)
            .map_err(|e|bad(e.to_string()))?;
        let result=(arr.into_pyarray(py),valid.into_pyarray(py),source.into_pyarray(py),
            lines.into_pyarray(py),samples.into_pyarray(py),angles.into_pyarray(py));
        let mut profile=HashMap::new();
        profile.insert("spool_validate_copy_s".into(),validation_s);
        profile.insert("candidate_selection_s".into(),selection_s);
        profile.insert("output_allocation_s".into(),allocation_s);
        profile.insert("spectrum_read_s".into(),spectral_s);
        profile.insert("rust_total_s".into(),began.elapsed().as_secs_f64());
        Ok((result.0,result.1,result.2,result.3,result.4,result.5,profile))
    }
}
