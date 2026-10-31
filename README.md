# spectral-las

A Rust-backed Python package for point cloud colorization, supporting all your imagery needs from panchromatic, to RGB, to hyperspectral.

## Layout

- Python: `package/src/spectral_las/`
- Private Rust extension: `spectral_las._native`
- Rust: `package/rust/src/`
- Build metadata: `pyproject.toml` and `Cargo.toml`
- Python subpackages: `adapters`, `pointcloud`, and `raster`
