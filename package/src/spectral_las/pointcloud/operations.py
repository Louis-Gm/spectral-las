from pathlib import Path
import json
import tempfile
import numpy as np
from numpy.lib.format import open_memmap
import laspy
from .io import companion_paths,open_spectra
from ..raster.operations import _Moments

def transform_points(source,output,*,select=None,transform=None,extra_dimensions=(),
                     output_header=None,point_chunk=100_000):

    source,output = Path(source).resolve(),Path(output).resolve()
    spectral_path,manifest_path = companion_paths(output)
    if source == output or any(p.exists() for p in (output,spectral_path,manifest_path)):
        raise FileExistsError(output)
    if not isinstance(point_chunk,int) or point_chunk < 1:
        raise ValueError("point_chunk must be a positive integer")
    spectra,metadata = (open_spectra(source) if companion_paths(source)[0].exists() else (None,None))
    offset = written = 0
    mapped = None
    try:
        with tempfile.TemporaryDirectory(prefix="spectral_las_transform_",dir=output.parent) as scratch:
            raw_path = Path(scratch)/"spectra.raw"
            with raw_path.open("wb") as raw,laspy.open(source) as reader:
                header = reader.header.copy() if output_header is None else output_header.copy()
                if not np.array_equal(header.scales,reader.header.scales) or not np.array_equal(header.offsets,reader.header.offsets):
                    raise ValueError("Coordinate scales/offsets must be preserved")
                original = set(reader.header.point_format.dimension_names)
                if not original.issubset(set(header.point_format.dimension_names)):
                    raise ValueError("Output header would lose source fields")
                for name,dtype in extra_dimensions:
                    if name in header.point_format.dimension_names:
                        raise ValueError(f"Dimension already exists: {name}")
                    header.add_extra_dim(laspy.ExtraBytesParams(name=name,type=np.dtype(dtype)))
                with laspy.open(output,mode="w",header=header) as writer:
                    for chunk in reader.chunk_iterator(point_chunk):
                        n = len(chunk)
                        mask = np.ones(n,dtype=bool) if select is None else np.asarray(select(chunk))
                        if mask.shape != (n,) or mask.dtype != np.bool_:
                            raise ValueError("select must return a boolean vector matching the chunk")
                        updates = {} if transform is None else transform(chunk)
                        record = laspy.ScaleAwarePointRecord.zeros(n,header=header)
                        for name in original:
                            record[name] = chunk[name]
                        for name,value in updates.items():
                            if name not in set(header.point_format.dimension_names) | {"x","y","z"}:
                                raise ValueError(f"Unknown output field: {name}")
                            value = np.asarray(value)
                            if value.shape not in {(),(n,)}:
                                raise ValueError(f"Invalid field shape: {name}")
                            record[name] = value
                        count = int(mask.sum())
                        if count:
                            writer.write_points(record[mask])
                            if spectra is not None:
                                np.ascontiguousarray(spectra[offset:offset+n][mask]).tofile(raw)
                        written += count
                        offset += n
                if offset != reader.header.point_count:
                    raise ValueError("LAS point count mismatch")
            if spectra is not None:
                bands = spectra.shape[1]
                mapped = open_memmap(spectral_path,mode="w+",dtype=np.float32,shape=(written,bands))
                if written:
                    with raw_path.open("rb") as raw:
                        for start in range(0,written,point_chunk):
                            n = min(point_chunk,written-start)
                            data = np.fromfile(raw,dtype=np.float32,count=n*bands)
                            if data.size != n*bands:
                                raise OSError("Truncated temporary spectra")
                            mapped[start:start+n] = data.reshape(n,bands)
                mapped.flush()
                mapped._mmap.close()
                mapped = None
                result = dict(metadata)
                for key in ("matched_points","candidate_count","candidate_spool_bytes","stage_timings_s"):
                    result.pop(key,None)
                result.update(schema_version=1,n_points=written,n_bands=bands,input_las=str(source),
                              painted_las=str(output),spectra_npy=spectral_path.name,
                              row_mapping="Spectral row i corresponds to output LAS point i",parent_metadata=metadata)
                manifest_path.write_text(json.dumps(result,indent=2),encoding="utf-8")
    except BaseException:
        if mapped is not None:
            mapped._mmap.close()
        for path in (output,spectral_path,manifest_path):
            path.unlink(missing_ok=True)
        raise
    return output

def statistics(source,dimensions=("z","intensity"),*,point_chunk=100_000):
    if not isinstance(point_chunk,int) or point_chunk < 1:
        raise ValueError("point_chunk must be a positive integer")
    accumulators = {name:_Moments() for name in dimensions}
    with laspy.open(source) as reader:
        available = set(reader.header.point_format.dimension_names) | {"x","y","z"}
        if any(name not in available for name in dimensions):
            raise ValueError("Missing LAS dimension")
        for points in reader.chunk_iterator(point_chunk):
            for name,accumulator in accumulators.items():
                accumulator.update(np.asarray(points[name],dtype=np.float64))
    return {name:accumulator.result() for name,accumulator in accumulators.items()}


def rgb_format(source_format: int) -> int:
    formats = {0: 2, 1: 3, 2: 2, 3: 3, 4: 5, 5: 5,
               6: 7, 7: 7, 8: 8, 9: 10, 10: 10}
    if source_format not in formats:
        raise ValueError(f"Unsupported LAS point format: {source_format}")
    return formats[source_format]

def add_rgb_to_header(header: laspy.LasHeader) -> laspy.LasHeader:
    target = rgb_format(header.point_format.id)
    if target == header.point_format.id:
        return header
    from laspy.header import Version
    from laspy.point import dims
    from laspy.point.format import PointFormat
    desired = PointFormat(target)
    # Preserve source extra dimensions in the RGB-capable point format.
    desired.dimensions.extend(list(header.point_format.extra_dimensions))
    needed = dims.preferred_file_version_for_point_format(target)
    current = (header.version.major, header.version.minor)
    required = tuple(map(int, needed.split(".")))
    version = header.version if current >= required else Version(*required)
    header.set_version_and_point_format(version, desired)
    return header

def add_rgb(source,output,bands,*,limits,point_chunk=100_000):
    """Write uint16 RGB with explicit zero-based bands and low/high display limits."""
    spectra,_ = open_spectra(source)
    bands = tuple(bands)
    if len(bands) != 3 or any(b < 0 or b >= spectra.shape[1] for b in bands):
        raise ValueError("Provide three valid band indices")
    low,high = (np.asarray(v,dtype=np.float64) for v in limits)
    if low.shape != (3,) or high.shape != (3,) or not np.isfinite([low,high]).all() or np.any(high <= low):
        raise ValueError("Invalid RGB display limits")
    with laspy.open(source) as reader:
        header = add_rgb_to_header(reader.header.copy())
    offset = 0
    def convert(chunk):
        nonlocal offset
        values = np.asarray(spectra[offset:offset+len(chunk),bands])
        valid = np.isfinite(values).all(axis=1)
        if "hs_valid" in chunk.point_format.dimension_names:
            valid &= np.asarray(chunk["hs_valid"]) != 0
        colors = np.zeros((len(chunk),3),dtype=np.uint16)
        colors[valid] = np.rint(np.clip((values[valid]-low)/(high-low),0,1)*65535).astype(np.uint16)
        offset += len(chunk)
        return dict(zip(("red","green","blue"),colors.T))
    return transform_points(source,output,transform=convert,output_header=header,point_chunk=point_chunk)
