from pathlib import Path
import json
import math
import shutil
import tempfile
import numpy as np
import laspy
import rasterio
from rasterio.windows import Window
from rasterio.transform import from_origin
from pyproj import CRS
from .io import resolve_path
from ..pointcloud.io import open_spectra
from ..config import FlightConfig

def _spectral_tags(dst, fields, metadata, reducer, source):
    dst.update_tags(reducer=reducer, source_pointcloud=str(source),
                    spectralpc_product="point_raster", missing_cells="nodata")
    if metadata:
        dst.update_tags(height_datum=metadata.get("las_height_datum", "unspecified"),
                        spectral_operation=metadata.get("spectral_operation", "unspecified"),
                        point_manifest_schema=str(metadata.get("schema_version", "unknown")))
    waves = metadata.get("wavelengths", []) if metadata else []
    pairs = metadata.get("source_band_pairs_zero_based", []) if metadata else []
    selected_waves = []
    for position, name in enumerate(fields, 1):
        dst.set_band_description(position, name)
        if name.startswith("hs:"):
            band = int(name[3:])
            if band < len(pairs):
                dst.update_tags(position, source_band_pair_zero_based=",".join(map(str, pairs[band])))
            if band < len(waves):
                dst.update_tags(position, wavelength=str(waves[band]),
                                wavelength_units=str(metadata.get("wavelength_units") or "unspecified"))
                selected_waves.append(waves[band])
    if len(selected_waves) == len(fields):
        dst.update_tags(ns="ENVI", wavelength="{"+",".join(map(str, selected_waves))+"}",
                        wavelength_units=str(metadata.get("wavelength_units") or "unspecified"))

