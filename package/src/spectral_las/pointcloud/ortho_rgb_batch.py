from __future__ import annotations
from collections import OrderedDict, defaultdict
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import shutil
import statistics
import time
import traceback
import uuid

ALGORITHM = "paired-ortho-rgb-v1-direct-uint16"
_EVENTS = None

@dataclass(frozen=True)
class TilePair:
    tile_id: str
    lidar_acquisition: str
    ortho_acquisition: str
    source: str
    ortho: str
    output: str

def _positive_int(value, name):
    if isinstance(value,bool) or not isinstance(value,int) or value<1:
        raise ValueError(f"{name} must be a positive integer")

def _filename_parts(name,prefix,digits):
    for kind,suffixes in (("lidar",(".copc_laz",".copc.laz",".laz",".las")),("ortho",(".tiff",".tif"))):
        for suffix in suffixes:
            if name.lower().endswith(suffix):
                match=re.fullmatch(re.escape(prefix)+r"([0-9]{"+str(digits)+r"})(.+)",name[:-len(suffix)])
                if not match: raise ValueError(f"Malformed input filename: {name}")
                return kind,match.group(1),match.group(2)
    return None

def scan_inputs(working_directory,output_subfolder="rgb",prefix="1km",tile_id_digits=11,
                lidar_acquisition=None,ortho_acquisition=None):
    _positive_int(tile_id_digits,"tile_id_digits")
    root=Path(working_directory).resolve();folder=Path(output_subfolder)
    if not root.is_dir(): raise NotADirectoryError(root)
    if folder.is_absolute() or len(folder.parts)!=1 or folder.name in {".",".."}:
        raise ValueError("output_subfolder must be a single subfolder name")
    if not prefix: raise ValueError("prefix cannot be empty")
    index={};issues=[];ignored=0
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_file(): continue
            row=dict(filename=entry.name,full_path=str(Path(entry.path).resolve()),tile_id="",input_type="",acquisition_id="",reason="")
            try: parsed=_filename_parts(entry.name,prefix,tile_id_digits)
            except ValueError:
                row["reason"]="malformed_filename";issues.append(row);continue
            if parsed is None: ignored+=1;continue
            kind,tile,acq=parsed;row.update(tile_id=tile,input_type=kind,acquisition_id=acq)
            selected=lidar_acquisition if kind=="lidar" else ortho_acquisition
            if selected is not None and acq!=selected:
                row["reason"]="excluded_by_acquisition_filter";issues.append(row);continue
            index.setdefault(tile,{"lidar":[],"ortho":[]})[kind].append(row)
    return root,root/folder,index,issues,ignored

def discover_pairs(working_directory,output_subfolder="rgb",prefix="1km",tile_id_digits=11,
                   lidar_acquisition=None,ortho_acquisition=None,skip_unpaired=False):
    root,output,index,issues,ignored=scan_inputs(working_directory,output_subfolder,prefix,tile_id_digits,lidar_acquisition,ortho_acquisition)
    errors=[r["filename"] for r in issues if r["reason"]=="malformed_filename"]
    pairs=[];unmatched=[]
    for tile,group in sorted(index.items()):
        if len(group["lidar"])>1 or len(group["ortho"])>1:
            errors.append(f"Ambiguous tile {tile}; specify acquisition filters");continue
        if not group["lidar"] or not group["ortho"]:
            unmatched.append(tile);continue
        l,o=group["lidar"][0],group["ortho"][0]
        name=f"{prefix}{tile}{l['acquisition_id']}-{o['acquisition_id']}.laz"
        if len(name+".rgb.json")>255: errors.append(f"Filename too long: {name}");continue
        pairs.append(TilePair(tile,l["acquisition_id"],o["acquisition_id"],l["full_path"],o["full_path"],str(output/name)))
    if unmatched and not skip_unpaired: errors.append(f"Unmatched tile IDs: {unmatched[:25]}")
    if errors: raise ValueError("Pair discovery failed:\n"+"\n".join(errors[:25]))
    if not pairs: raise ValueError("No complete matching pairs selected")
    if len({os.path.normcase(p.output) for p in pairs})!=len(pairs): raise ValueError("Output filename collision")
    return pairs,dict(pairs=len(pairs),unpaired_tile_ids=unmatched,ignored_files=ignored,
                      working_directory=str(root),output_directory=str(output))

def _file_signature(path):
    path=Path(path).resolve();s=path.stat()
    return dict(path=str(path),size=s.st_size,mtime_ns=s.st_mtime_ns)

