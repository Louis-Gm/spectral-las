from pathlib import Path
import re
import numpy as np
import rasterio
from rasterio.windows import Window

def read_envi_header(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    entries = re.findall(r"(?im)^\s*([^=\n]+)\s*=\s*(\{[^}]*\}|[^\n]*)", text)
    return {k.strip().lower():v.strip().strip("{}").strip() for k,v in entries}

def resolve_path(path):
    path = Path(path)
    if path.suffix.lower() != ".hdr":
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    meta = read_envi_header(path)
    candidates = [path.parent/meta["data file"]] if "data file" in meta else []
    candidates += [path.with_suffix(s) for s in ("", ".bin", ".img", ".dat")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No binary payload for {path}")

def _indices(src, bands):
    values = list(range(src.count)) if bands is None else list(bands)
    if not values or any(not isinstance(i,(int,np.integer)) or i < 0 or i >= src.count for i in values):
        raise ValueError("Provide valid zero-based band indices")
    return [int(i)+1 for i in values]

def read_info(path):
    with rasterio.open(resolve_path(path)) as src:
        tags = src.tags(ns="ENVI")
        waves = [float(v.strip()) for v in tags.get("wavelength", "").strip("{}").split(",") if v.strip()]
        return {"width":src.width,"height":src.height,"count":src.count,"dtypes":src.dtypes,
                "crs":src.crs,"transform":src.transform,"nodata":src.nodata,
                "descriptions":src.descriptions,"profile":src.profile.copy(),
                "wavelengths":waves,"wavelength_units":tags.get("wavelength_units")}

def iter_windows(path, *, bands=None, tile_size=512, masked=True):
    if not isinstance(tile_size,int) or tile_size < 1:
        raise ValueError("tile_size must be a positive integer")
    with rasterio.open(resolve_path(path)) as src:
        indices = _indices(src,bands)
        for row in range(0,src.height,tile_size):
            for col in range(0,src.width,tile_size):
                window = Window(col,row,min(tile_size,src.width-col),min(tile_size,src.height-row))
                yield window,src.read(indices,window=window,masked=masked)

def read_window(path, window, *, bands=None, masked=True):
    with rasterio.open(resolve_path(path)) as src:
        return src.read(_indices(src,bands),window=window,masked=masked)

def _profile(src,count,dtype="float32",nodata=float("nan")):
    return {"driver":"GTiff","width":src.width,"height":src.height,"count":count,
            "dtype":dtype,"crs":src.crs,"transform":src.transform,"nodata":nodata,
            "tiled":True,"blockxsize":256,"blockysize":256,"compress":"deflate","BIGTIFF":"IF_SAFER"}
