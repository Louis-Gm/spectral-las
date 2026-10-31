use nalgebra::{Matrix3, Point3, Vector3};
use rayon::prelude::*;
/// Represents the physical orientation and position of the drone chassis at a specific GPS epoch.
#[derive(Clone, Copy, Debug)]
pub struct ExteriorOrientation {
    pub gps_seconds_of_week: f64,
    pub easting: f64,
    pub northing: f64,
    pub altitude: f64,
    pub roll: f64,
    pub pitch: f64,
    pub yaw: f64,
}
/// Represents the optical calibration parameters and mechanical offsets of the sensor payload.
#[derive(Clone, Copy, Debug)]
pub struct InteriorOrientation {
    pub field_of_view_rad: f64,
    pub spatial_pixels: usize,
    pub boresight_roll: f64,
    pub boresight_pitch: f64,
    pub boresight_yaw: f64,
    pub lever_arm_x: f64,
    pub lever_arm_y: f64,
    pub lever_arm_z: f64,
}
/// Stores the fully resolved geographic footprint and quality metrics of a single raw camera pixel.
#[derive(Clone, Copy, Debug)]
pub struct ProjectedPixel {
    pub line_idx: usize,
    pub sample_idx: usize,
    pub utm_x: f64,
    pub utm_y: f64,
    pub nadir_distance: f64,
}
/// Linearly interpolates the drone's exterior orientation between two bounding trajectory records.
pub fn interpolate_eo(time: f64, before: &ExteriorOrientation, after: &ExteriorOrientation) -> ExteriorOrientation {
    let t = (time - before.gps_seconds_of_week) / (after.gps_seconds_of_week - before.gps_seconds_of_week);
    ExteriorOrientation {
        gps_seconds_of_week: time,
        easting: before.easting + t * (after.easting - before.easting),
        northing: before.northing + t * (after.northing - before.northing),
        altitude: before.altitude + t * (after.altitude - before.altitude),
        roll: before.roll + t * (after.roll - before.roll),
        pitch: before.pitch + t * (after.pitch - before.pitch),
        yaw: before.yaw + t * (after.yaw - before.yaw),
    }
}
/// Computes the absolute 3D rotation matrix combining chassis orientation and mechanical boresight offsets.
pub fn calculate_rotation_matrix(eo: &ExteriorOrientation, io: &InteriorOrientation) -> Matrix3<f64> {
    let r = eo.roll + io.boresight_roll;
    let p = eo.pitch + io.boresight_pitch;
    let h = eo.yaw + io.boresight_yaw;
    let rx = Matrix3::new(
        1.0, 0.0, 0.0,
        0.0, p.cos(), -p.sin(),
        0.0, p.sin(), p.cos()
    );
    let ry = Matrix3::new(
        r.cos(), 0.0, r.sin(),
        0.0, 1.0, 0.0,
        -r.sin(), 0.0, r.cos()
    );
    let ch = (-h).cos();
    let sh = (-h).sin();
    let rz = Matrix3::new(
        ch, -sh, 0.0,
        sh,  ch, 0.0,
        0.0, 0.0, 1.0
    );
    rz * rx * ry
}
/// Calculates the normalized internal directional vector of a specific pixel along the sensor array.
pub fn get_sensor_vector(sample_idx: usize, io: &InteriorOrientation) -> Vector3<f64> {
    let center = (io.spatial_pixels as f64 - 1.0) / 2.0;
    let ifov = io.field_of_view_rad / io.spatial_pixels as f64;
    let angle = (sample_idx as f64 - center) * ifov;
    Vector3::new(angle.sin(), 0.0, -angle.cos())
}
/// Steps a 3D ray through space until it mathematically collides with the Digital Surface Model.
pub fn intersect_ray_with_dsm(
    origin: &Point3<f64>,
    direction: &Vector3<f64>,
    dsm: &[f64],
    dsm_cols: usize,
    dsm_rows: usize,
    dsm_min_x: f64,
    dsm_max_y: f64,
    dsm_res: f64
    ) -> Option<Point3<f64>> {
    let step = dsm_res * 0.5;
    let mut curr = *origin;
    for _ in 0..60_000 {
        curr += direction * step;
        let c = ((curr.x - dsm_min_x) / dsm_res).floor() as isize;
        let r = ((dsm_max_y - curr.y) / dsm_res).floor() as isize;
        if c >= 0 && c < dsm_cols as isize && r >= 0 && r < dsm_rows as isize {
            let z = dsm[(r as usize) * dsm_cols + (c as usize)];
            if !z.is_nan() && curr.z <= z {
                return Some(curr);
            }
        }
    }
    None
}
/// Orthorectifies an entire swath chunk by projecting sensor rays against a DSM and computing their Distance-to-Nadir quality metric.
pub fn orthorectify_swath_chunked(
    gps_times: &[f64],
    sbet: &[ExteriorOrientation],
    io: &InteriorOrientation,
    dsm: &[f64],
    dsm_cols: usize,
    dsm_rows: usize,
    dsm_min_x: f64,
    dsm_max_y: f64,
    chunk_min_x: f64,
    chunk_max_y: f64,
    chunk_cols: usize,
    chunk_rows: usize,
    dsm_res: f64,
    ortho_res: f64
) -> Vec<ProjectedPixel> {
    let chunk_max_x = chunk_min_x + (chunk_cols as f64 * ortho_res);
    let chunk_min_y = chunk_max_y - (chunk_rows as f64 * ortho_res);
    gps_times.par_iter().enumerate().flat_map(|(l_idx, &t)| {
        let pos = sbet.binary_search_by(|probe| probe.gps_seconds_of_week.partial_cmp(&t).unwrap()).unwrap_or_else(|e| e);
        if pos == 0 || pos >= sbet.len() {
            return Vec::new();
        }
        let eo = interpolate_eo(t, &sbet[pos-1], &sbet[pos]);
        let safe_buffer = eo.altitude * 1.5;
        if eo.easting + safe_buffer < chunk_min_x || eo.easting - safe_buffer > chunk_max_x ||
           eo.northing + safe_buffer < chunk_min_y || eo.northing - safe_buffer > chunk_max_y {
            return Vec::new();
        }
        let rot = calculate_rotation_matrix(&eo, io);
        let lever_arm = Vector3::new(io.lever_arm_x, io.lever_arm_y, io.lever_arm_z);
        let rotated_lever_arm = rot * lever_arm;
        let center = Point3::new(
            eo.easting + rotated_lever_arm.x,
            eo.northing + rotated_lever_arm.y,
            eo.altitude + rotated_lever_arm.z
        );
        (0..io.spatial_pixels).filter_map(|s_idx| {
            let dir = rot * get_sensor_vector(s_idx, io);
            if let Some(p) = intersect_ray_with_dsm(&center, &dir, dsm, dsm_cols, dsm_rows, dsm_min_x, dsm_max_y, dsm_res) {
                if p.x >= chunk_min_x && p.x < chunk_max_x && p.y <= chunk_max_y && p.y > chunk_min_y {
                    let nadir_distance = ((p.x - center.x).powi(2) + (p.y - center.y).powi(2)).sqrt();
                    return Some(ProjectedPixel {
                        line_idx: l_idx,
                        sample_idx: s_idx,
                        utm_x: p.x,
                        utm_y: p.y,
                        nadir_distance
                    });
                }
            }
            None
        }).collect::<Vec<_>>()
    }).collect()
}