def _fingerprint(pair,options):
    identity=dict(algorithm=ALGORITHM,source=_file_signature(pair.source),ortho=_file_signature(pair.ortho),
                  rgb_bands=list(options["rgb_bands"]),unmatched_rgb=list(options["unmatched_rgb"]),coverage_tolerance=options["coverage_tolerance"])
    return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),identity

def _write_json(path,value):
    with Path(path).open("x",encoding="utf-8") as f:
        json.dump(value,f,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())

def _publish(stage,target):
    if os.name=="nt": os.rename(stage,target)
    else: os.link(stage,target);os.unlink(stage)

@contextmanager
def _lock(path):
    path=Path(path)
    with path.open("x",encoding="utf-8") as f:
        json.dump(dict(pid=os.getpid(),created_utc=datetime.now(timezone.utc).isoformat()),f)
    try: yield
    finally: path.unlink(missing_ok=True)

@contextmanager
def _thread_environment(threads):
    keys=("RAYON_NUM_THREADS","OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS","GDAL_NUM_THREADS")
    previous={k:os.environ.get(k) for k in keys}
    try:
        for k in keys: os.environ[k]=str(threads)
        yield
    finally:
        for k,v in previous.items():
            if v is None: os.environ.pop(k,None)
            else: os.environ[k]=v

def _init_worker(events):
    global _EVENTS
    _EVENTS=events

def _emit(event):
    if _EVENTS is not None:
        try: _EVENTS.put_nowait(dict(event,pid=os.getpid()))
        except (queue.Full,OSError,ValueError): pass

def _resume_summary(pair,digest):
    output=Path(pair.output);marker=output.with_suffix(output.suffix+".rgb.json")
    if not output.exists() and not marker.exists(): return None
    if not output.is_file() or not marker.is_file(): raise FileExistsError(f"Incomplete output; move orphan aside: {output}")
    summary=json.loads(marker.read_text(encoding="utf-8"))
    if not summary.get("completed") or summary.get("fingerprint")!=digest: raise FileExistsError(f"Existing output does not match inputs/settings: {output}")
    if _file_signature(output)!=summary.get("output_signature"): raise FileExistsError(f"Existing output changed: {output}")
    import laspy
    with laspy.open(output,read_evlrs=False) as r:
        if r.header.point_count!=summary["output_points"] or not {"red","green","blue"}.issubset(r.header.point_format.dimension_names):
            raise ValueError(f"Resume header validation failed: {output}")
    return summary

# These are payload estimates, not a process-RSS limit. GDAL and point buffers
# have separate budgets. Full-image loading reads one native band at a time.
def _image_estimates(dataset,bands):
    import numpy as np
    pixels=int(dataset.width)*int(dataset.height)
    itemsize=max(np.dtype(dataset.dtypes[b-1]).itemsize for b in bands)
    retained=pixels*13
    return dict(retained_bytes=retained,estimated_load_peak_bytes=retained+pixels*(itemsize+1)+8*1024*1024)

def _select_image_mode(requested,estimate,budget):
    if requested not in {"auto","full","blocks"}: raise ValueError("image_loading must be auto, full or blocks")
    fits=estimate["estimated_load_peak_bytes"]<=budget
    if requested=="full" and not fits:
        raise MemoryError("Full-image estimated loading peak exceeds this worker's raster budget; "
                          "use auto/blocks, fewer workers or a larger raster_memory_budget_gib")
    return "full" if requested=="full" or (requested=="auto" and fits) else "blocks"