def rasterize_pointcloud(source, output, *, resolution, fields=("z",), reducer="mean",
                         bounds=None, tile_size=256, point_chunk=25_000, select=None,
                         value_transform=None, scratch_directory=None, dtype="float32"):







    source, output = Path(source).resolve(), Path(output).resolve()
    fields = tuple(fields)
    data_type = np.dtype(dtype)
    if data_type not in {np.dtype("float32"), np.dtype("float64")}:
        raise ValueError("Raster dtype must be float32 or float64")
    if output.exists() or output == source:
        raise FileExistsError(output)
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("Resolution must be positive and finite")
    if not isinstance(tile_size, int) or not 1 <= tile_size <= 4096:
        raise ValueError("tile_size must be an integer from 1 to 4096")
    if not isinstance(point_chunk, int) or point_chunk < 1:
        raise ValueError("point_chunk must be a positive integer")
    if not fields or reducer not in {"mean", "min", "max", "count", "surface"}:
        raise ValueError("Provide fields and reducer mean/min/max/count/surface")
    with laspy.open(source) as reader:
        crs = reader.header.parse_crs()
        if crs is None:
            raise ValueError("LAS CRS missing")
        if bounds is None:
            bounds = (reader.header.mins[0], reader.header.mins[1],
                      reader.header.maxs[0], reader.header.maxs[1])
        available = set(reader.header.point_format.dimension_names) | {"x", "y", "z"}
        total_points = int(reader.header.point_count)
    if len(bounds) != 4:
        raise ValueError("bounds must contain min_x,min_y,max_x,max_y")
    min_x, min_y, max_x, max_y = map(float, bounds)
    if not np.isfinite(bounds).all() or min_x > max_x or min_y > max_y:
        raise ValueError("Invalid bounds")
    cols = max(1, math.ceil((max_x-min_x)/resolution))
    rows = max(1, math.ceil((max_y-min_y)/resolution))
    if cols >= 2**31 or rows >= 2**31:
        raise ValueError("Raster dimensions exceed supported GDAL grid range")
    tile_cols = math.ceil(cols/tile_size)
    spectra = None
    metadata = {}
    if any(name.startswith("hs:") for name in fields):
        spectra, metadata = open_spectra(source)
    for name in fields:
        if name.startswith("hs:"):
            if not 0 <= int(name[3:]) < spectra.shape[1]:
                raise ValueError(f"Invalid spectral field {name}")
        elif name not in available:
            raise ValueError(f"Missing LAS field {name}")
    profile = {"driver":"GTiff", "width":cols, "height":rows, "count":len(fields),
               "dtype":data_type.name, "crs":crs,
               "transform":from_origin(min_x,max_y,resolution,resolution),
               "nodata":float("nan"), "tiled":True, "blockxsize":256, "blockysize":256,
               "compress":"deflate", "BIGTIFF":"YES"}
    record_type = np.dtype([("cell","<u4"), ("z","<f8"), ("angle","<f4"),
                            ("id","<u8"), ("values",data_type.newbyteorder("<"),(len(fields),))])
    try:
        with tempfile.TemporaryDirectory(prefix="spectral_las_tiles_", dir=scratch_directory) as scratch:
            scratch = Path(scratch)
            offset = 0
            with laspy.open(source) as reader:
                for chunk_number, points in enumerate(reader.chunk_iterator(point_chunk), 1):
                    n = len(points)
                    x, y, z = np.asarray(points.x), np.asarray(points.y), np.asarray(points.z)
                    mask = np.isfinite(x) & np.isfinite(y)
                    if select is not None:
                        user_mask = np.asarray(select(points))
                        if user_mask.shape != (n,) or user_mask.dtype != np.bool_:
                            raise ValueError("select must return a matching boolean vector")
                        mask &= user_mask
                    mask &= (x >= min_x) & (x <= max_x) & (y >= min_y) & (y <= max_y)
                    c = np.minimum(np.floor(np.where(mask,(x-min_x)/resolution,0)).astype(np.int64), cols-1)
                    r = np.minimum(np.floor(np.where(mask,(max_y-y)/resolution,0)).astype(np.int64), rows-1)
                    values = np.empty((n,len(fields)), dtype=data_type)
                    for band, name in enumerate(fields):
                        if name.startswith("hs:"):
                            values[:,band] = spectra[offset:offset+n,int(name[3:])]
                            if "hs_valid" in available:
                                values[np.asarray(points["hs_valid"]) == 0,band] = np.nan
                        else:
                            values[:,band] = np.asarray(points[name])
                    if value_transform is not None:
                        values = np.asarray(value_transform(points,values), dtype=data_type)
                        if values.shape != (n,len(fields)):
                            raise ValueError("value_transform must preserve values shape")
                    if reducer == "surface":
                        mask &= np.isfinite(z) & np.isfinite(values).all(axis=1)
                    else:
                        mask &= np.isfinite(values).any(axis=1)
                    ids = (r//tile_size)*tile_cols+c//tile_size
                    order = np.flatnonzero(mask)
                    order = order[np.argsort(ids[order],kind="stable")]
                    records = np.empty(len(order),dtype=record_type)
                    records["cell"] = ((r[order]%tile_size)*tile_size+c[order]%tile_size).astype(np.uint32)
                    records["z"] = z[order]
                    records["id"] = np.asarray(order,dtype=np.uint64)+np.uint64(offset)
                    angle = (np.asarray(points["hs_angle"],dtype=np.float32) if "hs_angle" in available
                             else np.full(n,np.inf,dtype=np.float32))
                    records["angle"] = np.where(np.isfinite(angle[order]),np.abs(angle[order]),np.inf)
                    records["values"] = values[order]
                    if records.nbytes > shutil.disk_usage(scratch).free:
                        raise OSError("Insufficient scratch disk for point partitions")
                    sorted_ids = ids[order]
                    edges = np.r_[0,np.flatnonzero(np.diff(sorted_ids))+1,len(order)]
                    for start,end in zip(edges[:-1],edges[1:]):
                        if start != end:
                            with (scratch/f"{int(sorted_ids[start])}.bin").open("ab") as writer:
                                records[start:end].tofile(writer)
                    offset += n
                    if chunk_number % 20 == 0 or offset == total_points:
                        print(f"Raster partition: {offset:,}/{total_points:,} points", flush=True)
            if offset != total_points:
                raise ValueError("Source LAS point count mismatch")
            total_tiles = math.ceil(rows/tile_size)*tile_cols
            completed = 0
            with rasterio.open(output,"w",**profile) as dst:
                for row in range(0,rows,tile_size):
                    for col in range(0,cols,tile_size):
                        identifier = (row//tile_size)*tile_cols+col//tile_size
                        cells = tile_size*tile_size
                        partition = scratch/f"{identifier}.bin"
                        if reducer == "surface":
                            aggregate = np.full((len(fields),cells),np.nan,dtype=data_type)
                            best_z = np.full(cells,-np.inf,dtype=np.float64)
                            best_angle = np.full(cells,np.inf,dtype=np.float32)
                            best_id = np.full(cells,np.iinfo(np.uint64).max,dtype=np.uint64)
                            counts = None
                        else:
                            counts = np.zeros((len(fields),cells),dtype=np.uint64)
                            initial = np.inf if reducer == "min" else -np.inf if reducer == "max" else 0.0
                            aggregate = np.full((len(fields),cells),initial,dtype=np.float64)
                        if partition.exists():
                            if partition.stat().st_size % record_type.itemsize:
                                raise OSError("Truncated tile partition")
                            with partition.open("rb") as reader:
                                while True:
                                    records = np.fromfile(reader,dtype=record_type,count=point_chunk)
                                    if not len(records):
                                        break
                                    indices = records["cell"].astype(np.int64)
                                    if reducer == "surface":
                                        order = np.lexsort((records["id"],records["angle"],-records["z"],indices))
                                        ordered_cells = indices[order]
                                        first = np.r_[True, np.diff(ordered_cells) != 0]
                                        winners = records[order[first]]
                                        index = winners["cell"].astype(np.int64)
                                        better = ((winners["z"] > best_z[index]) |
                                                  ((winners["z"] == best_z[index]) &
                                                   ((winners["angle"] < best_angle[index]) |
                                                    ((winners["angle"] == best_angle[index]) &
                                                     (winners["id"] < best_id[index])))))
                                        chosen = winners[better]
                                        index = index[better]
                                        best_z[index] = chosen["z"]
                                        best_angle[index] = chosen["angle"]
                                        best_id[index] = chosen["id"]
                                        aggregate[:,index] = chosen["values"].T
                                    else:
                                        for band in range(len(fields)):
                                            value = records["values"][:,band].astype(np.float64)
                                            valid = np.isfinite(value)
                                            cell = indices[valid]
                                            np.add.at(counts[band],cell,1)
                                            if reducer == "min":
                                                np.minimum.at(aggregate[band],cell,value[valid])
                                            elif reducer == "max":
                                                np.maximum.at(aggregate[band],cell,value[valid])
                                            elif reducer == "mean":
                                                np.add.at(aggregate[band],cell,value[valid])
                            partition.unlink()
                        if reducer == "count":
                            aggregate = counts.astype(data_type)
                        elif reducer != "surface":
                            hit = counts > 0
                            if reducer == "mean":
                                np.divide(aggregate,counts,out=aggregate,where=hit)
                            aggregate[~hit] = np.nan
                        h,w = min(tile_size,rows-row),min(tile_size,cols-col)
                        tile = aggregate.reshape(len(fields),tile_size,tile_size)[:,:h,:w].astype(data_type,copy=False)
                        dst.write(tile,window=Window(col,row,w,h))
                        completed += 1
                        if completed % 20 == 0 or completed == total_tiles:
                            print(f"Raster write: {completed:,}/{total_tiles:,} tiles", flush=True)
                _spectral_tags(dst,fields,metadata,reducer,source)
                if reducer == "surface":
                    dst.update_tags(surface_selection="highest_z,lowest_hs_angle,earliest_source_point",
                                    spectral_consistency="one winning point supplies all bands")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        if spectra is not None:
            spectra._mmap.close()
    return output

def create_orthomosaic(flight:FlightConfig,surface,output,*,bands=None,tile_size=256,max_surface_mb=512):

    surface,output = resolve_path(surface),Path(output)
    if output.exists() or output.resolve() == surface.resolve() or any(output.resolve() in {s.header,s.binary} for s in flight.swaths):
        raise FileExistsError(output)
    if not isinstance(tile_size,int) or tile_size < 1 or max_surface_mb <= 0:
        raise ValueError("Positive tile_size and surface cap required")
    with rasterio.open(surface) as src:
        t = src.transform
        if t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0 or not math.isclose(t.a,-t.e):
            raise ValueError("Square north-up surface pixels required")
        if src.crs is None or CRS.from_user_input(src.crs) != CRS.from_user_input(flight.crs):
            raise ValueError("Flight/surface CRS mismatch")
        if src.width*src.height*17 > max_surface_mb*1024**2:
            raise MemoryError("Surface exceeds allocation cap")
        datum = src.tags().get("height_datum")
        if datum is not None and datum != flight.height_datum:
            raise ValueError("Height datum mismatch")
        data = src.read(1,masked=True,out_dtype="float64").filled(np.nan)
        profile = {"driver":"GTiff","width":src.width,"height":src.height,"dtype":"float32",
                   "crs":src.crs,"transform":t,"nodata":float("nan"),"tiled":True,
                   "blockxsize":256,"blockysize":256,"compress":"deflate","BIGTIFF":"YES"}
    native = flight.prepare()
    bands = list(range(native.bands)) if bands is None else list(bands)
    if not bands or any(i < 0 or i >= native.bands for i in bands):
        raise ValueError("Invalid output-band selection")
    profile["count"] = len(bands)
    data = np.ascontiguousarray(data)
    try:
        with rasterio.open(output,"w",**profile) as dst:
            for row in range(0,profile["height"],tile_size):
                for col in range(0,profile["width"],tile_size):
                    h,w = min(tile_size,profile["height"]-row),min(tile_size,profile["width"]-col)
                    tile = native.mosaic_chunk(data,t.c,t.f,t.a,t.c+col*t.a,t.f-row*t.a,w,h,bands)
                    dst.write(tile,window=Window(col,row,w,h))
            dst.update_tags(height_datum=flight.height_datum,time_reference=flight.time_reference)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output
