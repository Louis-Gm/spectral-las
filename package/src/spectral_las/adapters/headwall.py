from datetime import datetime
from pathlib import Path
import math
import numpy as np
from ..config import CameraConfig, FlightConfig, SwathConfig
from ..trajectory import read_trajectory, normalize_times, apply_geoid
from ..raster.io import read_envi_header, resolve_path

def _seconds(text):
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid clock time {text!r}")
    h, m, s = map(float, parts)
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 61):
        raise ValueError(f"Invalid clock time {text!r}")
    return 3600*h+60*m+s

def _date(text):
    for fmt in ("%Y/%b/%d", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date {text!r}")

def load_flight(directory, *, crs, gps_utc_offset_seconds, height_datum="ellipsoidal",
                boresight_degrees=(0.0,0.0,0.0), lever_arm_m=(0.0,0.0,0.0),
                sbet_columns=(0,2,3,7,8,9,10), trajectory_height_datum="ellipsoidal",
                allow_geoid_extrapolation=False):
    folder = Path(directory).resolve()
    settings = {}
    with (folder/"settings.txt").open(encoding="utf-8-sig") as reader:
        for line in reader:
            if "=" in line:
                key, value = line.split("=", 1)
                settings[key.strip()] = value.strip()
    width = int(settings["AOI width"])
    focal = float(settings["Lens EFL (mm)"])
    pitch = float(settings["Array Pixel Pitch (um)"])/1000
    if focal <= 0 or pitch <= 0:
        raise ValueError("Invalid focal length/pixel pitch")
    camera = CameraConfig(width, math.degrees(2*math.atan(width*pitch/(2*focal))),
                          tuple(boresight_degrees), tuple(lever_arm_m))
    imu = []
    with (folder/"imu_gps.txt").open(encoding="utf-8-sig") as reader:
        for number, line in enumerate(reader, 1):
            cells = line.split()
            if len(cells) < 14 or "Roll" in line:
                continue
            try:
                record = (_date(cells[7]), _seconds(cells[8]), _seconds(cells[11]), float(cells[10]))
            except ValueError as exc:
                raise ValueError(f"Malformed IMU row {number}") from exc
            imu.append(record)
    if not imu or not math.isfinite(gps_utc_offset_seconds):
        raise ValueError("IMU rows and an explicit GPS-UTC offset are required")
    first_date, utc, system, _ = imu[0]
    day_offset = ((first_date.weekday()+1) % 7)*86400
    drift = (system-utc+43200) % 86400-43200
    trajectory = read_trajectory(folder/"SBET.txt", columns=sbet_columns, angles="degrees", sort=True)
    if trajectory_height_datum not in {"ellipsoidal","orthometric"}:
        raise ValueError("Invalid source height datum")
    corrected = height_datum != trajectory_height_datum
    if corrected:
        if (trajectory_height_datum, height_datum) != ("ellipsoidal", "orthometric"):
            raise ValueError("Only ellipsoidal-to-orthometric correction is supported")
        geoid = {}
        for date, utc, _, sep in imu:
            time = day_offset+(date-first_date).days*86400+utc+gps_utc_offset_seconds
            if not math.isfinite(sep) or (time in geoid and geoid[time] != sep):
                raise ValueError("Invalid/conflicting geoid observation")
            geoid[time] = sep
        records = sorted(geoid.items())
        trajectory = apply_geoid(trajectory, [r[0] for r in records], [r[1] for r in records],
                                 allow_extrapolation=allow_geoid_extrapolation)
    swaths = []
    for header in sorted(folder.glob("raw_*_rd_rf.hdr")):
        identifier = int(header.name.split("_")[1])
        clocks = []
        with (folder/f"frameIndex_{identifier}.txt").open(encoding="utf-8-sig") as reader:
            for number, line in enumerate(reader, 1):
                cells = line.split()
                if not cells:
                    continue
                if len(cells) < 3:
                    raise ValueError(f"Malformed frame row {number}")
                try:
                    clocks.append(_seconds(cells[2]))
                except ValueError:
                    if number != 1:
                        raise
        if not clocks:
            raise ValueError(f"No frame times for {header}")
        times = normalize_times(clocks, rollover_seconds=86400,
                                offset_seconds=day_offset-drift+gps_utc_offset_seconds)
        times += round((trajectory[0,0]-times[0])/86400)*86400
        if len(times) != int(read_envi_header(header)["lines"]):
            raise ValueError(f"Frame/image line mismatch: {header}")
        if np.any((times < 0) | (times >= 604800)):
            raise ValueError("GPS-week rollover is unsupported by this adapter")
        swaths.append(SwathConfig(identifier, header, resolve_path(header), times))
    return FlightConfig(camera, trajectory, tuple(swaths), crs, height_datum,
                        metadata={"adapter":"headwall", "source":str(folder),
                                  "gps_utc_offset_seconds":gps_utc_offset_seconds,
                                  "clock_offset_seconds":drift, "height_correction_applied":corrected})