class _FullImage:
    def __init__(self,dataset,bands,estimate,budget):
        import numpy as np
        started=time.perf_counter()
        self.values=np.empty((3,dataset.height,dataset.width),dtype=np.float32)
        self.valid=np.ones((dataset.height,dataset.width),dtype=bool)
        row_step=max(1,min(dataset.height,1_048_576//max(1,dataset.width)))
        for channel,band in enumerate(bands):
            native=dataset.read(band,masked=True)

            self.values[channel]=np.ma.getdata(native)
            for first in range(0,dataset.height,row_step):
                last=min(dataset.height,first+row_step)
                self.valid[first:last] &= ~np.ma.getmaskarray(native[first:last])
                self.valid[first:last] &= np.isfinite(self.values[channel,first:last])
            del native
        self.metadata=dict(strategy="full",load_seconds=time.perf_counter()-started,
                           retained_bytes=int(self.values.nbytes+self.valid.nbytes),budget_bytes=budget,
                           estimated_load_peak_bytes=estimate["estimated_load_peak_bytes"])
    def stats(self): return dict(self.metadata)

class _PairBlockCache:
    def __init__(self,dataset,bands,budget,estimate):
        self.dataset,self.bands,self.budget=dataset,bands,budget
        self.items=OrderedDict();self.current=self.peak=self.hits=self.misses=self.evictions=0
        self.oversized=self.loaded=0;self.load_seconds=0.;self.estimate=estimate
        self.bh,self.bw=dataset.block_shapes[bands[0]-1]
    def get(self,br,bc):
        import numpy as np
        from rasterio.windows import Window
        key=(int(br),int(bc))
        if key in self.items:
            self.hits+=1;self.items.move_to_end(key);return self.items[key]
        self.misses+=1;t=time.perf_counter();ro,co=int(br)*self.bh,int(bc)*self.bw
        h,w=min(self.bh,self.dataset.height-ro),min(self.bw,self.dataset.width-co)
        data=self.dataset.read(self.bands,window=Window(co,ro,w,h),masked=True)
        values=np.ma.getdata(data).astype(np.float32,copy=False)
        valid=~np.ma.getmaskarray(data).any(axis=0);valid &= np.isfinite(values).all(axis=0)
        size=values.nbytes+valid.nbytes;self.loaded+=size;self.load_seconds+=time.perf_counter()-t
        if size>self.budget: self.oversized+=1;return values,valid
        while self.items and self.current+size>self.budget:
            _,old=self.items.popitem(last=False);self.current-=old[0].nbytes+old[1].nbytes;self.evictions+=1
        self.items[key]=(values,valid);self.current+=size;self.peak=max(self.peak,self.current)
        return values,valid
    def stats(self):
        requests=self.hits+self.misses
        return dict(strategy="blocks",max_bytes=self.budget,peak_bytes=self.peak,hits=self.hits,misses=self.misses,
                    evictions=self.evictions,hit_rate=self.hits/requests if requests else 0.,oversized_blocks=self.oversized,
                    decoded_bytes_loaded=self.loaded,load_seconds=self.load_seconds,
                    full_image_retained_estimate=self.estimate["retained_bytes"],
                    full_image_load_peak_estimate=self.estimate["estimated_load_peak_bytes"])

def _pixel_indices(dataset,x,y,timings):
    import numpy as np
    from rasterio.transform import xy
    t=time.perf_counter();cf,rf=xy(~dataset.transform,y,x,offset="ul")
    cf,rf=np.asarray(cf,dtype=np.float64),np.asarray(rf,dtype=np.float64)
    inside=np.isfinite(cf)&np.isfinite(rf)&(cf>=0)&(cf<dataset.width)&(rf>=0)&(rf<dataset.height)
    ids=np.flatnonzero(inside);rows=np.floor(rf[ids]).astype(np.int64);cols=np.floor(cf[ids]).astype(np.int64)
    timings["pixel_coordinates"]+=time.perf_counter()-t
    return ids,rows,cols

def _sample_image(dataset,source,x,y,unmatched,timings):
    import numpy as np
    colors=np.empty((len(x),3),dtype=np.uint16);colors[:]=unmatched
    ids,rows,cols=_pixel_indices(dataset,x,y,timings)
    if not len(ids): return colors,0
    if isinstance(source,_FullImage):
        t=time.perf_counter();good=source.valid[rows,cols];target=ids[good];rr,cc=rows[good],cols[good]
        for band in range(3): colors[target,band]=source.values[band,rr,cc]
        timings["pixel_gather_and_scatter"]+=time.perf_counter()-t
        return colors,len(target)
    t=time.perf_counter();nbc=(dataset.width+source.bw-1)//source.bw
    keys=(rows//source.bh)*nbc+cols//source.bw;order=np.argsort(keys,kind="stable")
    keys,ids,rows,cols=keys[order],ids[order],rows[order],cols[order]
    edges=np.r_[0,np.flatnonzero(np.diff(keys))+1,len(keys)]
    timings["block_grouping"]+=time.perf_counter()-t;matched=0
    for first,last in zip(edges[:-1],edges[1:]):
        br,bc=divmod(int(keys[first]),nbc);values,valid=source.get(br,bc);t=time.perf_counter()
        rr,cc=rows[first:last]-br*source.bh,cols[first:last]-bc*source.bw
        good=valid[rr,cc];target=ids[first:last][good];rr,cc=rr[good],cc[good]
        for band in range(3): colors[target,band]=values[band,rr,cc]
        matched+=len(target);timings["pixel_gather_and_scatter"]+=time.perf_counter()-t
    return colors,matched

def _paint_pair(pair,options,stage):
    import laspy
    import numpy as np
    import rasterio
    from pyproj import CRS
    from .operations import add_rgb_to_header
    started=time.perf_counter();available=laspy.LazBackend.detect_available()
    if not available: raise RuntimeError('Install LAZ support: pip install "laspy[lazrs]"')
    preferred=laspy.LazBackend.Lazrs if options["native_threads_per_worker"]==1 else laspy.LazBackend.LazrsParallel
    backend=preferred if preferred in available else available[0]
    timings=defaultdict(float);total=matched=chunks=0;last_event=started
    with rasterio.Env(GDAL_CACHEMAX=options["gdal_cache_mb"]*1024*1024,GDAL_NUM_THREADS=str(options["native_threads_per_worker"])):
        with rasterio.open(pair.ortho) as image,laspy.open(pair.source,laz_backend=backend) as reader:
            crs=reader.header.parse_crs()
            if crs is None or image.crs is None: raise ValueError("Both tiles need a CRS")
            crs=CRS.from_user_input(crs)
            if crs!=CRS.from_user_input(image.crs): raise ValueError("Paired CRSs differ; reproject explicitly")
            if not crs.is_projected or len(crs.axis_info)<2 or any(not math.isclose(a.unit_conversion_factor,1.) for a in crs.axis_info[:2]):
                raise ValueError("Paired tiles must use projected metre XY coordinates")
            count=int(reader.header.point_count)
            if count!=options["expected_points"][pair.tile_id]: raise ValueError("Input point count changed after preflight")
            mins,maxs=reader.header.mins,reader.header.maxs;left,bottom,right,top=image.bounds;tol=options["coverage_tolerance"]
            if not np.isfinite(np.r_[mins[:2],maxs[:2],image.bounds]).all(): raise ValueError("Nonfinite bounds")
            if mins[0]<left-tol or mins[1]<bottom-tol or maxs[0]>right+tol or maxs[1]>top+tol:
                raise ValueError("LiDAR bounds extend beyond matched raster")
            bands=tuple(b+1 for b in options["rgb_bands"])
            if max(bands)>image.count: raise ValueError("Requested RGB band does not exist")
            header=add_rgb_to_header(reader.header.copy())
            header.vlrs=[v for v in header.vlrs if str(getattr(v,"user_id","")).strip().lower()!="copc"]
            evlrs=header.evlrs
            if evlrs is not None:
                evlrs=type(evlrs)([v for v in evlrs if str(getattr(v,"user_id","")).strip().lower()!="copc"]);header.evlrs=evlrs
            timings["open_validate_header"]=time.perf_counter()-started
            estimate=_image_estimates(image,bands);budget=options["raster_budget_per_worker_bytes"]
            strategy=_select_image_mode(options["image_loading"],estimate,budget)
            _emit(dict(event="tile_start",tile_id=pair.tile_id,total=count,points=0,elapsed_seconds=0.,
                       image_strategy=strategy,estimated_raster_bytes=estimate["retained_bytes"]))
            t=time.perf_counter()
            if strategy=="full": source=_FullImage(image,bands,estimate,budget)
            else: source=_PairBlockCache(image,bands,min(int(options["block_cache_mb"]*1024*1024),budget),estimate)
            timings["image_prepare"]=time.perf_counter()-t
            _emit(dict(event="image_ready",tile_id=pair.tile_id,total=count,points=0,
                       elapsed_seconds=time.perf_counter()-started,image_strategy=strategy,
                       image_load_seconds=timings["image_prepare"]))
            with laspy.open(stage,mode="w",header=header,do_compress=True,laz_backend=backend) as writer:
                while total<count:
                    chunk_start=t=time.perf_counter();points=reader.read_points(min(options["point_chunk"],count-total))
                    read_s=time.perf_counter()-t;timings["point_read"]+=read_s
                    if not len(points): raise RuntimeError("Truncated point stream")
                    t=time.perf_counter();x,y=np.asarray(points.x,dtype=np.float64),np.asarray(points.y,dtype=np.float64)
                    if not (np.isfinite(x).all() and np.isfinite(y).all()): raise ValueError("Nonfinite point XY")
                    timings["point_xy_extract"]+=time.perf_counter()-t;t=time.perf_counter()
                    colors,matches=_sample_image(image,source,x,y,options["unmatched_rgb"],timings)
                    paint_s=time.perf_counter()-t;timings["sampling_total"]+=paint_s;t=time.perf_counter()
                    output=laspy.ScaleAwarePointRecord.zeros(len(points),header=header)
                    for name in points.point_format.dimension_names: output[name]=points[name]
                    output.red,output.green,output.blue=colors[:,0],colors[:,1],colors[:,2]
                    copy_s=time.perf_counter()-t;timings["point_record_copy"]+=copy_s;t=time.perf_counter()
                    writer.write_points(output);write_s=time.perf_counter()-t;timings["point_write"]+=write_s
                    total+=len(points);matched+=matches;chunks+=1;now=time.perf_counter()
                    if now-last_event>=options["progress_interval_seconds"] or total==count:
                        event=dict(event="tile_progress",tile_id=pair.tile_id,chunk=chunks,points=total,total=count,
                                   matched=matched,elapsed_seconds=now-started,image_strategy=strategy,
                                   read_seconds=read_s,paint_seconds=paint_s,copy_seconds=copy_s,write_seconds=write_s)
                        if strategy=="blocks": event.update(cache_hits=source.hits,cache_misses=source.misses)
                        _emit(event);last_event=now
                    del points,output,x,y,colors
                if evlrs:
                    t=time.perf_counter();writer.write_evlrs(evlrs);timings["evlr_write"]+=time.perf_counter()-t
                t=time.perf_counter()
            timings["writer_finalize"]+=time.perf_counter()-t
            raster_stats=source.stats()
    return dict(input_points=count,output_points=total,rgb_matched_points=matched,rgb_unmatched_points=total-matched,
                point_chunks=chunks,laz_backend=str(backend),stage_timings_seconds=dict(timings),raster=raster_stats,
                block_cache=raster_stats if strategy=="blocks" else None,elapsed_seconds=time.perf_counter()-started,
                output_is_copc=False,output_is_compressed=True,rgb_encoding="float32 values assigned to uint16; no stretching")

def _process_pair(pair,options):
    started=time.perf_counter();work=None
    try:
        digest,identity=_fingerprint(pair,options)
        if options["resume"]:
            old=_resume_summary(pair,digest)
            if old is not None:
                return dict(status="skipped",tile_id=pair.tile_id,output=pair.output,points=old["output_points"],seconds=time.perf_counter()-started)
        output=Path(pair.output);marker=output.with_suffix(output.suffix+".rgb.json")
        if output.exists() or marker.exists(): raise FileExistsError(f"Refusing overwrite: {output}")
        work=output.parent/".rgb-work"/uuid.uuid4().hex;work.mkdir(parents=True)
        stage=work/"tile.laz";summary=_paint_pair(pair,options,stage)
        if _fingerprint(pair,options)[0]!=digest: raise RuntimeError("Input changed during processing")
        summary.update(schema_version=2,operation="paint_rgb_tile_pair",algorithm=ALGORITHM,completed=True,
                       fingerprint=digest,identity=identity,**asdict(pair))
        _publish(stage,output);summary["output_signature"]=_file_signature(output)
        sm=work/"tile.rgb.json";_write_json(sm,summary);_publish(sm,marker)
        return dict(status="completed",tile_id=pair.tile_id,output=pair.output,points=summary["output_points"],
                    matched_points=summary["rgb_matched_points"],seconds=time.perf_counter()-started,
                    stage_timings_seconds=summary["stage_timings_seconds"],raster=summary["raster"])
    except Exception as error:
        return dict(status="failed",tile_id=pair.tile_id,output=pair.output,source=pair.source,ortho=pair.ortho,
                    seconds=time.perf_counter()-started,error=f"{type(error).__name__}: {error}",traceback=traceback.format_exc())
    finally:
        if work is not None: shutil.rmtree(work,ignore_errors=True)

# Snapshots contain cumulative counters, never deltas. Terminal results replace
# snapshots, and delayed telemetry for terminal jobs is ignored by the parent.
def _progress_metrics(planned,states,results,elapsed,workers):
    published=sum(r["points"] for r in results.values() if r["status"]=="completed")
    skipped=sum(r["points"] for r in results.values() if r["status"]=="skipped")
    active={k:s for k,s in states.items() if k not in results}
    active_points=sum(min(planned[k],max(0,int(s.get("points",0)))) for k,s in active.items())
    painted=published+active_points
    rates=[s["points"]/s["elapsed_seconds"] for s in active.values()
           if s.get("points",0)>0 and s.get("elapsed_seconds",0)>0]
    rates += [r["points"]/r["seconds"] for r in results.values()
              if r["status"]=="completed" and r.get("seconds",0)>0]
    eta=None
    if rates and not any(r["status"]=="failed" for r in results.values()):
        typical=statistics.median(rates)
        loads=[]
        for k,s in active.items():
            rate=s.get("points",0)/max(s.get("elapsed_seconds",0),1e-9) if s.get("points",0)>0 else typical
            loads.append(max(0,planned[k]-s.get("points",0))/max(rate,1e-9))

        loads += [0.]*max(0,workers-len(loads))
        if not loads: loads=[0.]
        heapq.heapify(loads)
        queued=sorted((n for k,n in planned.items() if k not in results and k not in active),reverse=True)
        for n in queued: heapq.heapreplace(loads,loads[0]+n/typical)
        eta=max(loads)
    return dict(total_points=sum(planned.values()),painted_new_points=painted,published_new_points=published,
                skipped_points=skipped,progress_points=painted+skipped,active_tiles=len(active),
                throughput=painted/max(elapsed,1e-9),paint_eta_seconds=eta)

def _accept_event(event,states,results):
    tile=event["tile_id"]
    if tile in results: return False
    old=states.get(tile,{})
    if int(event.get("points",0))<int(old.get("points",0)): return False
    states[tile]=dict(old,**event)
    return True

def _drain_events(events,log,states,results,live_progress):
    received=0
    while True:
        try: event=events.get_nowait()
        except queue.Empty: break
        if not _accept_event(event,states,results): continue
        received+=1;log.write(json.dumps(event)+"\n")
        if not live_progress: continue
        tile=event["tile_id"];kind=event["event"]
        if kind=="tile_start":
            print(f"START tile {tile} | {event['total']:,} points | image={event['image_strategy']} "
                  f"retained estimate={event['estimated_raster_bytes']/1024**3:.2f}GiB",flush=True)
        elif kind=="image_ready":
            print(f"READY tile {tile} | image={event['image_strategy']} | prepare={event['image_load_seconds']:.2f}s",flush=True)
        else:
            rate=event["points"]/max(event["elapsed_seconds"],1e-9)
            detail=(f"cache={event['cache_hits']:,} hits/{event['cache_misses']:,} misses" if event["image_strategy"]=="blocks" else "image=full (no block-cache lookup)")
            print(f"tile {tile} chunk {event['chunk']}: {event['points']:,}/{event['total']:,} "
                  f"({100*event['points']/event['total']:.1f}%) | matched={event['matched']:,} | "
                  f"read={event['read_seconds']:.3f}s paint={event['paint_seconds']:.3f}s "
                  f"copy={event['copy_seconds']:.3f}s write={event['write_seconds']:.3f}s | "
                  f"{detail} | {rate:,.0f} pts/s",flush=True)
    if received: log.flush()
    return received

def _show_overall(planned,states,results,elapsed,workers):
    m=_progress_metrics(planned,states,results,elapsed,workers)
    eta="warming up/unknown" if m["paint_eta_seconds"] is None else f"~{m['paint_eta_seconds']:.1f}s"
    print(f"PAINT {m['progress_points']:,}/{m['total_points']:,} points "
          f"({100*m['progress_points']/max(m['total_points'],1):.1f}%) | new={m['painted_new_points']:,} "
          f"published={m['published_new_points']:,} resumed={m['skipped_points']:,} | "
          f"{m['throughput']:,.0f} observed new pts/s | tiles {len(results)}/{len(planned)} finished | "
          f"paint ETA={eta} (merge excluded)",flush=True)
    return m

def run_rgb_tile_batch(working_directory,output_subfolder="rgb",prefix="1km",tile_id_digits=11,
                      lidar_acquisition=None,ortho_acquisition=None,workers=8,native_threads_per_worker=1,
                      point_chunk=500_000,block_cache_mb=256.,gdal_cache_mb=64,rgb_bands=(0,1,2),
                      unmatched_rgb=(0,0,0),coverage_tolerance=.01,resume=True,skip_unpaired=False,
                      limit_pairs=None,dry_run=False,log_every_tiles=1,detailed_tile_logging=True,
                      live_progress=True,progress_interval_seconds=5.,heartbeat_seconds=10.,
                      merge_outputs=False,merged_output_name="merged_pcd_rgb.laz",merge_chunk_points=2_000_000,
                      merge_native_threads=2,image_loading="auto",raster_memory_budget_gib=48.):
    for name,value in (("workers",workers),("native_threads_per_worker",native_threads_per_worker),
                       ("point_chunk",point_chunk),("gdal_cache_mb",gdal_cache_mb),("log_every_tiles",log_every_tiles),
                       ("merge_chunk_points",merge_chunk_points),("merge_native_threads",merge_native_threads)):
        _positive_int(value,name)
    for name,value in (("block_cache_mb",block_cache_mb),("progress_interval_seconds",progress_interval_seconds),
                       ("heartbeat_seconds",heartbeat_seconds),("raster_memory_budget_gib",raster_memory_budget_gib)):
        if not math.isfinite(value) or value<=0: raise ValueError(f"{name} must be positive and finite")
    if image_loading not in {"auto","full","blocks"}: raise ValueError("image_loading must be auto, full or blocks")
    if int(block_cache_mb*1024**2)<1: raise ValueError("Cache budget is too small")
    if not math.isfinite(coverage_tolerance) or coverage_tolerance<0: raise ValueError("Invalid coverage_tolerance")
    if len(rgb_bands)!=3 or len(set(rgb_bands))!=3 or any(type(v) is not int or v<0 for v in rgb_bands): raise ValueError("Invalid rgb_bands")
    if len(unmatched_rgb)!=3 or any(type(v) is not int or not 0<=v<=65535 for v in unmatched_rgb): raise ValueError("Invalid unmatched_rgb")
    if limit_pairs is not None: _positive_int(limit_pairs,"limit_pairs")
    name=merged_output_name or "merged_pcd_rgb.laz"
    if Path(name).name!=name or not name.lower().endswith(".laz"): raise ValueError("merged_output_name must be one .laz filename")
    started=time.perf_counter()
    pairs,discovery=discover_pairs(working_directory,output_subfolder,prefix,tile_id_digits,lidar_acquisition,ortho_acquisition,skip_unpaired)
    if limit_pairs is not None: pairs=pairs[:limit_pairs]
    merge_paths=[p.output for p in sorted(pairs,key=lambda p:p.tile_id)]
    if str(Path(discovery["output_directory"])/name) in merge_paths: raise ValueError("Merged name conflicts with a tile")
    workers=min(workers,len(pairs))
    if os.name=="nt" and workers>61: raise ValueError("Windows supports at most 61 ProcessPoolExecutor workers")
    budget=int(raster_memory_budget_gib*1024**3)//workers
    if budget<1: raise ValueError("Raster budget too small")
    print(f"Discovered {discovery['pairs']:,} pairs; selected {len(pairs):,}; workers={workers}; "
          f"native_threads/worker={native_threads_per_worker}; image_loading={image_loading}; "
          f"raster payload/load-estimate share={budget/1024**3:.2f}GiB/worker",flush=True)
    if dry_run:
        for p in pairs[:10]: print(f"{Path(p.source).name} + {Path(p.ortho).name} -> {Path(p.output).name}")
        return dict(dry_run=True,selected_pairs=len(pairs),discovery=discovery)
    import laspy
    planned={}
    print("Reading point-count headers for workload-aware progress...",flush=True)
    for i,p in enumerate(pairs):
        with laspy.open(p.source,read_evlrs=False) as reader: planned[p.tile_id]=int(reader.header.point_count)
        if planned[p.tile_id]<1: raise ValueError(f"Empty point-cloud tile: {p.source}")
        if (i+1)%1000==0: print(f"Point-count preflight {i+1:,}/{len(pairs):,}",flush=True)
    preflight_seconds=time.perf_counter()-started
    print(f"Planned points: {sum(planned.values()):,}; header/discovery preflight={preflight_seconds:.2f}s",flush=True)
    pairs.sort(key=lambda p:planned[p.tile_id],reverse=True)
    output=Path(discovery["output_directory"]);output.mkdir(exist_ok=True)
    options=dict(native_threads_per_worker=native_threads_per_worker,point_chunk=point_chunk,block_cache_mb=float(block_cache_mb),
                 gdal_cache_mb=gdal_cache_mb,rgb_bands=tuple(rgb_bands),unmatched_rgb=tuple(unmatched_rgb),
                 coverage_tolerance=coverage_tolerance,resume=bool(resume),progress_interval_seconds=progress_interval_seconds,
                 image_loading=image_loading,raster_budget_per_worker_bytes=budget,expected_points=planned)
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    journal=output/f"batch-{run_id}.jsonl";results={};states={};rollup=defaultdict(float);strategy_counts=defaultdict(int)
    cache_hits=cache_misses=0
    with _lock(output/".rgb-batch.lock"),journal.open("x",encoding="utf-8") as log:
        log.write(json.dumps(dict(event="start",discovery=discovery,selected_pairs=len(pairs),workers=workers,
                                  total_points=sum(planned.values()),image_loading=image_loading,raster_budget_per_worker_bytes=budget))+"\n");log.flush()
        context=mp.get_context("spawn");events=context.Queue(maxsize=max(128,workers*8))
        paint_started=last_overall=last_activity=time.perf_counter()
        try:
            with _thread_environment(native_threads_per_worker):
                with ProcessPoolExecutor(max_workers=workers,mp_context=context,initializer=_init_worker,initargs=(events,)) as executor:
                    todo=iter(pairs);pending={}
                    def refill():
                        while len(pending)<2*workers:
                            pair=next(todo,None)
                            if pair is None: break

                            job_options=dict(options,expected_points={pair.tile_id:planned[pair.tile_id]})
                            pending[executor.submit(_process_pair,pair,job_options)]=pair
                    refill()
                    while pending:
                        done,_=wait(pending,timeout=.5,return_when=FIRST_COMPLETED)
                        if _drain_events(events,log,states,results,live_progress): last_activity=time.perf_counter()
                        for future in done:
                            pair=pending.pop(future)
                            try: result=future.result()
                            except Exception as error:
                                log.write(json.dumps(dict(event="pool_error",tile_id=pair.tile_id,error=repr(error)))+"\n");log.flush();raise
                            results[pair.tile_id]=result;states.pop(pair.tile_id,None);status=result["status"]
                            log.write(json.dumps(result)+"\n");log.flush();last_activity=time.perf_counter()
                            if status=="completed":
                                for k,v in result["stage_timings_seconds"].items(): rollup[k]+=v
                                raster=result["raster"];strategy_counts[raster["strategy"]]+=1
                                if raster["strategy"]=="blocks": cache_hits+=raster["hits"];cache_misses+=raster["misses"]
                            if status=="failed": print(f"FAILED tile {pair.tile_id}: {result['error']}",flush=True)
                            elif detailed_tile_logging:
                                if status=="skipped": print(f"[{len(results)}/{len(pairs)}] SKIPPED tile {pair.tile_id} | {result['points']:,} points",flush=True)
                                else:
                                    st=result["stage_timings_seconds"];raster=result["raster"];sec=result["seconds"]
                                    detail=(f"full image={raster['retained_bytes']/1024**3:.2f}GiB load={raster['load_seconds']:.2f}s"
                                            if raster["strategy"]=="full" else f"block cache={100*raster['hit_rate']:.1f}%")
                                    print(f"[{len(results)}/{len(pairs)}] DONE tile {pair.tile_id} | {result['points']:,} pts "
                                          f"matched={result['matched_points']:,} | {sec:.2f}s | "
                                          f"read={st.get('point_read',0):.2f}s sample={st.get('sampling_total',0):.2f}s "
                                          f"copy={st.get('point_record_copy',0):.2f}s write={st.get('point_write',0):.2f}s | {detail}",flush=True)
                            if len(results)%log_every_tiles==0 or len(results)==len(pairs):
                                _show_overall(planned,states,results,time.perf_counter()-paint_started,workers);last_overall=time.perf_counter()
                        refill();now=time.perf_counter()
                        if live_progress and now-last_overall>=progress_interval_seconds:
                            _show_overall(planned,states,results,now-paint_started,workers);last_overall=now
                        if live_progress and now-last_activity>=heartbeat_seconds:
                            print(f"HEARTBEAT: {len(results)}/{len(pairs)} jobs finalized; loading/sampling/writing may still be active",flush=True)
                            last_activity=now
            _drain_events(events,log,states,results,False)
        finally:
            events.close();events.join_thread()
        paint_seconds=time.perf_counter()-paint_started
        counts={s:sum(r["status"]==s for r in results.values()) for s in ("completed","skipped","failed")}
        final_metrics=_progress_metrics(planned,states,results,paint_seconds,workers)
        report=dict(run_id=run_id,selected_pairs=len(pairs),**counts,planned_points=sum(planned.values()),
                    new_output_points=final_metrics["published_new_points"],resumed_output_points=final_metrics["skipped_points"],
                    preflight_seconds=preflight_seconds,painting_elapsed_seconds=paint_seconds,workers=workers,journal=str(journal),
                    discovery=discovery,stage_timings_worker_seconds=dict(rollup),image_strategy_counts=dict(strategy_counts),
                    cache_hits=cache_hits,cache_misses=cache_misses,
                    timing_note="Worker wall-duration sums, not CPU seconds; sampling_total includes substages; image_prepare is separate")
        print(f"PAINT COMPLETE: {counts} | new={report['new_output_points']:,}, resumed={report['resumed_output_points']:,} | "
              f"{paint_seconds:.2f}s | {report['new_output_points']/max(paint_seconds,1e-9):,.0f} new pts/s",flush=True)
        print(f"Worker stage sums (nested; NOT CPU seconds): {dict(rollup)}",flush=True)
        print(f"Image strategies for newly painted tiles: {dict(strategy_counts)}; block-only cache {cache_hits:,} hits/{cache_misses:,} misses",flush=True)
        if merge_outputs and counts["failed"]==0:
            from .ortho_rgb_merge import merge_rgb_laz_tiles
            with _thread_environment(merge_native_threads):
                report["merged_output"]=merge_rgb_laz_tiles(merge_paths,output/name,merge_chunk_points,merge_native_threads,resume=resume)
        elif merge_outputs: report["merge_skipped_reason"]="One or more selected tiles failed"
        report["elapsed_seconds"]=time.perf_counter()-started
        log.write(json.dumps(dict(event="finish",**report))+"\n");log.flush();os.fsync(log.fileno())
        _write_json(output/f"batch-{run_id}.json",report)
    print(f"Batch finished: {counts}; journal: {journal}",flush=True)
    return report
