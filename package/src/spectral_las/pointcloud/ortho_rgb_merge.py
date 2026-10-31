from __future__ import annotations
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

MERGE_ALGORITHM = "rgb-integer-grid-merge-v1"
IMPLEMENTATION_VERSION = 2
AXES = ("X", "Y", "Z")


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _file_signature(path):
    path = Path(path).resolve()
    value = path.stat()
    return dict(path=str(path), size=value.st_size, mtime_ns=value.st_mtime_ns)


def _write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _publish(stage, target):
    if os.name == "nt":
        os.rename(stage, target)
    else:
        os.link(stage, target)
        os.unlink(stage)


@contextmanager
def _lock(path):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(dict(pid=os.getpid(), created_utc=datetime.now(timezone.utc).isoformat()), stream)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _versions():
    result = {"python": sys.version.split()[0]}
    for package in ("numpy", "laspy", "lazrs", "laszip"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def _coordinate_plan(headers):
    import numpy as np
    scales=np.array([h.scales for h in headers],dtype=np.float64)
    offsets=np.array([h.offsets for h in headers],dtype=np.float64)
    mins=np.array([h.mins for h in headers],dtype=np.float64);maxs=np.array([h.maxs for h in headers],dtype=np.float64)
    if not all(np.isfinite(a).all() for a in (scales,offsets,mins,maxs)) or np.any(scales<=0): raise ValueError("Invalid coordinate headers")
    target=scales.min(axis=0);low=mins.min(axis=0);high=maxs.max(axis=0);anchor=offsets[0]
    origin=anchor+np.rint((low+(high-low)/2-anchor)/target)*target
    ratios=scales/target;shifts=(offsets-origin)/target
    if np.any(ratios>(2**63-1)//2**31) or np.any(np.abs(shifts)>2**62): raise ValueError("Unsafe coordinate conversion range")
    if not np.allclose(ratios,np.rint(ratios),rtol=0,atol=1e-7): raise ValueError("Input scales do not share an integer grid")
    if not np.allclose(shifts,np.rint(shifts),rtol=0,atol=1e-5): raise ValueError("Input offsets do not align on a common grid")
    if np.any((low-origin)/target < -2**31-.01) or np.any((high-origin)/target > 2**31-1+.01):
        raise ValueError("Merged extent exceeds signed int32 at existing precision; no automatic coarsening is allowed")
    return target,origin,np.rint(ratios).astype(np.int64),np.rint(shifts).astype(np.int64)

def _extra_schema(header):
    return tuple((v.user_id,v.record_id,bytes(v.record_data_bytes())) for v in header.vlrs if v.user_id=="LASF_Spec" and v.record_id==4)

class _IntegerRemapper:


    def __init__(self, capacity):
        import numpy as np
        self.scratch = np.empty(capacity, dtype=np.int64)
        self.counts = dict(identity_axes=0, translation_axes=0, affine_axes=0)

    def apply(self, chunk, ratios, shifts, timings):
        import numpy as np
        n = len(chunk)
        if n > len(self.scratch):
            raise ValueError("Chunk exceeds remap scratch capacity")
        scratch = self.scratch[:n]
        for axis, name in enumerate(AXES):
            ratio, shift = int(ratios[axis]), int(shifts[axis])
            if ratio < 1:
                raise ValueError("Coordinate scale ratio must be a positive integer")
            if ratio == 1 and shift == 0:
                self.counts["identity_axes"] += 1
                continue
            started = time.perf_counter()
            raw = np.asarray(chunk[name])
            if raw.dtype.kind != "i" or raw.dtype.itemsize != 4:
                raise ValueError(f"Expected signed int32 field {name}")
            lower = -((-(-2**31 - shift)) // ratio)
            upper = (2**31 - 1 - shift) // ratio
            if n and (int(raw.min()) < lower or int(raw.max()) > upper):
                raise ValueError(f"Coordinate overflows int32 in field {name}")
            timings["coordinate_validation"] += time.perf_counter() - started
            started = time.perf_counter()
            np.copyto(scratch, raw, casting="safe")
            if ratio != 1:
                np.multiply(scratch, ratio, out=scratch)
            if shift:
                np.add(scratch, shift, out=scratch)

            np.copyto(raw, scratch, casting="unsafe")
            self.counts["translation_axes" if ratio == 1 else "affine_axes"] += 1
            timings["coordinate_remap"] += time.perf_counter() - started


def _check_headers(first, candidate, path):
    if candidate.point_format.id in {4, 5, 9, 10}:
        raise ValueError("Waveform-reference formats are not supported by this strict merger")
    if not {"red", "green", "blue"}.issubset(candidate.point_format.dimension_names):
        raise ValueError(f"RGB missing: {path}")
    crs = candidate.parse_crs()
    if crs is None:
        raise ValueError(f"CRS missing: {path}")
    if first is None:
        return
    if (candidate.point_format.id != first.point_format.id
            or candidate.point_format.dtype() != first.point_format.dtype()):
        raise ValueError(f"Point schema differs: {path}")
    if (tuple(candidate.point_format.dimension_names) != tuple(first.point_format.dimension_names)
            or _extra_schema(candidate) != _extra_schema(first)):
        raise ValueError(f"Extra-dimension schema differs: {path}")
    if crs != first.parse_crs():
        raise ValueError(f"CRS differs: {path}")
    if candidate.global_encoding.value != first.global_encoding.value:
        raise ValueError(f"Global encoding flags differ: {path}")


def _merge_in_process(paths, output, chunk_points, native_threads, resume,
                      log_every_chunks, progress_interval_seconds):
    overall_start = time.perf_counter()
    cpu_start = time.process_time()
    started = time.perf_counter()
    import laspy
    import numpy as np
    from laspy.header import Version
    timings = defaultdict(float)
    timings["dependency_imports"] = time.perf_counter() - started
    available = laspy.LazBackend.detect_available()
    if not available:
        raise RuntimeError('Install LAZ support: pip install "laspy[lazrs]"')
    preferred = laspy.LazBackend.Lazrs if native_threads == 1 else laspy.LazBackend.LazrsParallel
    backend = preferred if preferred in available else available[0]
    marker = output.with_suffix(output.suffix + ".merge.json")
    started = time.perf_counter()
    identity = dict(algorithm=MERGE_ALGORITHM, inputs=[_file_signature(p) for p in paths])
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    timings["input_signatures"] += time.perf_counter() - started
    with _lock(output.with_suffix(output.suffix + ".merge.lock")):
        if output.exists() or marker.exists():
            if not resume or not output.is_file() or not marker.is_file():
                raise FileExistsError(f"Existing/incomplete merged output: {output}")
            old = json.loads(marker.read_text(encoding="utf-8"))
            if (not old.get("completed") or old.get("fingerprint") != digest
                    or old.get("output_signature") != _file_signature(output)):
                raise FileExistsError("Existing merge differs from the current selected inputs")
            with laspy.open(output, read_evlrs=False) as reader:
                if reader.header.point_count != old["total_points"]:
                    raise ValueError("Merged header count changed")
            print(f"MERGE SKIPPED: verified {output.name} | {old['total_points']:,} points", flush=True)
            return dict(old, skipped=True)
        print(f"MERGE PREFLIGHT: {len(paths):,} tiles -> {output.name}", flush=True)
        print(f"MERGE backend={backend} requested_native_threads={native_threads} "
              f"RAYON_NUM_THREADS={os.environ.get('RAYON_NUM_THREADS', '<unset>')} "
              f"chunk_points={chunk_points:,} pid={os.getpid()}", flush=True)
        started = time.perf_counter()
        headers = []
        total = 0
        first = None
        for i, path in enumerate(paths, 1):
            with laspy.open(path, laz_backend=backend) as reader:
                header = reader.header.copy()
            _check_headers(first, header, path)
            if first is None:
                first = header
            headers.append(header)
            total += int(header.point_count)
            if i % 1000 == 0:
                print(f"MERGE headers {i:,}/{len(paths):,}", flush=True)
        if not 0 < total <= 2**64 - 1:
            raise ValueError("Invalid total point count")
        scales, offsets, ratios, shifts = _coordinate_plan(headers)
        merged_header = first.copy()
        merged_header.set_version_and_point_format(Version(1, 4), merged_header.point_format)
        merged_header.scales, merged_header.offsets = scales, offsets
        merged_header.vlrs = [v for v in merged_header.vlrs
                             if str(getattr(v, "user_id", "")).strip().lower() != "copc"]
        evlrs = merged_header.evlrs
        if evlrs is not None:
            evlrs = type(evlrs)([v for v in evlrs
                                if str(getattr(v, "user_id", "")).strip().lower() != "copc"])
            merged_header.evlrs = evlrs
        timings["header_preflight_and_grid"] += time.perf_counter() - started
        remapper = _IntegerRemapper(min(chunk_points, max(int(h.point_count) for h in headers)))
        print(f"MERGE remap scratch={remapper.scratch.nbytes / 1024**2:.2f} MiB; "
              "validation retained; identity axes skipped", flush=True)
        work = output.parent / (".merge-work-" + uuid.uuid4().hex)
        work.mkdir()
        stage = work / "merged.laz"
        written = chunks = 0
        stream_start = last_progress = time.perf_counter()
        try:
            started = time.perf_counter()
            with laspy.open(stage, mode="w", header=merged_header, do_compress=True,
                            laz_backend=backend) as writer:
                timings["writer_open"] += time.perf_counter() - started
                for index, path in enumerate(paths):
                    started = time.perf_counter()
                    with laspy.open(path, laz_backend=backend) as reader:
                        timings["input_open"] += time.perf_counter() - started
                        tile_written = 0
                        expected = int(headers[index].point_count)
                        if reader.header.point_count != expected:
                            raise RuntimeError(f"Input point count changed: {path}")
                        while tile_written < expected:
                            started = time.perf_counter()
                            chunk = reader.read_points(min(chunk_points, expected - tile_written))
                            timings["read_decompress"] += time.perf_counter() - started
                            if not len(chunk):
                                raise RuntimeError(f"Truncated input stream: {path}")
                            remapper.apply(chunk, ratios[index], shifts[index], timings)
                            started = time.perf_counter()
                            chunk.scales, chunk.offsets = scales.copy(), offsets.copy()
                            timings["coordinate_metadata"] += time.perf_counter() - started
                            started = time.perf_counter()
                            writer.write_points(chunk)
                            timings["write_compress"] += time.perf_counter() - started
                            n = len(chunk)
                            written += n
                            tile_written += n
                            chunks += 1
                            now = time.perf_counter()
                            if (now - last_progress >= progress_interval_seconds
                                    or (log_every_chunks and chunks % log_every_chunks == 0)):
                                rate = written / max(now - stream_start, 1e-9)
                                print(f"MERGE chunk {chunks}: {written:,}/{total:,} "
                                      f"({100*written/total:.1f}%) | {rate:,.0f} pts/s | "
                                      f"read={timings['read_decompress']:.2f}s "
                                      f"check={timings['coordinate_validation']:.2f}s "
                                      f"remap={timings['coordinate_remap']:.2f}s "
                                      f"write={timings['write_compress']:.2f}s cumulative | "
                                      f"ETA={(total-written)/max(rate,1e-9):.1f}s", flush=True)
                                last_progress = now
                            del chunk
                        close_started = time.perf_counter()
                    timings["input_close"] += time.perf_counter() - close_started
                    print(f"MERGE tile {index+1}/{len(paths)}: {path.name} | "
                          f"{written:,}/{total:,} points", flush=True)
                if evlrs:
                    started = time.perf_counter()
                    writer.write_evlrs(evlrs)
                    timings["evlr_write"] += time.perf_counter() - started
                finalize_started = time.perf_counter()
            timings["writer_finalize"] += time.perf_counter() - finalize_started
            started = time.perf_counter()
            if written != total:
                raise RuntimeError("Merged point count mismatch")
            if [_file_signature(p) for p in paths] != identity["inputs"]:
                raise RuntimeError("Merge input changed during processing")
            with laspy.open(stage, read_evlrs=False) as verify:
                if verify.header.point_count != written:
                    raise RuntimeError("Merged header count mismatch")
            timings["verification_and_signatures"] += time.perf_counter() - started
            started = time.perf_counter()
            _publish(stage, output)
            timings["data_publication"] += time.perf_counter() - started
            elapsed = time.perf_counter() - overall_start
            cpu = time.process_time() - cpu_start
            summary = dict(
                operation="merge_rgb_laz_tiles", completed=True, implementation_version=IMPLEMENTATION_VERSION,
                fingerprint=digest, identity=identity, output=str(output),
                output_signature=_file_signature(output), total_tiles_merged=len(paths), total_points=written,
                scales=scales.tolist(), offsets=offsets.tolist(), elapsed_seconds=elapsed,
                process_cpu_seconds=cpu, cpu_seconds_per_wall_second=cpu/max(elapsed, 1e-9),
                points_per_second=written/max(elapsed, 1e-9), output_is_copc=False,
                stage_timings_seconds=dict(timings), chunks=chunks, chunk_points=chunk_points,
                point_record_bytes=int(first.point_format.size), scratch_bytes=int(remapper.scratch.nbytes),
                coordinate_fast_paths=dict(remapper.counts), laz_backend=str(backend),
                requested_native_threads=native_threads, rayon_num_threads=os.environ.get("RAYON_NUM_THREADS"),
                versions=_versions(), input_compressed_bytes=sum(v["size"] for v in identity["inputs"]),
                coordinate_policy="Common integer grid; refuses incompatible grids or int32 overflow",
                metadata_policy="Point schemas must agree; other metadata retained from first tile",
                timing_note="Nonoverlapping wall-duration stages; read includes decode/allocation; "
                            "write includes laspy header statistics, compression and I/O; "
                            "finalization/EVLR writing may include delayed codec work. "
                            "No process-RAM bound or native-thread-count measurement is claimed.",
            )
            staged_marker = work / "merge.json"
            _write_json(staged_marker, summary)
            _publish(staged_marker, marker)
            print(f"MERGE COMPLETE: {written:,} points | {elapsed:.2f}s | "
                  f"{summary['points_per_second']:,.0f} pts/s | process CPU={cpu:.2f}s", flush=True)
            print(f"MERGE stage seconds: {dict(timings)}", flush=True)
            print(f"MERGE coordinate paths: {remapper.counts}", flush=True)
            return summary
        finally:
            shutil.rmtree(work, ignore_errors=True)


def merge_rgb_laz_tiles(tile_paths, output_path, chunk_points=2_000_000,
                        native_threads=2, resume=True, *, run_in_subprocess=True,
                        log_every_chunks=10, progress_interval_seconds=5.0):
    """Drop-in merge API; fresh-process thread initialization is enabled by default.

    Existing compatible .merge.json markers remain resumable. Change the output
    filename to benchmark: resuming a completed merge deliberately skips its work.
    The fresh subprocess inherits terminal output. No image painting is performed.
    """
    _positive_int(chunk_points, "chunk_points")
    _positive_int(native_threads, "native_threads")
    if isinstance(log_every_chunks, bool) or not isinstance(log_every_chunks, int) or log_every_chunks < 0:
        raise ValueError("log_every_chunks must be a nonnegative integer")
    import math
    if not math.isfinite(progress_interval_seconds) or progress_interval_seconds <= 0:
        raise ValueError("progress_interval_seconds must be finite and positive")
    paths = [Path(p).resolve() for p in tile_paths]
    output = Path(output_path).resolve()
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("Merge requires nonempty unique input paths")
    if output in paths or output.suffix.lower() != ".laz":
        raise ValueError("Invalid merged output")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    arguments = dict(paths=[str(p) for p in paths], output=str(output), chunk_points=chunk_points,
                     native_threads=native_threads, resume=bool(resume), log_every_chunks=log_every_chunks,
                     progress_interval_seconds=progress_interval_seconds)
    if not run_in_subprocess:
        arguments["paths"], arguments["output"] = paths, output
        return _merge_in_process(**arguments)
    job = output.parent / (".merge-job-" + uuid.uuid4().hex)
    job.mkdir()
    request, response = job / "request.json", job / "result.json"
    arguments["result_file"] = str(response)
    _write_json(request, arguments)
    environment = os.environ.copy()
    for key in ("RAYON_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "GDAL_NUM_THREADS"):
        environment[key] = str(native_threads)
    child = None
    started = time.perf_counter()
    try:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                  "--job-file", str(request)], env=environment)
        code = child.wait()
        if not response.is_file():
            raise RuntimeError(f"Merge process exited with code {code} without a result; "
                               "inspect terminal output and any stale merge lock")
        payload = json.loads(response.read_text(encoding="utf-8"))
        if code or not payload.get("ok"):
            message = payload.get("error", f"Merge process exited with code {code}")
            kind = payload.get("error_type")
            cls = {"ValueError": ValueError, "FileExistsError": FileExistsError,
                   "FileNotFoundError": FileNotFoundError}.get(kind, RuntimeError)
            raise cls(message + "\n" + payload.get("traceback", ""))
        result = payload["summary"]
        result["run_in_subprocess"] = True
        result["subprocess_wall_seconds"] = time.perf_counter() - started
        return result
    except BaseException:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise
    finally:
        shutil.rmtree(job, ignore_errors=True)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-file", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.job_file.read_text(encoding="utf-8"))
    result_file = Path(data.pop("result_file"))
    data["paths"] = [Path(p) for p in data["paths"]]
    data["output"] = Path(data["output"])
    try:
        summary = _merge_in_process(**data)
        _write_json(result_file, dict(ok=True, summary=summary))
    except Exception as error:
        detail = traceback.format_exc()
        _write_json(result_file, dict(ok=False, error_type=type(error).__name__,
                                      error=str(error), traceback=detail))
        print(detail, file=sys.stderr, flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
