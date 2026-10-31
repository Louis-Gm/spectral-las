from pathlib import Path
import math
import numpy as np
import numexpr as ne
import rasterio
from .io import resolve_path,iter_windows,_indices,_profile

def _target(source,output):
    source,output = resolve_path(source),Path(output)
    if output.exists() or source.resolve() == output.resolve():
        raise FileExistsError(output)
    return source,output

def select_bands(source,output,bands,*,tile_size=512):
    source,output = _target(source,output)
    bands = list(bands)
    try:
        with rasterio.open(source) as src:
            indices = _indices(src,bands)
            dtypes = {src.dtypes[i-1] for i in indices}
            if len(dtypes) != 1:
                raise ValueError("Selected band dtypes differ")
            with rasterio.open(output,"w",**_profile(src,len(indices),dtypes.pop(),src.nodata)) as dst:
                for window,block in iter_windows(source,bands=bands,tile_size=tile_size):
                    dst.write(block.filled(src.nodata if src.nodata is not None else 0),window=window)
                    dst.write_mask((~np.ma.getmaskarray(block).any(axis=0)).astype("uint8")*255,window=window)
                for index,original in enumerate(indices,1):
                    if src.descriptions[original-1]:
                        dst.set_band_description(index,src.descriptions[original-1])
                    dst.update_tags(index,**src.tags(original))
                tags = src.tags(ns="ENVI")
                waves = tags.get("wavelength","").strip("{}").split(",")
                if len(waves) == src.count:
                    dst.update_tags(ns="ENVI",wavelength="{"+",".join(waves[i-1] for i in indices)+"}",
                                    wavelength_units=tags.get("wavelength_units",""))
                dst.update_tags(source_band_indices_zero_based=",".join(map(str,bands)))
    except BaseException:
        output.unlink(missing_ok=True)
        Path(str(output)+".msk").unlink(missing_ok=True)
        raise
    return output

def remove_bands(source,output,bands,*,tile_size=512):
    with rasterio.open(resolve_path(source)) as src:
        removed = set(bands)
        if any(i < 0 or i >= src.count for i in removed):
            raise ValueError("Invalid band index")
        keep = [i for i in range(src.count) if i not in removed]
    return select_bands(source,output,keep,tile_size=tile_size)

def calculate_bands(source,output,expressions,band_mapping,*,tile_size=512):
    source,output = _target(source,output)
    if not expressions or not band_mapping:
        raise ValueError("Provide named expressions and variable-to-band mapping")
    bands = sorted(set(band_mapping.values()))
    positions = {band:i for i,band in enumerate(bands)}
    try:
        with rasterio.open(source) as src:
            _indices(src,bands)
            with rasterio.open(output,"w",**_profile(src,len(expressions))) as dst:
                for window,block in iter_windows(source,bands=bands,tile_size=tile_size):
                    data = np.ma.asarray(block,dtype=np.float32).filled(np.nan)
                    variables = {name:data[positions[band]] for name,band in band_mapping.items()}
                    valid = np.isfinite(data).all(axis=0)
                    results = []
                    for expression in expressions.values():
                        with np.errstate(all="ignore"):
                            result = np.asarray(ne.evaluate(expression,local_dict=variables),dtype=np.float32)
                        result = np.broadcast_to(result,data.shape[1:]).copy()
                        result[~valid | ~np.isfinite(result)] = np.nan
                        results.append(result)
                    dst.write(np.stack(results),window=window)
                for index,name in enumerate(expressions,1):
                    dst.set_band_description(index,name)
                    dst.update_tags(index,expression=expressions[name])
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output

class _Moments:
    def __init__(self):
        self.n,self.mean,self.m2 = 0,0.0,0.0
        self.minimum,self.maximum = float("inf"),float("-inf")
    def update(self,values):
        values = np.asarray(values,dtype=np.float64)
        values = values[np.isfinite(values)]
        if not len(values):
            return
        n,mean = len(values),float(values.mean())
        delta,total = mean-self.mean,self.n+n
        self.m2 += float(np.sum((values-mean)**2))+delta*delta*self.n*n/total
        self.mean += delta*n/total
        self.n = total
        self.minimum = min(self.minimum,float(values.min()))
        self.maximum = max(self.maximum,float(values.max()))
    def result(self):
        return {"count":self.n,"mean":self.mean if self.n else float("nan"),
                "std":math.sqrt(max(0,self.m2/self.n)) if self.n else float("nan"),
                "min":self.minimum if self.n else float("nan"),"max":self.maximum if self.n else float("nan")}

def statistics(source,*,bands=None,tile_size=512):
    accumulators = None
    for _,block in iter_windows(source,bands=bands,tile_size=tile_size):
        if accumulators is None:
            accumulators = [_Moments() for _ in range(block.shape[0])]
        for accumulator,values in zip(accumulators,block):
            accumulator.update(np.ma.asarray(values,dtype=np.float64).filled(np.nan).ravel())
    return [a.result() for a in accumulators or []]
