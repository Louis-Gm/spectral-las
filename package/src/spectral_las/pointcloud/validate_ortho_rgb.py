from __future__ import annotations

import sys
from pathlib import Path

import laspy
import numpy as np


def validate(source: str | Path, output: str | Path) -> None:
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()

    with laspy.open(source_path) as src, laspy.open(output_path) as dst:
        assert src.header.point_count == dst.header.point_count

        source_names = set(src.header.point_format.dimension_names)
        output_names = set(dst.header.point_format.dimension_names)

        assert source_names.issubset(output_names)
        assert {"red", "green", "blue"}.issubset(output_names)

        for name in source_names:
            if name in {"x", "y", "z"}:
                continue

            source_values = np.asarray(src[name])
            output_values = np.asarray(dst[name])

            if source_values.dtype == output_values.dtype:
                assert np.array_equal(source_values, output_values), name

        red = np.asarray(dst.red)
        green = np.asarray(dst.green)
        blue = np.asarray(dst.blue)

        assert red.dtype == np.uint16
        assert green.dtype == np.uint16
        assert blue.dtype == np.uint16

        print(f"points: {dst.header.point_count}")
        print(f"nonblack RGB points: {np.count_nonzero(red | green | blue)}")
        print("validation passed")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: python validate_ortho_rgb.py INPUT_LAS_OR_LAZ OUTPUT_LAS_OR_LAZ"
        )

    validate(sys.argv[1], sys.argv[2])
