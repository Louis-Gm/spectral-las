from __future__ import annotations

import json
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path

import laspy
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import xy as rasterio_xy
from rasterio.windows import Window

from .operations import add_rgb_to_header


COPC_USER_ID = "copc"
COPC_INFO_RECORD_ID = 1


@dataclass(frozen=True)
class OrthoTile:
    path: Path
    crs: CRS
    transform: object
    inverse_transform: object
    width: int
    height: int
    count: int
    bounds: tuple[float, float, float, float]
    rgb_band_indexes: tuple[int, int, int]
    block_height: int
    block_width: int


class _DatasetCache:
    def __init__(self, max_open_datasets: int):
        _positive_int(max_open_datasets, "max_open_datasets")
        self._max = int(max_open_datasets)
        self._items = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.open_seconds = 0.0

    def get(self, path: Path):
        dataset = self._items.get(path)
        if dataset is not None:
            self.hits += 1
            self._items.move_to_end(path)
            return dataset
        self.misses += 1
        while len(self._items) >= self._max:
            _, old = self._items.popitem(last=False)
            old.close()
            self.evictions += 1
        started = time.perf_counter()
        dataset = rasterio.open(path)
        self.open_seconds += time.perf_counter() - started
        self._items[path] = dataset
        return dataset

    def stats(self) -> dict:
        return dict(hits=self.hits, misses=self.misses, evictions=self.evictions,
                    open_seconds=self.open_seconds, max_open_datasets=self._max)

    def close(self) -> None:
        while self._items:
            _, dataset = self._items.popitem()
            dataset.close()


class _BlockCache:


    def __init__(self, max_blocks: int = 16, max_bytes: int | None = None):
        _positive_int(max_blocks, "block_cache_size")
        if max_bytes is not None:
            _positive_int(max_bytes, "block_cache_bytes")
        self._max = int(max_blocks)
        self._max_bytes = None if max_bytes is None else int(max_bytes)
        self._items = OrderedDict()
        self.current_bytes = 0
        self.peak_bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.oversized_blocks = 0
        self.loaded_bytes = 0
        self.load_seconds = 0.0

    @staticmethod
    def _size(block) -> int:
        return int(block[0].nbytes + block[1].nbytes)

    def _evict(self) -> None:
        _, old = self._items.popitem(last=False)
        self.current_bytes -= self._size(old)
        self.evictions += 1

    def get(self, tile, block_row, block_col, datasets):
        key = (tile.path, int(block_row), int(block_col))
        cached = self._items.get(key)
        if cached is not None:
            self.hits += 1
            self._items.move_to_end(key)
            return cached
        self.misses += 1
        started = time.perf_counter()
        row_off = int(block_row) * tile.block_height
        col_off = int(block_col) * tile.block_width
        height = min(tile.block_height, tile.height - row_off)
        width = min(tile.block_width, tile.width - col_off)
        if row_off < 0 or col_off < 0 or height <= 0 or width <= 0:
            raise ValueError(f"Invalid raster block for {tile.path}: {block_row}, {block_col}")
        data = datasets.get(tile.path).read(
            indexes=tile.rgb_band_indexes,
            window=Window(col_off, row_off, width, height), masked=True,
        )
        if data.shape != (3, height, width):
            raise ValueError(f"Unexpected raster block shape for {tile.path}: {data.shape}")
        values = np.ma.getdata(data).astype(np.float32, copy=False)
        valid = ~np.ma.getmaskarray(data).any(axis=0)
        valid &= np.isfinite(values).all(axis=0)
        block = (values, valid)
        size = self._size(block)
        self.loaded_bytes += size
        self.load_seconds += time.perf_counter() - started
        if self._max_bytes is not None:
            if size > self._max_bytes:
                self.oversized_blocks += 1
                return block
            while self._items and self.current_bytes + size > self._max_bytes:
                self._evict()
        else:
            while len(self._items) >= self._max:
                self._evict()
        self._items[key] = block
        self.current_bytes += size
        self.peak_bytes = max(self.peak_bytes, self.current_bytes)
        return block

    def stats(self) -> dict:
        requests = self.hits + self.misses
        return dict(
            mode="bytes" if self._max_bytes is not None else "block_count",
            max_bytes=self._max_bytes,
            max_blocks=self._max if self._max_bytes is None else None,
            hits=self.hits, misses=self.misses, evictions=self.evictions,
            hit_rate=self.hits / requests if requests else 0.0,
            current_bytes=self.current_bytes, peak_bytes=self.peak_bytes,
            cached_blocks=len(self._items), oversized_blocks=self.oversized_blocks,
            decoded_bytes_loaded=self.loaded_bytes, load_seconds=self.load_seconds,
        )

    def close(self) -> None:
        self._items.clear()
        self.current_bytes = 0


