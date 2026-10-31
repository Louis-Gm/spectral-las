from __future__ import annotations
from datetime import datetime
import json
import math
from pathlib import Path
import time
import tempfile
import shutil
import os
from contextlib import ExitStack
from collections import defaultdict
import laspy
import numpy as np
from numpy.lib.format import open_memmap
from ..config import FlightConfig
from .io import companion_paths
from .operations import add_rgb_to_header
from ..raster.io import read_envi_header
OBS_DIMS = (
    ("hs_valid", np.uint8),
    ("hs_swath", np.int32),
    ("hs_line", np.int32),
    ("hs_sample", np.int32),
    ("hs_angle", np.float32),
)
def _log(message: str) -> None:
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}", flush=True)
def _memory_status() -> str:
    try:
        import psutil
    except ImportError:
        return "RSS not available (install psutil for RAM readings)"
    info=psutil.Process(os.getpid()).memory_info()
    value=f"RSS={info.rss/2**30:.3f} GiB"
    if hasattr(info,"peak_wset"):
        value+=f" peak_working_set={info.peak_wset/2**30:.3f} GiB"
    return value
def _xyz(chunk) -> np.ndarray:
    return np.ascontiguousarray(np.column_stack((
        np.asarray(chunk.x), np.asarray(chunk.y), np.asarray(chunk.z)
    )), dtype=np.float64)
