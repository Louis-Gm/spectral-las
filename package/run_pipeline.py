from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path
import time
import laspy
import numpy as np
from pyproj import CRS, Transformer
from spectral_las.adapters.headwall import load_flight
from spectral_las.pointcloud import paint_pointcloud
from spectral_las.pointcloud.io import companion_paths, open_spectra
from spectral_las.pointcloud.operations import add_rgb_to_header
from spectral_las.raster import rasterize_pointcloud
from spectral_las.raster.io import read_envi_header


PARAMETERS = {
    "flight_directory": Path(r"C:\Temp_traitement\Valcartier_2025_07_16\BaseValcartier_74m_4p6ms_16072025_2025_07_16_14_29_42_555"),
    "las_path": "lidar.las",
    "output_directory": None,
    "crs": "EPSG:32619",
    "height_datum": "orthometric",
    "trajectory_height_datum": "ellipsoidal",
    "gps_utc_offset_seconds": 18,
    "boresight_degrees": (-0.05, -0.225, 0.0),
    "lever_arm_m": (0.0, 0.0, 0.0),
    "allow_geoid_extrapolation": False,
    "point_chunk": 100_000,
    "tile_size": 256,
    "reducer": "mean",
    "classifications": None,
    "scratch_directory": None,
    "max_time_gap": 0.25,
    "max_cross_track_error": 0.15,
    "depth_tolerance": 0.35,
    "resolution": 0.10,
    "use_gps_time": False,
    "rgb_bands": None,
    "rgb_limits": None,
    "rgb_percentiles": (0.0, 100.0),
    "rgb_wavelength_targets_nm": (640.0, 550.0, 465.0),
}


def _log(message):
    print(
        f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {message}",
        flush=True,
    )


def _las_path(folder, supplied):
    if supplied is not None:
        path = Path(supplied)
        path = path if path.is_absolute() else folder / path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path.resolve()
    candidates = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".las", ".laz"}
    )
    if len(candidates) != 1:
        names = ", ".join(p.name for p in candidates) or "none"
        raise ValueError(
            f"Expected exactly one input LAS/LAZ; found {names}. "
            "Set PARAMETERS['las_path'] to select the input explicitly."
        )
    return candidates[0].resolve()

def _describe_crs(crs):
    try:
        return f"{crs.name} ({crs.to_string()})"
    except Exception:
        return str(crs)


def _reproject_las(source, destination, target_crs, point_chunk):
    """Reproject LAS XY into ``target_crs``. Z is not modified.

    Coordinates are transformed; scales/offsets/extra dims/point format are
    preserved. Writes to ``destination`` (must not exist).
    """
    with laspy.open(source) as reader:
        source_crs = reader.header.parse_crs()
        if source_crs is None:
            raise ValueError(
                f"Input LAS has no CRS assigned: {source}. "
                "Tag it first (e.g. `las2las -i input.las -o tagged.las -epsg <code>`)."
            )
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        header = reader.header.copy()
        header.add_crs(target_crs)
        total = int(reader.header.point_count)
        written = 0
        with laspy.open(destination, mode="w", header=header) as writer:
            for chunk in reader.chunk_iterator(point_chunk):
                x = np.asarray(chunk.x, dtype=np.float64)
                y = np.asarray(chunk.y, dtype=np.float64)
                new_x, new_y = transformer.transform(x, y)
                chunk.x = new_x
                chunk.y = new_y
                writer.write_points(chunk)
                written += len(chunk)
                if written % 1_000_000 == 0 or written == total:
                    _log(f"  reprojected {written:,}/{total:,}")
    return destination

