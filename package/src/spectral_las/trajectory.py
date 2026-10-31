from pathlib import Path
import numpy as np

def read_trajectory(path, *, columns=(0, 1, 2, 3, 4, 5, 6), angles="radians", sort=False):
    if len(columns) != 7 or min(columns) < 0 or angles not in {"radians", "degrees"}:
        raise ValueError("Provide seven columns and angles radians/degrees")
    rows = []
    with Path(path).open(encoding="utf-8-sig") as reader:
        for number, line in enumerate(reader, 1):
            cells = line.split()
            if not cells:
                continue
            try:
                float(cells[columns[0]])
            except (ValueError, IndexError):
                continue
            try:
                row = [float(cells[i]) for i in columns]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"Malformed trajectory record at line {number}") from exc
            if not np.isfinite(row).all():
                raise ValueError(f"Non-finite trajectory record at line {number}")
            rows.append(row)
    data = np.asarray(rows, dtype=np.float64)
    if data.ndim != 2 or len(data) < 2:
        raise ValueError("Need at least two trajectory records")
    if sort:
        data = data[np.argsort(data[:, 0], kind="stable")]
    if np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError("Duplicate/decreasing trajectory times")
    if angles == "degrees":
        data[:, 4:7] = np.deg2rad(data[:, 4:7])
    return np.ascontiguousarray(data)

def normalize_times(times, *, offset_seconds=0.0, rollover_seconds=None):
    values = np.array(times, dtype=np.float64, copy=True)
    if values.ndim != 1 or not np.isfinite(values).all() or not np.isfinite(offset_seconds):
        raise ValueError("Expected finite one-dimensional times and offset")
    if rollover_seconds is not None:
        if not np.isfinite(rollover_seconds) or rollover_seconds <= 0:
            raise ValueError("Rollover period must be finite and positive")
        values += np.cumsum(np.r_[0, np.diff(values) < -rollover_seconds/2])*rollover_seconds
    values += offset_seconds
    if np.any(np.diff(values) <= 0):
        raise ValueError("Times must strictly increase after normalization")
    return values

def apply_geoid(trajectory, geoid_times, separations, *, allow_extrapolation=False):
    data = np.array(trajectory, dtype=np.float64, copy=True)
    times, sep = np.asarray(geoid_times, dtype=np.float64), np.asarray(separations, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != 7 or not len(data) or not np.isfinite(data).all():
        raise ValueError("Expected a finite Nx7 trajectory")
    if times.ndim != 1 or times.shape != sep.shape or not len(times):
        raise ValueError("Matching nonempty geoid vectors required")
    if not np.isfinite(times).all() or not np.isfinite(sep).all() or np.any(np.diff(times) <= 0):
        raise ValueError("Geoid records must be finite with increasing times")
    if not allow_extrapolation and (data[:, 0].min() < times[0] or data[:, 0].max() > times[-1]):
        raise ValueError("Geoid records do not cover trajectory")
    data[:, 3] -= np.interp(data[:, 0], times, sep)
    return data
