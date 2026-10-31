from dataclasses import dataclass, field, replace
from pathlib import Path
import math
import numpy as np
from pyproj import CRS

@dataclass(frozen=True)
class CameraConfig:
    samples: int
    field_of_view_degrees: float
    boresight_degrees: tuple[float, float, float] = (0.0, 0.0, 0.0)
    lever_arm_m: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self):
        if not isinstance(self.samples, (int, np.integer)) or self.samples < 2:
            raise ValueError("samples must be an integer >= 2")
        if not 0 < self.field_of_view_degrees < 180:
            raise ValueError("FOV must be between 0 and 180 degrees")
        if len(self.boresight_degrees) != 3 or len(self.lever_arm_m) != 3:
            raise ValueError("Boresight and lever arm require three values")
        if not all(math.isfinite(v) for v in (*self.boresight_degrees, *self.lever_arm_m)):
            raise ValueError("Non-finite calibration")

@dataclass(frozen=True)
class SwathConfig:
    identifier: int
    header: Path
    binary: Path
    line_times: np.ndarray

    def __post_init__(self):
        object.__setattr__(self, "header", Path(self.header).resolve())
        object.__setattr__(self, "binary", Path(self.binary).resolve())
        times = np.array(self.line_times, dtype=np.float64, order="C", copy=True)
        if times.ndim != 1 or not len(times) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
            raise ValueError("Line times must be finite and strictly increasing")
        if not isinstance(self.identifier, (int, np.integer)) or not -(2**31) <= self.identifier < 2**31:
            raise ValueError("Swath identifier must fit int32")
        times.setflags(write=False)
        object.__setattr__(self, "line_times", times)
        for path in (self.header, self.binary):
            if not path.is_file():
                raise FileNotFoundError(path)

@dataclass(frozen=True)
class FlightConfig:
    camera: CameraConfig
    trajectory: np.ndarray
    swaths: tuple[SwathConfig, ...]
    crs: str
    height_datum: str
    time_reference: str = "gps_seconds_of_week"
    metadata: dict = field(default_factory=dict)
    spectral_groups: tuple[tuple[int, int], ...] | None = None

    def __post_init__(self):
        data = np.array(self.trajectory, dtype=np.float64, order="C", copy=True)
        if data.ndim != 2 or data.shape[1] != 7 or len(data) < 2:
            raise ValueError("Trajectory must be Nx7: time,x,y,z,roll,pitch,yaw (radians)")
        if not np.isfinite(data).all() or np.any(np.diff(data[:, 0]) <= 0):
            raise ValueError("Trajectory must be finite with increasing times")
        crs = CRS.from_user_input(self.crs)
        if not crs.is_projected or len(crs.axis_info) < 2 or any(
            not math.isclose(axis.unit_conversion_factor, 1.0) for axis in crs.axis_info[:2]
        ):
            raise ValueError("Flight requires projected metre XY coordinates")
        if self.height_datum not in {"ellipsoidal", "orthometric"}:
            raise ValueError("Declare ellipsoidal or orthometric heights")
        if self.time_reference not in {"gps_seconds_of_week", "gps_absolute", "relative_seconds"}:
            raise ValueError("Unsupported time reference")
        swaths = tuple(self.swaths)
        if not swaths or len({s.identifier for s in swaths}) != len(swaths):
            raise ValueError("Swaths must have unique identifiers")
        if self.spectral_groups is not None:
            groups = tuple(tuple(pair) for pair in self.spectral_groups)
            if not groups or any(len(pair) != 2 or any(
                not isinstance(index, (int, np.integer)) or index < 0 for index in pair
            ) for pair in groups):
                raise ValueError("spectral_groups requires nonempty pairs of nonnegative band indices")
            object.__setattr__(self, "spectral_groups", groups)
        data.setflags(write=False)
        object.__setattr__(self, "trajectory", data)
        object.__setattr__(self, "swaths", swaths)
        object.__setattr__(self, "crs", crs.to_string())

    def with_pairwise_binning(self):

        from .raster.io import read_envi_header
        counts = [int(read_envi_header(s.header)["bands"]) for s in self.swaths]
        if len(set(counts)) != 1 or counts[0] < 2 or counts[0] % 2:
            raise ValueError("Pairwise binning requires equal, positive, EVEN band counts")
        count = counts[0]
        groups = tuple((i, i+1) for i in range(0, count, 2))
        meta = dict(self.metadata)
        meta["spectral_transform"] = {"operation": "adjacent_pair_mean",
                                      "input_bands": count, "output_bands": count//2,
                                      "original_images_modified": False}
        return replace(self, spectral_groups=groups, metadata=meta)

    def prepare(self):
        from . import _native
        descriptors = [(int(s.identifier), str(s.header), str(s.binary), s.line_times.tolist())
                       for s in self.swaths]
        groups = [] if self.spectral_groups is None else [tuple(map(int, pair)) for pair in self.spectral_groups]
        return _native.Flight(self.trajectory, descriptors, int(self.camera.samples),
                              math.radians(self.camera.field_of_view_degrees),
                              *self.camera.boresight_degrees, *self.camera.lever_arm_m, groups)