def _gps_times(chunk, use_gps_time: bool) -> np.ndarray | None:
    if not use_gps_time:
        return None
    values = np.asarray(chunk.gps_time, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError("LAS contains non-finite GPS times")
    return np.ascontiguousarray(values)
def _validate_crs(crs, expected_crs: str | None) -> None:
    if crs is None:
        raise ValueError("LAS CRS missing: assign its actual CRS first")
    if not crs.is_projected:
        raise ValueError("LAS must be in a projected CRS matching SBET easting/northing")
    units = [axis.unit_name.lower() for axis in crs.axis_info[:2]]
    if len(units) < 2 or not all(unit in {"metre", "meter", "metres", "meters"} for unit in units):
        raise ValueError("LAS XY coordinate units must be metres")
    if expected_crs is not None:
        from pyproj import CRS
        if crs != CRS.from_user_input(expected_crs):
            raise ValueError(f"LAS CRS does not match declared SBET CRS: {expected_crs}")
def _validate_rgb(rgb_bands, rgb_limits, bands: int):
    if (rgb_bands is None) != (rgb_limits is None):
        raise ValueError("rgb_bands and rgb_limits must be provided together")
    if rgb_bands is None:
        return None, None, None
    rgb_bands = tuple(int(b) for b in rgb_bands)
    if len(rgb_bands) != 3 or any(b < 0 or b >= bands for b in rgb_bands):
        raise ValueError(f"rgb_bands must be three zero-based indices in [0,{bands})")
    low, high = (np.asarray(v, dtype=np.float64) for v in rgb_limits)
    if low.shape != (3,) or high.shape != (3,) or not np.isfinite([low, high]).all() or np.any(high <= low):
        raise ValueError("rgb_limits must be ((r,g,b) lows, (r,g,b) highs) with high > low")
    return rgb_bands, low, high
def paint_pointcloud(
    flight_config: FlightConfig, las_path: str | Path, output_path: str | Path, *,
    point_chunk: int = 25000, max_time_gap: float = 0.25,
    max_cross_track_error: float = 0.15, depth_tolerance: float = 0.35,
    flush_every_chunks: int = 10, use_gps_time: bool = False, reuse_candidates: bool = True,
    rgb_bands: tuple[int, int, int] | None = None,
    rgb_limits: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None,
) -> Path:
    started = time.perf_counter()
    source = Path(las_path).resolve()
    target = Path(output_path).resolve()
    if source == target:
        raise ValueError("Refusing to overwrite input LAS")
    if not source.is_file() or not target.parent.is_dir():
        raise FileNotFoundError("Input LAS or output directory missing")
    if point_chunk < 1 or flush_every_chunks < 1 or any(
        not math.isfinite(v) or v <= 0 for v in
        (max_time_gap, max_cross_track_error, depth_tolerance)
    ):
        raise ValueError("Chunk, flush interval and tolerances must be positive and finite")
    if not isinstance(flight_config, FlightConfig):
        raise TypeError("Provide a FlightConfig; use adapters.headwall.load_flight for a Headwall folder")
    sbet_crs = flight_config.crs
    las_height_datum = flight_config.height_datum
    camera = flight_config.camera
    boresight_roll, boresight_pitch, boresight_yaw = camera.boresight_degrees
    lever_arm_x, lever_arm_y, lever_arm_z = camera.lever_arm_m
    apply_geoid = bool(flight_config.metadata.get("height_correction_applied", False))
    occlusion_radius = 0.12
    spectral_path, metadata_path = companion_paths(target)
    products = (target, spectral_path, metadata_path)
    for product in products:
        if product.exists():
            raise FileExistsError(product)
    _log("Preparing flight once (trajectory, line poses, and memory-mapped cubes)...")
    flight = flight_config.prepare()
    bands = flight.bands
    source_bands = flight.source_bands
    rgb_bands, rgb_low, rgb_high = _validate_rgb(rgb_bands, rgb_limits, bands)
    source_metadata = []
    for swath in flight_config.swaths:
        meta = read_envi_header(swath.header)
        wave = [float(v.strip()) for v in meta.get("wavelength", "").split(",") if v.strip()]
        if wave and (len(wave) != source_bands or not np.isfinite(wave).all()):
            raise ValueError("Invalid wavelength metadata")
        source_metadata.append((wave, meta.get("wavelength units")))
    if any(value != source_metadata[0] for value in source_metadata[1:]):
        raise ValueError("Swath wavelength definitions differ")
    groups = (tuple((i, i) for i in range(source_bands)) if flight_config.spectral_groups is None
              else flight_config.spectral_groups)
    wavelength = ([0.5*(source_metadata[0][0][a]+source_metadata[0][0][b]) for a,b in groups]
                  if source_metadata[0][0] else [])
    _log(f"Prepared: {bands} bands; swaths={flight.swath_ids}"
         + (f"; rgb_bands={list(rgb_bands)}" if rgb_bands is not None else ""))
    spectra_map = None
    success = False
    timings = defaultdict(float)
    spool_context = tempfile.TemporaryDirectory(prefix="spectral_las_candidates_", dir=target.parent) if reuse_candidates else None
    spool_path = Path(spool_context.name) / "candidates.bin" if spool_context is not None else None
    try:
        with laspy.open(source) as src:
            npoints = int(src.header.point_count)
            if npoints == 0:
                raise ValueError("Input LAS contains zero points")
            _validate_crs(src.header.parse_crs(), sbet_crs)
            if use_gps_time and "gps_time" not in src.header.point_format.dimension_names:
                raise ValueError("LAS point format does not contain gps_time")
            if use_gps_time:
                if flight_config.time_reference != "gps_seconds_of_week":
                    raise ValueError("LAS time matching requires flight GPS seconds of week")
                if src.header.global_encoding.value & 1:
                    raise ValueError("Convert adjusted-standard LAS GPS times before matching")
            header = src.header.copy()
            existing = set(header.point_format.dimension_names)
            if existing.intersection(name for name, _ in OBS_DIMS):
                raise ValueError("Input LAS already contains HyperLAS observation fields")
            if rgb_bands is not None and {"red","green","blue"}.intersection(existing):
                raise ValueError("Input LAS already contains RGB fields")
            header.add_extra_dims([
                laspy.ExtraBytesParams(name=name, type=dtype)
                for name, dtype in OBS_DIMS
            ])
            if rgb_bands is not None:


                header = add_rgb_to_header(header)
            _log(f"LAS: {npoints:,} points; estimated spectra file {npoints * bands * 4 / 1e9:.2f} GB")
            _log("START pass 1: build depth map and cache candidates")
            pass1_start = time.perf_counter()
            candidate_total = 0
            spool_total = 0
            seen = 0
            spool_writer = open(spool_path, "wb") if reuse_candidates else None
            try:
                for number, chunk in enumerate(src.chunk_iterator(point_chunk), start=1):
                    t = time.perf_counter()
                    xyz = _xyz(chunk)
                    gps = _gps_times(chunk, use_gps_time)
                    timings["pass1_numpy_input_s"] += time.perf_counter()-t
                    t = time.perf_counter()
                    if reuse_candidates:
                        payload, profile = flight.accumulate_spooled(
                            xyz, gps, max_time_gap, max_cross_track_error,
                            occlusion_radius, depth_tolerance,
                        )
                        candidate_total += int(profile["candidates"])
                        if len(payload) != int(profile["bytes"]):
                            raise RuntimeError("Candidate byte count mismatch")
                        spool_total += len(payload)+8
                        timings["pass1_candidate_search_s"] += profile["candidate_search_s"]
                        timings["pass1_serialization_s"] += profile["serialization_s"]
                        timings["pass1_depth_merge_s"] += profile["depth_merge_s"]
                    else:
                        flight.accumulate(xyz, gps, max_time_gap, max_cross_track_error,
                                          occlusion_radius, depth_tolerance)
                    timings["pass1_rust_call_s"] += time.perf_counter()-t
                    if reuse_candidates:
                        t = time.perf_counter()
                        spool_writer.write(len(payload).to_bytes(8,"little"))
                        spool_writer.write(payload)
                        timings["pass1_spool_write_s"] += time.perf_counter()-t
                    seen += len(chunk)
                    if number % flush_every_chunks == 0 or seen == npoints:
                        if reuse_candidates:
                            mean=candidate_total/max(seen,1)
                            estimate=4*npoints+36*mean*npoints+8*(npoints/point_chunk+1)
                            free=shutil.disk_usage(target.parent).free
                            _log(f"Pass 1: {seen:,}/{npoints:,}; candidates={candidate_total:,}; "
                                 f"mean={mean:.3f}/point; cached={spool_total/2**30:.3f} GiB; "
                                 f"projected={estimate/2**30:.3f} GiB; free={free/2**30:.1f} GiB; "
                                 f"{_memory_status()}")
                            if estimate > free+spool_total:
                                raise OSError("Not enough free disk for projected candidate spool")
                        else:
                            _log(f"Pass 1: {seen:,}/{npoints:,}")
            finally:
                if spool_writer is not None:
                    spool_writer.close()
            if seen != npoints:
                raise RuntimeError(f"LAS header says {npoints} points; pass 1 read {seen}")
            timings["pass1_total_s"] = time.perf_counter()-pass1_start
            _log(f"END pass 1: {timings['pass1_total_s']:.3f}s")
        _log("START pass 2: select from cached candidates and write LAS")
        pass2_start = time.perf_counter()
        spectra_map = open_memmap(spectral_path, mode="w+", dtype=np.float32,
                                  shape=(npoints, bands))
        offset = 0
        matches = 0
        rgb_matches = 0
        with ExitStack() as stack:
            src = stack.enter_context(laspy.open(source))
            dst = stack.enter_context(laspy.open(target, mode="w", header=header))
            spool_reader = stack.enter_context(open(spool_path, "rb")) if reuse_candidates else None
            for number, chunk in enumerate(src.chunk_iterator(point_chunk), start=1):
                count = len(chunk)
                if reuse_candidates:
                    t = time.perf_counter()
                    size_bytes = spool_reader.read(8)
                    if len(size_bytes) != 8:
                        raise RuntimeError(f"Missing candidate spool for chunk {number}")
                    length = int.from_bytes(size_bytes,"little")
                    if length < 4+4*count or length > 512*1024*1024:
                        raise RuntimeError(f"Invalid candidate spool frame length {length}")
                    payload = spool_reader.read(length)
                    if len(payload) != length:
                        raise RuntimeError(f"Truncated candidate spool at chunk {number}")
                    timings["pass2_spool_read_s"] += time.perf_counter()-t
                    t = time.perf_counter()
                    spectra, valid, swath, line, sample, angle, profile = flight.paint_spooled(
                        payload, count, depth_tolerance,
                    )
                    timings["pass2_rust_call_s"] += time.perf_counter()-t
                    for key in ("spool_validate_copy_s","candidate_selection_s",
                                "output_allocation_s","spectrum_read_s"):
                        timings["pass2_rust_"+key] += profile[key]
                else:
                    t = time.perf_counter()
                    xyz = _xyz(chunk)
                    gps = _gps_times(chunk, use_gps_time)
                    timings["pass2_numpy_input_s"] += time.perf_counter()-t
                    t = time.perf_counter()
                    spectra, valid, swath, line, sample, angle = flight.paint(
                        xyz, gps, max_time_gap, max_cross_track_error,
                        occlusion_radius, depth_tolerance,
                    )
                    timings["pass2_rust_call_s"] += time.perf_counter()-t
                if spectra.shape != (count, bands):
                    raise ValueError(f"Expected {(count, bands)} spectra, got {spectra.shape}")
                t = time.perf_counter()
                spectra_map[offset:offset + count] = spectra
                timings["pass2_npy_write_s"] += time.perf_counter()-t
                output = laspy.ScaleAwarePointRecord.zeros(count, header=header)
                for dim in src.header.point_format.dimension_names:
                    output[dim] = chunk[dim]
                output["hs_valid"] = valid
                output["hs_swath"] = swath
                output["hs_line"] = line
                output["hs_sample"] = sample
                output["hs_angle"] = angle
                if rgb_bands is not None:
                    t = time.perf_counter()



                    rgb_values = np.asarray(spectra[:, list(rgb_bands)], dtype=np.float64)
                    finite_rgb = np.isfinite(rgb_values).all(axis=1)
                    scaled = np.clip((rgb_values - rgb_low) / (rgb_high - rgb_low), 0.0, 1.0) * 65535.0
                    scaled = np.rint(scaled).astype(np.uint16)
                    scaled[~finite_rgb] = 0
                    output["red"] = scaled[:, 0]
                    output["green"] = scaled[:, 1]
                    output["blue"] = scaled[:, 2]
                    rgb_matches += int(np.count_nonzero(finite_rgb))
                    timings["pass2_rgb_s"] += time.perf_counter()-t
                t = time.perf_counter()
                dst.write_points(output)
                timings["pass2_las_write_s"] += time.perf_counter()-t
                offset += count
                matches += int(np.count_nonzero(valid))
                if number % flush_every_chunks == 0 or offset == npoints:
                    t = time.perf_counter()
                    spectra_map.flush()
                    timings["pass2_npy_flush_s"] += time.perf_counter()-t
                    detail=(f"; selection={profile['candidate_selection_s']:.3f}s; "
                            f"spectrum={profile['spectrum_read_s']:.3f}s" if reuse_candidates else "")
                    rgb_detail = f"; rgb={rgb_matches:,}" if rgb_bands is not None else ""
                    _log(f"Pass 2: {offset:,}/{npoints:,}; matched={matches:,}{rgb_detail}{detail}")
            if reuse_candidates and spool_reader.read(1):
                raise RuntimeError("Spool has extra frames after LAS ends")
        if offset != npoints:
            raise RuntimeError(f"LAS header says {npoints} points; pass 2 read {offset}")
        spectra_map.flush()
        timings["pass2_total_s"] = time.perf_counter()-pass2_start
        _log(f"END pass 2: {timings['pass2_total_s']:.3f}s")
        metadata_path.write_text(json.dumps({
            "schema_version": 2,
            "time_reference": flight_config.time_reference,
            "flight_metadata": flight_config.metadata,
            "input_band_count": source_bands,
            "source_band_pairs_zero_based": [list(pair) for pair in groups],
            "spectral_operation": "pair_mean" if flight_config.spectral_groups is not None else "identity",
            "wavelengths": wavelength, "wavelength_units": source_metadata[0][1],
            "invalid_policy": "strict: both values valid for every output pair; otherwise unmatched",
            "original_images_modified": False,
            "input_las": str(source), "painted_las": str(target),
            "spectra_npy": str(spectral_path), "n_points": npoints,
            "n_bands": bands, "swath_ids": flight.swath_ids,
            "row_mapping": "spectra[i, :] corresponds to LAS point i in original order",
            "unmatched": "hs_valid=0; spectral row filled with NaN",
            "matched_points": matches,
            "boresight_degrees": [boresight_roll, boresight_pitch, boresight_yaw],
            "lever_arm_m": [lever_arm_x, lever_arm_y, lever_arm_z],
            "apply_geoid": apply_geoid, "las_height_datum": las_height_datum,
            "sbet_crs": sbet_crs, "use_gps_time": use_gps_time,
            "max_time_gap_s": max_time_gap,
            "max_cross_track_error_m": max_cross_track_error,
            "occlusion_radius_m": occlusion_radius,
            "depth_tolerance_m": depth_tolerance,
            "visibility": "global nearest observed LAS point within the same swath/line/sample",
            "visibility_limit": "Point-sampled only; no continuous-surface ray test; occlusion_radius is reserved and not applied",
            "candidate_reuse": reuse_candidates,
            "candidate_count": candidate_total if reuse_candidates else None,
            "candidate_spool_bytes": spool_total if reuse_candidates else None,
            "rgb_bands_zero_based": list(rgb_bands) if rgb_bands is not None else None,
            "rgb_limits_low_high": ([list(rgb_limits[0]), list(rgb_limits[1])]
                                    if rgb_bands is not None else None),
            "rgb_storage": ("uint16 in standard LAS red/green/blue point-format fields"
                            if rgb_bands is not None else None),
            "rgb_is_radiometric_calibration": False,
            "rgb_matched_points": rgb_matches if rgb_bands is not None else None,
            "stage_timings_s": dict(timings),
        }, indent=2), encoding="utf-8")
        _log("Cumulative timings: " + ", ".join(
            f"{key}={value:.3f}s" for key,value in sorted(timings.items())))
        success = True
        _log(f"Finished in {time.perf_counter() - started:.1f}s; {matches:,}/{npoints:,} matched")
        return target
    finally:
        if spectra_map is not None:
            spectra_map._mmap.close()
            del spectra_map
        if spool_context is not None:
            spool_context.cleanup()
        if not success:
            for product in products:
                product.unlink(missing_ok=True)