def _resolve_rgb_band_indexes(
    dataset: rasterio.DatasetReader,
    rgb_bands: tuple[int, int, int],
) -> tuple[int, int, int]:
    if len(rgb_bands) != 3 or len(set(rgb_bands)) != 3:
        raise ValueError(
            f"rgb_bands must contain three distinct zero-based indices; got {rgb_bands}"
        )

    indexes = []
    for band in rgb_bands:
        if not isinstance(band, (int, np.integer)) or band < 0 or band >= dataset.count:
            raise ValueError(
                f"RGB band index {band} is outside 0..{dataset.count - 1} "
                f"for {dataset.name}"
            )
        indexes.append(int(band) + 1)

    return tuple(indexes)


def _preferred_rgb_bands(dataset) -> tuple[int, int, int]:
    interpretations = [getattr(value, "name", str(value)).upper()
                       for value in dataset.colorinterp]
    try:
        return tuple(interpretations.index(name) for name in ("RED", "GREEN", "BLUE"))
    except ValueError:
        if dataset.count < 3:
            raise ValueError(f"Cannot infer RGB bands from {dataset.name}: {dataset.count} bands")
        return 0, 1, 2


def _load_tile(
    path: Path,
    rgb_bands: tuple[int, int, int] | None,
) -> OrthoTile:
    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ValueError(f"Orthophoto has no CRS: {path}")

        if rgb_bands is None:
            bands = _resolve_rgb_band_indexes(dataset, _preferred_rgb_bands(dataset))
        else:
            bands = _resolve_rgb_band_indexes(dataset, rgb_bands)

        block_height, block_width = dataset.block_shapes[0]

        return OrthoTile(
            path=path.resolve(),
            crs=CRS.from_user_input(dataset.crs),
            transform=dataset.transform,
            inverse_transform=~dataset.transform,
            width=int(dataset.width),
            height=int(dataset.height),
            count=int(dataset.count),
            bounds=tuple(dataset.bounds),
            rgb_band_indexes=bands,
            block_height=int(block_height),
            block_width=int(block_width),
        )


def _build_tile_index(
    ortho_directory: Path,
    rgb_bands: tuple[int, int, int] | None,
) -> list[OrthoTile]:
    directory = Path(ortho_directory).resolve()

    if not directory.is_dir():
        raise NotADirectoryError(f"Orthophoto directory does not exist: {directory}")

    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )

    if not paths:
        raise FileNotFoundError(f"No GeoTIFF orthophotos found in {directory}")

    return [_load_tile(path, rgb_bands) for path in paths]


