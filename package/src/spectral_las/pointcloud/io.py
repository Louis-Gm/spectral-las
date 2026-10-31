from pathlib import Path
import json
import numpy as np
import laspy

def iter_chunks(path,*,point_chunk=100_000):
    if not isinstance(point_chunk,int) or point_chunk < 1:
        raise ValueError("point_chunk must be a positive integer")
    with laspy.open(path) as reader:
        yield from reader.chunk_iterator(point_chunk)

def read_info(path):
    with laspy.open(path) as reader:
        return {"point_count":reader.header.point_count,"point_format":reader.header.point_format.id,
                "dimensions":tuple(reader.header.point_format.dimension_names),
                "crs":reader.header.parse_crs(),"header":reader.header.copy()}

def companion_paths(path):
    path = Path(path)
    return path.with_suffix(".spectra.npy"),path.with_suffix(".spectralpc.json")

def open_spectra(path):
    spectra_path,manifest = companion_paths(path)
    if not manifest.exists():
        for suffix in (".spectrivet.json",".hyperlas.json"):
            candidate = Path(path).with_suffix(suffix)
            if candidate.exists():
                manifest = candidate
                break
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    data = np.load(spectra_path,mmap_mode="r",allow_pickle=False)
    with laspy.open(path) as reader:
        count = int(reader.header.point_count)
    if data.ndim != 2 or data.shape[0] != count or data.dtype != np.float32:
        raise ValueError("Spectra dtype/shape does not match LAS")
    if int(metadata.get("n_points",-1)) != count or int(metadata.get("n_bands",-1)) != data.shape[1]:
        raise ValueError("Manifest does not match LAS/spectra")
    return data,metadata
