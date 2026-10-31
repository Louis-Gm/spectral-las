# spectral-las

A Rust-backed Python package for point cloud colorization, supporting all your imagery needs from panchromatic, to RGB, to hyperspectral.

## Status

Pre-alpha migration of the existing SpectralPC implementation. Sensor coverage,
projection workflows, distribution builds, and output equivalence require
verification before release. The description states the project scope, not a
claim that every imagery format is already validated.

The project targets forward- and reverse-projection workflows. This rename
preserves existing processing algorithms and public function names.

## Layout

- Python: `package/src/spectral_las/`
- Private Rust extension: `spectral_las._native`
- Rust: `package/rust/src/`
- Build metadata: `pyproject.toml` and `Cargo.toml`
- Python subpackages: `adapters`, `pointcloud`, and `raster`
- Website, if present, remains separate from the Python distribution.

## Local development

Use Python 3.10-3.12, an active virtual environment, and the Rust toolchain.
From the repository root:

```sh
python -m pip install "maturin>=1.7,<2"
maturin develop --release
python -c "import spectral_las; import spectral_las._native"
```

## Project documents

The root README is the canonical project introduction. Keep package/README.md
identical for distribution. License changes, NOTICE revisions, citation metadata,
and legal-document relocation require maintainer decisions before release.
Existing license declarations and attribution remain unchanged in this overlay.