def _point_bounds(
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[float, float, float, float]:
    return (
        float(np.min(x)),
        float(np.min(y)),
        float(np.max(x)),
        float(np.max(y)),
    )


def _boxes_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return not (
        first[2] < second[0]
        or first[0] > second[2]
        or first[3] < second[1]
        or first[1] > second[3]
    )


def _candidate_tiles(
    tile_index: list[OrthoTile],
    x: np.ndarray,
    y: np.ndarray,
) -> list[OrthoTile]:
    if len(x) == 0:
        return []

    bounds = _point_bounds(x, y)
    return [tile for tile in tile_index if _boxes_intersect(bounds, tile.bounds)]


def _sample_tile(
    tile: OrthoTile,
    point_ids: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    colors: np.ndarray,
    datasets: _DatasetCache,
    blocks: _BlockCache,
    matched_mask: np.ndarray | None = None,
    timings: dict | None = None,
) -> int:
    """Group pixels once per tile; preserve baseline floor/mask/uint16 behavior."""
    if len(point_ids) == 0:
        return 0
    if timings is None:
        timings = defaultdict(float)
    started = time.perf_counter()
    cols_f, rows_f = rasterio_xy(tile.inverse_transform, y, x, offset="ul")
    cols_f = np.asarray(cols_f, dtype=np.float64)
    rows_f = np.asarray(rows_f, dtype=np.float64)
    finite = np.isfinite(cols_f) & np.isfinite(rows_f)
    point_ids = point_ids[finite]
    cols = np.floor(cols_f[finite]).astype(np.int64)
    rows = np.floor(rows_f[finite]).astype(np.int64)
    inside = ((cols >= 0) & (cols < tile.width)
              & (rows >= 0) & (rows < tile.height))
    point_ids, rows, cols = point_ids[inside], rows[inside], cols[inside]
    timings["pixel_coordinates"] += time.perf_counter() - started
    if len(point_ids) == 0:
        return 0

    started = time.perf_counter()
    block_rows = rows // tile.block_height
    block_cols = cols // tile.block_width
    block_columns = (tile.width + tile.block_width - 1) // tile.block_width
    block_ids = block_rows * block_columns + block_cols
    order = np.argsort(block_ids, kind="stable")
    ordered_blocks = block_ids[order]
    edges = np.concatenate(([0], np.flatnonzero(np.diff(ordered_blocks)) + 1,
                            [len(order)]))
    point_ids, rows, cols = point_ids[order], rows[order], cols[order]
    timings["block_grouping"] += time.perf_counter() - started
    matched = 0
    for first, last in zip(edges[:-1], edges[1:]):
        block_row, block_col = divmod(int(ordered_blocks[first]), block_columns)
        values, valid = blocks.get(tile, block_row, block_col, datasets)
        started = time.perf_counter()
        local_rows = rows[first:last] - block_row * tile.block_height
        local_cols = cols[first:last] - block_col * tile.block_width
        if (np.any(local_rows < 0) or np.any(local_rows >= values.shape[1])
                or np.any(local_cols < 0) or np.any(local_cols >= values.shape[2])):
            raise RuntimeError(f"Pixel request escaped raster block for {tile.path}")
        valid_points = valid[local_rows, local_cols]
        ids = point_ids[first:last][valid_points]
        if len(ids):
            rr, cc = local_rows[valid_points], local_cols[valid_points]
            colors[ids, 0] = values[0, rr, cc]
            colors[ids, 1] = values[1, rr, cc]
            colors[ids, 2] = values[2, rr, cc]
            if matched_mask is not None:
                matched_mask[ids] = True
            matched += len(ids)
        timings["pixel_gather_and_scatter"] += time.perf_counter() - started
    return matched


def _encode_rgb(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values)

    if values.dtype == np.uint16:
        return values

    if values.dtype == np.uint8:
        return values.astype(np.uint16) * np.uint16(257)

    if np.issubdtype(values.dtype, np.floating):
        minimum = float(np.nanmin(values))
        maximum = float(np.nanmax(values))

        if minimum >= 0.0 and maximum <= 1.0:
            return np.rint(np.clip(values, 0.0, 1.0) * 65535.0).astype(np.uint16)

        if minimum >= 0.0 and maximum <= 255.0:
            return np.rint(np.clip(values, 0.0, 255.0) * 257.0).astype(np.uint16)

        if minimum >= 0.0 and maximum <= 65535.0:
            return np.rint(np.clip(values, 0.0, 65535.0)).astype(np.uint16)

    raise ValueError(
        f"Unsupported RGB dtype/range for LAS encoding: dtype={values.dtype}, "
        f"min={minimum}, max={maximum}"
    )


def _copy_chunk_to_output(
    chunk: laspy.ScaleAwarePointRecord,
    header: laspy.LasHeader,
    colors: np.ndarray,
) -> laspy.ScaleAwarePointRecord:
    output = laspy.ScaleAwarePointRecord.zeros(len(chunk), header=header)

    for name in chunk.point_format.dimension_names:
        output[name] = chunk[name]

    output.red = colors[:, 0]
    output.green = colors[:, 1]
    output.blue = colors[:, 2]

    return output


def _is_copc_info_vlr(vlr: object) -> bool:
    user_id = str(getattr(vlr, "user_id", "")).strip().lower()
    record_id = getattr(vlr, "record_id", None)

    return user_id == COPC_USER_ID and record_id == COPC_INFO_RECORD_ID

def _strip_copc_for_plain_laz(header: laspy.LasHeader) -> laspy.LasHeader:


    header.vlrs = [
        vlr
        for vlr in header.vlrs
        if not _is_copc_info_vlr(vlr)
    ]

    return header


def _select_laz_backend():
    available = laspy.LazBackend.detect_available()

    if not available:
        raise RuntimeError(
            "No LAZ backend is installed. Install one with:\n"
            '  pip install "laspy[lazrs]"\n'
            "or write the output with a .las extension instead."
        )

    return available[0]


def _positive_int(value, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _group_tiles_by_crs(tiles):
    groups = []
    for index, tile in enumerate(tiles):
        for crs, members in groups:
            if tile.crs == crs:
                members.append((index, tile))
                break
        else:
            groups.append((tile.crs, [(index, tile)]))
    return groups


def _chunk_candidates(groups, input_crs, assume_same_crs, x, y, transformers, timings):

    candidates = []
    for group_number, (crs, members) in enumerate(groups):
        started = time.perf_counter()
        if assume_same_crs or input_crs == crs:
            sample_x, sample_y = x, y
        else:
            transformer = transformers.get(group_number)
            if transformer is None:
                transformer = Transformer.from_crs(input_crs, crs, always_xy=True)
                transformers[group_number] = transformer
            sample_x, sample_y = transformer.transform(x, y)
            sample_x = np.asarray(sample_x, dtype=np.float64)
            sample_y = np.asarray(sample_y, dtype=np.float64)
        timings["crs_transform"] += time.perf_counter() - started
        started = time.perf_counter()
        finite = np.isfinite(sample_x) & np.isfinite(sample_y)
        if np.any(finite):
            bounds = _point_bounds(sample_x[finite], sample_y[finite])
            for index, tile in members:
                if _boxes_intersect(bounds, tile.bounds):
                    candidates.append((index, tile, sample_x, sample_y))
        timings["candidate_filter"] += time.perf_counter() - started
    candidates.sort(key=lambda candidate: candidate[0])
    return candidates


def _paint_chunk(tiles, groups, input_crs, assume_same_crs, x, y,
                 unmatched, datasets, blocks, transformers, timings):
    colors = np.empty((len(x), 3), dtype=np.uint16)
    colors[:] = unmatched
    matched_mask = np.zeros(len(x), dtype=bool)
    per_tile = np.zeros(len(tiles), dtype=np.int64)
    candidates = _chunk_candidates(groups, input_crs, assume_same_crs,
                                   x, y, transformers, timings)
    for index, tile, sample_x, sample_y in candidates:
        started = time.perf_counter()
        min_x, min_y, max_x, max_y = tile.bounds
        inside = ((sample_x >= min_x) & (sample_x <= max_x)
                  & (sample_y >= min_y) & (sample_y <= max_y)
                  & np.isfinite(sample_x) & np.isfinite(sample_y))
        ids = np.flatnonzero(inside)
        timings["point_bounds_filter"] += time.perf_counter() - started
        if len(ids):
            started = time.perf_counter()
            per_tile[index] = _sample_tile(
                tile, ids, sample_x[ids], sample_y[ids], colors, datasets,
                blocks, matched_mask=matched_mask, timings=timings,
            )
            timings["tile_sampling_total"] += time.perf_counter() - started
    return colors, int(np.count_nonzero(matched_mask)), per_tile, len(candidates)


def paint_rgb_from_orthos(
    source: str | Path,
    output: str | Path,
    ortho_directory: str | Path,
    rgb_bands: tuple[int, int, int] | None = None,
    point_chunk: int = 250_000,
    unmatched_rgb: tuple[int, int, int] = (0, 0, 0),
    assume_same_crs: bool = False,
    max_open_datasets: int = 8,
    block_cache_size: int = 16,
    block_cache_mb: float | None = 512.0,
    log_every_chunks: int = 1,
) -> dict:

















    started = time.perf_counter()
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    directory = Path(ortho_directory).resolve()
    sidecar = output_path.with_suffix(output_path.suffix + ".rgb.json")
    if source_path == output_path:
        raise ValueError("source and output must be different paths")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not output_path.parent.is_dir():
        raise NotADirectoryError(output_path.parent)
    if output_path.suffix.lower() not in {".las", ".laz"}:
        raise ValueError("output must have a .las or .laz extension")
    if output_path.exists() or sidecar.exists():
        raise FileExistsError(f"Refusing to overwrite output or sidecar: {output_path}")
    _positive_int(point_chunk, "point_chunk")
    _positive_int(max_open_datasets, "max_open_datasets")
    _positive_int(block_cache_size, "block_cache_size")
    _positive_int(log_every_chunks, "log_every_chunks")
    cache_bytes = None
    if block_cache_mb is not None:
        if isinstance(block_cache_mb, bool) or not np.isfinite(block_cache_mb) or block_cache_mb <= 0:
            raise ValueError("block_cache_mb must be finite and positive, or None")
        cache_bytes = int(float(block_cache_mb) * 1024 * 1024)
        _positive_int(cache_bytes, "block_cache_bytes")
    raw_unmatched = np.asarray(unmatched_rgb)
    if (raw_unmatched.shape != (3,) or raw_unmatched.dtype.kind not in "iu"
            or np.any(raw_unmatched < 0) or np.any(raw_unmatched > 65535)):
        raise ValueError("unmatched_rgb must contain three integers in 0..65535")
    unmatched = raw_unmatched.astype(np.uint16)
    timings = defaultdict(float)
    setup_started = time.perf_counter()
    tiles = _build_tile_index(directory, rgb_bands)
    groups = _group_tiles_by_crs(tiles)
    datasets = _DatasetCache(max_open_datasets)
    blocks = _BlockCache(block_cache_size, max_bytes=cache_bytes)
    compressed = output_path.suffix.lower() == ".laz"
    backend = _select_laz_backend() if compressed else None
    timings["setup"] += time.perf_counter() - setup_started
    total_points = 0
    matched_points = 0
    tile_sample_count = 0
    tile_counts = np.zeros(len(tiles), dtype=np.int64)
    candidate_tile_checks = 0
    chunks = 0
    transformers = {}
    created_output = False
    created_sidecar = False
    success = False
    try:
        open_started = time.perf_counter()
        with laspy.open(source_path) as reader:
            input_count = int(reader.header.point_count)
            input_crs = reader.header.parse_crs()
            if input_count == 0:
                raise ValueError(f"Input contains zero points: {source_path}")
            if input_crs is None and not assume_same_crs:
                raise ValueError("Input CRS missing; assign its actual CRS, or explicitly "
                                 "set assume_same_crs=True for already matching XY coordinates")
            if input_crs is not None:
                input_crs = CRS.from_user_input(input_crs)
            header = add_rgb_to_header(reader.header.copy())
            if compressed:
                header = _strip_copc_for_plain_laz(header)
            timings["source_open_and_header"] += time.perf_counter() - open_started
            writer_options = {"laz_backend": backend} if compressed else {}

            with output_path.open("xb") as output_stream:
                created_output = True
                open_started = time.perf_counter()
                with laspy.open(output_stream, mode="w", header=header,
                                closefd=False, do_compress=compressed,
                                **writer_options) as writer:
                    timings["writer_open"] += time.perf_counter() - open_started
                    print(f"Input: {source_path} | points={input_count:,} | "
                          f"tiles={len(tiles)} | chunk={point_chunk:,} | "
                          f"backend={backend} | execution=sequential", flush=True)
                    iterator = iter(reader.chunk_iterator(int(point_chunk)))
                    while True:
                        chunk_started = time.perf_counter()
                        read_started = time.perf_counter()
                        try:
                            chunk = next(iterator)
                        except StopIteration:
                            timings["point_read"] += time.perf_counter() - read_started
                            break
                        read_seconds = time.perf_counter() - read_started
                        timings["point_read"] += read_seconds
                        chunks += 1
                        coord_started = time.perf_counter()
                        x = np.asarray(chunk.x, dtype=np.float64)
                        y = np.asarray(chunk.y, dtype=np.float64)
                        timings["point_xy_extract"] += time.perf_counter() - coord_started
                        paint_started = time.perf_counter()
                        colors, unique_matches, per_tile, candidate_count = _paint_chunk(
                            tiles, groups, input_crs, assume_same_crs, x, y, unmatched,
                            datasets, blocks, transformers, timings,
                        )
                        paint_seconds = time.perf_counter() - paint_started
                        timings["painting_total"] += paint_seconds
                        copy_started = time.perf_counter()
                        output_points = _copy_chunk_to_output(chunk, header, colors)
                        timings["point_record_copy"] += time.perf_counter() - copy_started
                        write_started = time.perf_counter()
                        writer.write_points(output_points)
                        write_seconds = time.perf_counter() - write_started
                        timings["point_write"] += write_seconds
                        total_points += len(chunk)
                        matched_points += unique_matches
                        tile_counts += per_tile
                        tile_sample_count += int(per_tile.sum())
                        candidate_tile_checks += candidate_count
                        if total_points > input_count:
                            raise RuntimeError("Read more points than the input header declares")
                        if chunks % log_every_chunks == 0 or total_points == input_count:
                            elapsed = time.perf_counter() - started
                            throughput = total_points / elapsed if elapsed else 0.0
                            eta = (input_count - total_points) / throughput if throughput else 0.0
                            print(
                                f"chunk {chunks}: {len(chunk):,} points, "
                                f"{unique_matches:,} uniquely RGB-matched | "
                                f"{total_points:,}/{input_count:,} ({100*total_points/input_count:.1f}%) | "
                                f"read={read_seconds:.3f}s paint={paint_seconds:.3f}s "
                                f"write={write_seconds:.3f}s "
                                f"chunk_total={time.perf_counter()-chunk_started:.3f}s | "
                                f"cache={blocks.hits:,} hits/{blocks.misses:,} misses | "
                                f"{throughput:,.0f} pts/s ETA={eta/60:.1f}min", flush=True,
                            )
                        del chunk, output_points, colors, x, y
                    finalize_started = time.perf_counter()
                timings["writer_finalize"] += time.perf_counter() - finalize_started
            if total_points != input_count:
                raise RuntimeError(f"Point count changed: expected {input_count}, wrote {total_points}")
        summary = {
            "schema_version": 2,
            "operation": "paint_rgb_from_orthos",
            "source": str(source_path), "output": str(output_path),
            "ortho_directory": str(directory),
            "input_points": input_count, "output_points": total_points,
            "rgb_matched_points": matched_points,
            "rgb_unmatched_points": total_points - matched_points,
            "successful_tile_samples": tile_sample_count,
            "overlap_resamples": tile_sample_count - matched_points,
            "matching_count_semantics": "unique source points with at least one valid tile sample",
            "tile_count_semantics": "successful samples per tile, including overwritten samples",
            "overlap_policy": "last valid tile in sorted filename order wins",
            "rgb_encoding": "baseline float32 values assigned directly to uint16; no stretching",
            "rgb_band_indexes_zero_based": [b - 1 for b in tiles[0].rgb_band_indexes],
            "unmatched_rgb_uint16": unmatched.tolist(),
            "input_crs": input_crs.to_string() if input_crs is not None else None,
            "assume_same_crs": bool(assume_same_crs),
            "candidate_filter_crs": "tile CRS; chunk transformed once per distinct tile CRS",
            "point_chunk": int(point_chunk), "chunks": chunks,
            "candidate_tile_visits": candidate_tile_checks,
            "execution": "sequential grouped block sampling; no multiprocessing",
            "output_is_compressed": compressed, "output_is_copc": False,
            "laz_backend": str(backend) if backend is not None else None,
            "block_cache_mb": block_cache_mb,
            "block_cache": blocks.stats(), "dataset_cache": datasets.stats(),
            "stage_timings_seconds": dict(timings),
            "timing_semantics": {
                "painting_total": "inclusive of CRS, filtering, tile sampling and buffer setup",
                "tile_sampling_total": "inclusive of pixel coordinates, grouping, block loads and scatter",
                "block_cache.load_seconds": "inclusive of dataset opening, raster reads and mask conversion",
                "point_write": "includes compression work performed during write_points",
                "writer_finalize": "includes remaining compression and header finalization",
            },
            "tiles": [dict(path=str(tile.path), crs=tile.crs.to_string(),
                           width=tile.width, height=tile.height,
                           block_width=tile.block_width, block_height=tile.block_height,
                           rgb_band_indexes_rasterio_one_based=list(tile.rgb_band_indexes),
                           matched_points=int(tile_counts[index]))
                      for index, tile in enumerate(tiles)],
            "elapsed_seconds": time.perf_counter() - started,
        }
        summary["points_per_second"] = total_points / summary["elapsed_seconds"]
        with sidecar.open("x", encoding="utf-8") as stream:
            created_sidecar = True
            json.dump(summary, stream, indent=2)
            stream.write("\n")
        success = True
        return summary
    finally:
        datasets.close()
        blocks.close()
        if not success:
            if created_output:
                output_path.unlink(missing_ok=True)
            if created_sidecar:
                sidecar.unlink(missing_ok=True)