def _ensure_matching_crs(source, destination_dir, flight_crs_string, point_chunk):
    """Return a LAS path whose CRS matches ``flight_crs_string``.

    - matching CRS: returns ``source`` unchanged.
    - differing CRS: reprojects to a temporary LAS in ``destination_dir`` and
      returns that path. The caller must delete it.
    - missing CRS: raises ValueError.
    """
    target = CRS.from_user_input(flight_crs_string)
    with laspy.open(source) as reader:
        input_crs = reader.header.parse_crs()
    if input_crs is None:
        raise ValueError(
            f"Input LAS has no CRS assigned: {source}. "
            "Assign it before running (e.g. `las2las -i input.las -o tagged.las "
            f"-epsg <code>` or the reproject_las.py helper). The pipeline "
            f"expects the flight CRS {_describe_crs(target)}."
        )
    if CRS.from_user_input(input_crs) == target:
        return source, None
    _log(
        f"WARNING: input LAS CRS ({_describe_crs(input_crs)}) does not match "
        f"flight CRS ({_describe_crs(target)}). "
        "Reprojecting XY coordinates automatically; Z is not modified."
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    reprojected = destination_dir / f"{Path(source).stem}__reprojected.las"
    reprojected.unlink(missing_ok=True)
    _reproject_las(source, reprojected, target, point_chunk)
    _log(
        f"WARNING: LAS reprojected {_describe_crs(input_crs)} -> "
        f"{_describe_crs(target)} at {reprojected}"
    )
    return reprojected, reprojected

def _parse_wavelength_units(meta, values):
    units = (meta.get("wavelength units") or "").strip().lower()
    if units in {"nm", "nanometers", "nanometer", "nanometre", "nanometres"}:
        return 1.0
    if units in {"um", "µm", "micrometers", "micrometer",
                 "micrometre", "micrometres"}:
        return 1000.0
    return 1.0 if max(values) > 10.0 else 1000.0

def _output_band_wavelengths_nm(flight):
    source_nm = None
    for swath in flight.swaths:
        meta = read_envi_header(swath.header)
        raw = meta.get("wavelength", "")
        values = [float(v.strip()) for v in raw.split(",") if v.strip()]
        if not values:
            _log(f"No wavelength metadata in {swath.header}")
            return None
        scale = _parse_wavelength_units(meta, values)
        band_nm = [v * scale for v in values]
        if source_nm is None:
            source_nm = band_nm
        elif band_nm != source_nm:
            raise ValueError("Swath wavelength definitions differ")
    groups = flight.spectral_groups
    if groups is None:
        return source_nm
    return [0.5 * (source_nm[a] + source_nm[b]) for a, b in groups]

def _auto_rgb_bands_from_wavelengths(out_wavelengths_nm, targets_nm):
    if out_wavelengths_nm is None:
        return None
    arr = np.asarray(out_wavelengths_nm, dtype=np.float64)
    if arr.ndim != 1 or arr.size < 3:
        return None
    indices = [int(np.argmin(np.abs(arr - t))) for t in targets_nm]
    if len(set(indices)) != 3:
        return None
    return tuple(indices)

def _auto_rgb_bands_even_spacing(output_bands):
    if output_bands < 3:
        raise ValueError(f"At least three output bands required; got {output_bands}")
    return (output_bands // 6, output_bands // 2, (5 * output_bands) // 6)


# ---------------------------------------------------------------------------
# Auto RGB display stretch for LAS uint16 fields
# ---------------------------------------------------------------------------
def _compute_rgb_limits(
    spectra_path, rgb_bands, percentiles,
    *, target_samples=2_000_000, chunk_rows=500_000,
):
    spectra = np.load(spectra_path, mmap_mode="r", allow_pickle=False)
    try:
        if spectra.ndim != 2:
            raise ValueError("Painted spectra must be a 2D array")
        n_bands = spectra.shape[1]
        bands = [int(b) for b in rgb_bands]
        if len(bands) != 3 or any(b < 0 or b >= n_bands for b in bands):
            raise ValueError(f"rgb_bands must be three indices in [0,{n_bands})")
        total = spectra.shape[0]
        if total == 0:
            raise ValueError("Painted spectra is empty")
        stride = max(1, total // max(1, target_samples))
        collected = [[] for _ in range(3)]
        for start in range(0, total, chunk_rows):
            block = np.asarray(
                spectra[start:start + chunk_rows, bands], dtype=np.float64
            )
            if stride > 1:
                block = block[::stride, :]
            for i in range(3):
                col = block[:, i]
                col = col[np.isfinite(col)]
                if col.size:
                    collected[i].append(col)
        lows, highs = [], []
        for i in range(3):
            if not collected[i]:
                raise ValueError(f"No finite samples for RGB channel {i}")
            values = np.concatenate(collected[i])
            lo, hi = np.percentile(values, percentiles)
            lo, hi = float(lo), float(hi)
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                lo, hi = float(values.min()), float(values.max())
                if hi <= lo:
                    hi = lo + 1.0
            lows.append(lo)
            highs.append(hi)
        return (tuple(lows), tuple(highs))
    finally:
        spectra._mmap.close()


def _rewrite_las_with_rgb(las_path, rgb_bands, rgb_limits, point_chunk):
    las_path = Path(las_path)
    spectra_path, _ = companion_paths(las_path)
    if not spectra_path.is_file():
        raise FileNotFoundError(spectra_path)
    spectra = np.load(spectra_path, mmap_mode="r", allow_pickle=False)
    bands = [int(b) for b in rgb_bands]
    low = np.asarray(rgb_limits[0], dtype=np.float64)
    high = np.asarray(rgb_limits[1], dtype=np.float64)
    if low.shape != (3,) or high.shape != (3,) or np.any(high <= low):
        raise ValueError(
            "rgb_limits must be ((r,g,b) lows, (r,g,b) highs) with high > low"
        )
    temp_las = las_path.with_name(las_path.stem + "__rgb_tmp.las")
    temp_las.unlink(missing_ok=True)
    try:
        with laspy.open(las_path) as reader:
            header = add_rgb_to_header(reader.header.copy())
            source_dims = tuple(reader.header.point_format.dimension_names)
            total = int(reader.header.point_count)
            with laspy.open(temp_las, mode="w", header=header) as writer:
                offset = 0
                for chunk in reader.chunk_iterator(point_chunk):
                    n = len(chunk)
                    output = laspy.ScaleAwarePointRecord.zeros(n, header=header)
                    for dim in source_dims:
                        output[dim] = chunk[dim]
                    values = np.asarray(
                        spectra[offset:offset + n, bands], dtype=np.float64
                    )
                    valid = np.isfinite(values).all(axis=1)
                    if "hs_valid" in source_dims:
                        valid &= np.asarray(chunk["hs_valid"]) != 0
                    colors = np.zeros((n, 3), dtype=np.uint16)
                    if valid.any():
                        scaled = (values[valid] - low) / (high - low)
                        colors[valid] = np.rint(
                            np.clip(scaled, 0.0, 1.0) * 65535.0
                        ).astype(np.uint16)
                    output["red"] = colors[:, 0]
                    output["green"] = colors[:, 1]
                    output["blue"] = colors[:, 2]
                    writer.write_points(output)
                    offset += n
                if offset != total:
                    raise RuntimeError(
                        f"Point count changed during RGB rewrite: {offset} != {total}"
                    )
        temp_las.replace(las_path)
    except BaseException:
        temp_las.unlink(missing_ok=True)
        raise
    finally:
        spectra._mmap.close()


def _update_manifest_with_rgb(manifest_path, rgb_bands, rgb_limits, rgb_wavelengths_nm):
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    payload["rgb_bands_zero_based"] = list(rgb_bands)
    payload["rgb_band_wavelengths_nm"] = (
        [float(w) for w in rgb_wavelengths_nm] if rgb_wavelengths_nm else None
    )
    payload["rgb_limits_low_high"] = [list(rgb_limits[0]), list(rgb_limits[1])]
    payload["rgb_storage"] = (
        "uint16 in standard LAS red/green/blue point-format fields"
    )
    payload["rgb_is_radiometric_calibration"] = False
    Path(manifest_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")





def run_pipeline(
    flight_directory,
    *,
    crs,
    height_datum,
    gps_utc_offset_seconds,
    las_path=None,
    output_directory=None,
    boresight_degrees=(0.0, 0.0, 0.0),
    lever_arm_m=(0.0, 0.0, 0.0),
    trajectory_height_datum="ellipsoidal",
    allow_geoid_extrapolation=False,
    point_chunk=100_000,
    tile_size=256,
    reducer="surface",
    max_time_gap=0.25,
    max_cross_track_error=0.15,
    depth_tolerance=0.35,
    use_gps_time=False,
    classifications=None,
    resolution=PARAMETERS["resolution"],
    scratch_directory=None,
    rgb_bands=None,
    rgb_limits=None,
    rgb_percentiles=(2.0, 98.0),
    rgb_wavelength_targets_nm=(640.0, 550.0, 465.0),
):

    if gps_utc_offset_seconds is None:
        raise ValueError(
            "Set PARAMETERS['gps_utc_offset_seconds'] to the verified offset "
            "for your acquisition date before running."
        )
    if not isinstance(point_chunk, int) or point_chunk < 1:
        raise ValueError("point_chunk must be a positive integer")
    if not isinstance(tile_size, int) or not 1 <= tile_size <= 4096:
        raise ValueError("tile_size must be an integer from 1 to 4096")
    if reducer not in {"surface", "mean"}:
        raise ValueError("reducer must be 'surface' or 'mean'")
    if classifications is not None:
        classifications = tuple(classifications)
        if not classifications:
            raise ValueError("classifications must be nonempty when specified")
    if rgb_limits is not None:
        low = tuple(float(v) for v in rgb_limits[0])
        high = tuple(float(v) for v in rgb_limits[1])
        if len(low) != 3 or len(high) != 3 or any(high[i] <= low[i] for i in range(3)):
            raise ValueError(
                "rgb_limits must be ((r,g,b) lows, (r,g,b) highs) with high > low"
            )
        rgb_limits = (low, high)
    if not (isinstance(rgb_percentiles, (tuple, list)) and len(rgb_percentiles) == 2):
        raise ValueError("rgb_percentiles must be a (low, high) pair")

    started = time.perf_counter()
    folder = Path(flight_directory).resolve()
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    original_source = _las_path(folder, las_path)
    destination = (
        folder / "spectral_las_output"
        if output_directory is None
        else Path(output_directory).resolve()
    )
    resolution_cm = int(round(resolution * 100))
    painted = destination / "hyperspectral_half.las"
    mosaic = destination / f"hyperspectral_{resolution_cm}cm.tif"
    rgb_mosaic = destination / f"rgb_{resolution_cm}cm.tif"
    report_path = destination / "pipeline_report.json"
    spectra_path, manifest_path = companion_paths(painted)
    products = [painted, spectra_path, manifest_path, mosaic, rgb_mosaic, report_path]
    for path in products:
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing output: {path}")
    if scratch_directory is not None:
        scratch_directory = Path(scratch_directory).resolve()
        if not scratch_directory.is_dir():
            raise NotADirectoryError(scratch_directory)
    destination.mkdir(parents=True, exist_ok=True)

    reprojected_path = None
    try:
        _log("Read Headwall configuration and normalize trajectory/line times")
        flight = load_flight(
            folder,
            crs=crs,
            height_datum=height_datum,
            gps_utc_offset_seconds=gps_utc_offset_seconds,
            boresight_degrees=tuple(boresight_degrees),
            lever_arm_m=tuple(lever_arm_m),
            trajectory_height_datum=trajectory_height_datum,
            allow_geoid_extrapolation=allow_geoid_extrapolation,
        ).with_pairwise_binning()
        input_bands = flight.metadata["spectral_transform"]["input_bands"]
        output_bands = len(flight.spectral_groups)
        _log(
            f"Virtual binning: {input_bands} -> {output_bands} bands; "
            "source cubes stay unchanged"
        )


        source, reprojected_path = _ensure_matching_crs(
            original_source, destination, flight.crs, point_chunk
        )


        out_wavelengths_nm = _output_band_wavelengths_nm(flight)
        if out_wavelengths_nm is not None and len(out_wavelengths_nm) != output_bands:
            raise RuntimeError(
                f"Wavelength count ({len(out_wavelengths_nm)}) does not match "
                f"output band count ({output_bands})"
            )
        if rgb_bands is None:
            auto = _auto_rgb_bands_from_wavelengths(
                out_wavelengths_nm, tuple(rgb_wavelength_targets_nm)
            )
            if auto is not None:
                rgb_bands = auto
                r_wl, g_wl, b_wl = (out_wavelengths_nm[i] for i in rgb_bands)
                _log(
                    "Auto-selected RGB bands by wavelength: "
                    f"R={rgb_bands[0]} ({r_wl:.1f} nm), "
                    f"G={rgb_bands[1]} ({g_wl:.1f} nm), "
                    f"B={rgb_bands[2]} ({b_wl:.1f} nm)"
                )
            else:
                rgb_bands = _auto_rgb_bands_even_spacing(output_bands)
                _log(
                    "WARNING: wavelength metadata unavailable; falling back to "
                    f"evenly spaced RGB bands {list(rgb_bands)}. "
                    "Verify against your sensor before trusting colours."
                )
        rgb_bands = tuple(int(b) for b in rgb_bands)
        if len(rgb_bands) != 3 or any(b < 0 or b >= output_bands for b in rgb_bands):
            raise ValueError(
                f"rgb_bands must be three zero-based indices in [0,{output_bands})"
            )
        if len(set(rgb_bands)) != 3:
            raise ValueError(f"rgb_bands must be three distinct indices; got {rgb_bands}")
        rgb_wavelengths_nm = (
            [out_wavelengths_nm[i] for i in rgb_bands]
            if out_wavelengths_nm is not None else None
        )

        with laspy.open(source) as reader:
            input_count = int(reader.header.point_count)
            las_crs = reader.header.parse_crs()
            flight_crs = CRS.from_user_input(flight.crs)
            if las_crs is None or CRS.from_user_input(las_crs) != flight_crs:
                # Should have been handled by _ensure_matching_crs; this is a
                # defensive check in case the helper misbehaves.
                raise ValueError(
                    "Internal error: CRS still mismatched after reprojection"
                )


        use_direct_rgb = rgb_limits is not None
        rewrite_elapsed = 0.0
        if use_direct_rgb:
            _log(
                f"Paint {input_count:,} points with adjacent-pair means "
                f"and inline RGB bands {list(rgb_bands)}"
            )
            paint_start = time.perf_counter()
            paint_pointcloud(
                flight,
                source,
                painted,
                point_chunk=point_chunk,
                max_time_gap=max_time_gap,
                max_cross_track_error=max_cross_track_error,
                depth_tolerance=depth_tolerance,
                use_gps_time=use_gps_time,
                reuse_candidates=True,
                rgb_bands=rgb_bands,
                rgb_limits=rgb_limits,
            )
            paint_elapsed = time.perf_counter() - paint_start
            spectral_array, metadata = open_spectra(painted)
            try:
                if spectral_array.shape != (input_count, output_bands):
                    raise RuntimeError(
                        "Painted spectral shape does not match half-band configuration"
                    )
                matched = int(metadata.get("matched_points", 0))
                if not matched:
                    raise RuntimeError(
                        "No points received valid spectra; "
                        "completed cloud retained for diagnosis"
                    )
            finally:
                spectral_array._mmap.close()
        else:
            temp_painted = destination / "hyperspectral_half__raw.las"
            temp_spectra, temp_manifest = companion_paths(temp_painted)
            for path in (temp_painted, temp_spectra, temp_manifest):
                path.unlink(missing_ok=True)
            _log(
                f"Paint {input_count:,} points with adjacent-pair means "
                f"(deferred RGB bands {list(rgb_bands)})"
            )
            try:
                paint_start = time.perf_counter()
                paint_pointcloud(
                    flight,
                    source,
                    temp_painted,
                    point_chunk=point_chunk,
                    max_time_gap=max_time_gap,
                    max_cross_track_error=max_cross_track_error,
                    depth_tolerance=depth_tolerance,
                    use_gps_time=use_gps_time,
                    reuse_candidates=True,
                )
                paint_elapsed = time.perf_counter() - paint_start

                spectral_array, metadata = open_spectra(temp_painted)
                try:
                    if spectral_array.shape != (input_count, output_bands):
                        raise RuntimeError(
                            "Painted spectral shape does not match half-band configuration"
                        )
                    matched = int(metadata.get("matched_points", 0))
                    if not matched:
                        raise RuntimeError(
                            "No points received valid spectra; "
                            "completed cloud retained for diagnosis"
                        )
                finally:
                    spectral_array._mmap.close()

                _log(
                    f"Computing RGB display stretch from painted spectra "
                    f"(percentiles {tuple(rgb_percentiles)})"
                )
                rgb_limits = _compute_rgb_limits(
                    temp_spectra, rgb_bands, tuple(rgb_percentiles)
                )
                _log(f"RGB display limits: low={rgb_limits[0]}, high={rgb_limits[1]}")

                _log("Rewriting LAS with standard red/green/blue point-format fields")
                rewrite_start = time.perf_counter()
                _rewrite_las_with_rgb(temp_painted, rgb_bands, rgb_limits, point_chunk)
                _update_manifest_with_rgb(
                    temp_manifest, rgb_bands, rgb_limits, rgb_wavelengths_nm
                )
                rewrite_elapsed = time.perf_counter() - rewrite_start

                for src, dst in (
                    (temp_painted, painted),
                    (temp_spectra, spectra_path),
                    (temp_manifest, manifest_path),
                ):
                    src.replace(dst)
            except BaseException:
                for path in (temp_painted, temp_spectra, temp_manifest):
                    path.unlink(missing_ok=True)
                raise


        _log(
            f"Rasterize {output_bands} bands onto {resolution_cm} cm cells; "
            f"reducer={reducer}; dtype=float32"
        )
        mask = None
        if classifications is not None:
            allowed = np.asarray(classifications)
            def mask(points):
                return np.isin(np.asarray(points.classification), allowed)
        raster_start = time.perf_counter()
        rasterize_pointcloud(
            painted,
            mosaic,
            resolution=resolution,
            fields=tuple(f"hs:{i}" for i in range(output_bands)),
            reducer=reducer,
            point_chunk=point_chunk,
            tile_size=tile_size,
            dtype="float32",
            select=mask,
            scratch_directory=scratch_directory,
        )
        hyperspectral_elapsed = time.perf_counter() - raster_start

        rgb_field_names = tuple(f"hs:{b}" for b in rgb_bands)
        _log(
            f"Rasterize RGB bands {list(rgb_bands)} -> fields {list(rgb_field_names)} "
            f"onto {resolution_cm} cm cells; reducer={reducer}; dtype=float32"
        )
        rgb_start = time.perf_counter()
        rasterize_pointcloud(
            painted,
            rgb_mosaic,
            resolution=resolution,
            fields=rgb_field_names,
            reducer=reducer,
            point_chunk=point_chunk,
            tile_size=tile_size,
            dtype="float32",
            select=mask,
            scratch_directory=scratch_directory,
        )
        rgb_elapsed = time.perf_counter() - rgb_start

        report = {
            "schema_version": 1,
            "completed_at": datetime.now().astimezone().isoformat(),
            "flight_directory": str(folder),
            "input_las": str(original_source),
            "input_las_crs": (
                _describe_crs(las_crs) if las_crs is not None else None
            ),
            "las_reprojected": reprojected_path is not None,
            "las_reprojected_from": (
                _describe_crs(CRS.from_user_input(
                    laspy.open(original_source).header.parse_crs()
                )) if reprojected_path is not None else None
            ),
            "painted_las": str(painted),
            "spectra": str(spectra_path),
            "point_manifest": str(manifest_path),
            "raster": str(mosaic),
            "rgb_raster": str(rgb_mosaic),
            "input_bands": input_bands,
            "output_bands": output_bands,
            "n_points": input_count,
            "painted_points": matched,
            "source_band_pairs_zero_based": [
                list(pair) for pair in flight.spectral_groups
            ],
            "original_images_modified": False,
            "intermediate_image_cubes_created": False,
            "resolution_m": resolution,
            "resolution_cm": resolution_cm,
            "raster_dtype": "float32",
            "raster_reducer": reducer,
            "rgb_bands_zero_based": list(rgb_bands),
            "rgb_band_wavelengths_nm": rgb_wavelengths_nm,
            "rgb_field_names_in_las": ["red", "green", "blue"],
            "rgb_display_limits_low_high": [list(rgb_limits[0]), list(rgb_limits[1])],
            "rgb_display_is_radiometric_calibration": False,
            "rgb_raster_source": "hs:<band> float32 raw spectral values",
            "rgb_raster_excludes_unmatched": True,
            "classifications": (
                list(classifications) if classifications is not None else None
            ),
            "height_datum": height_datum,
            "crs": flight.crs,
            "paint_seconds": paint_elapsed,
            "rgb_rewrite_seconds": rewrite_elapsed,
            "raster_seconds": hyperspectral_elapsed,
            "rgb_raster_seconds": rgb_elapsed,
            "total_seconds": time.perf_counter() - started,
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        _log(f"Complete: {mosaic}; {rgb_mosaic}")
        return report
    finally:
        if reprojected_path is not None:
            try:
                reprojected_path.unlink(missing_ok=True)
            except OSError as exc:
                _log(
                    f"WARNING: could not delete temporary reprojected LAS "
                    f"{reprojected_path}: {exc}"
                )


def main():
    """Use the editable configuration block instead of command-line arguments."""
    run_pipeline(**PARAMETERS)


if __name__ == "__main__":
    main()
