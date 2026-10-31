<br />
<div align="center">
  <a href="https://github.com/Louis-Gm/spectral-las">
    <img src="https://raw.githubusercontent.com/Louis-Gm/spectral-las/main/website/public/favicon.svg" alt="Spectral LAS logo" width="120" height="120">
  </a>
  <h1 align="center"><b>Spectral LAS</b></h1>
  <div align="center">
    An open-source point cloud colorization method for multidimensional imagery
  </div>

  <p align="center">
    <a href="https://louis-gm.github.io/spectral-las/"><strong>Explore the website »</strong></a>
  </p>

  <br /><div align="center">
    <a href="https://github.com/Louis-Gm/spectral-las/issues/new?template=bug_report.yml">Report Bug</a>
    ·
    <a href="https://github.com/Louis-Gm/spectral-las/issues/new?template=feature_request.yml">Request Feature</a>
  </div><br />

  <div align="center">
    <a href="https://doi.org/10.5281/zenodo.23842730"><img src="https://zenodo.org/badge/DOI/10.5281/zenodo.23842730.svg" alt="DOI"></a>
    <img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg" alt="Python versions">
    <img src="https://img.shields.io/badge/Apache%202.0-blue.svg" alt="License">
    <img src="https://img.shields.io/pypi/v/spectral-las?color=purple" alt="PyPI version">
    <img src="https://github.com/Louis-Gm/spectral-las/actions/workflows/package-build.yml/badge.svg" alt="Package build status">
    <img src="https://github.com/Louis-Gm/spectral-las/actions/workflows/website-build.yml/badge.svg" alt="Website build status">
  </div>
</div>

## **About The Project**

**Spectral LAS** is a Python package for colorizing LAS point clouds with imagery.
It supports workflows spanning panchromatic, RGB, multispectral, and
hyperspectral data, with an explicit Python interface and Rust-backed projection
kernels.

The package connects imagery, LAS point clouds, and synchronized trajectory
information. It can retain point-aligned spectra, create RGB attributes, calculate
image bands, and reduce point attributes into raster products.

### **Key Features**

* **LAS and imagery workflows:** Process LAS point clouds alongside ENVI and
  Rasterio-supported imagery.
* **Spectral colorization:** Attach spectra to points and create RGB or
  analysis-ready products.
* **Explicit geometry:** Declare CRS, height datum, trajectory, and time
  conventions rather than relying on silent inference.
* **Rust-backed processing:** Python coordinates files and workflows; selected
  projection kernels use Rust, PyO3, and Rayon.
* **Memory-conscious design:** Use image windows, point chunks, and disk
  partitions to control working buffers.
* **Flexible imagery support:** Work with panchromatic, RGB, multispectral, and
  hyperspectral imagery.

> **Pre-alpha software:** Spectral LAS is under active development. Its API and
> supported acquisition workflows may change. Validate outputs against your own
> acquisition data before using them as scientific or production deliverables.

## **Getting Started**

### **Installation**

Install Spectral LAS from PyPI with Python 3.10, 3.11, or 3.12:

```sh
python -m pip install spectral-las
```

To enable optional LAZ compression support:

```sh
python -m pip install "spectral-las[laz]"
```

See the [website](https://louis-gm.github.io/spectral-las/) for installation
guidance, examples, and current limitations.

## **Usage**

The following example loads a Headwall acquisition configuration and paints a LAS
point cloud with imagery-derived spectra.

```python
from spectral_las.adapters.headwall import load_flight
from spectral_las.pointcloud import paint_pointcloud

flight = load_flight(
    "flight_directory",
    crs="EPSG:32619",
    height_datum="ellipsoidal",
    gps_utc_offset_seconds=18.0,  # Verify this value for your acquisition epoch.
)

paint_pointcloud(
    flight,
    "input.las",
    "painted.las",
    point_chunk=100_000,
)
```

The output includes a painted LAS file, a row-aligned spectra array, and a
completion manifest. The LAS coordinates and flight heights must use the same
vertical datum.

For additional workflows, see the [website quickstart](https://louis-gm.github.io/spectral-las/#quickstart).

## **Contribute**

As an open-source project, we encourage and welcome contributions from
researchers, industry professionals, as well as passionate hobbyists.

**Want to help?** Please read our
[CONTRIBUTING](https://github.com/Louis-Gm/spectral-las/blob/main/CONTRIBUTING.md)
section for a detailed explanation of how to submit pull requests. Please also
make sure to read the project's
[CODE OF CONDUCT](https://github.com/Louis-Gm/spectral-las/blob/main/CODE_OF_CONDUCT.md).

Not sure how to implement your idea, but want to contribute?

Feel free to leave a feature request
[here](https://github.com/Louis-Gm/spectral-las/issues/new?template=feature_request.yml).

## **Citation**

If you use this project in your research, please cite it as:

Grand'Maison, L.-V. (2026). Spectral LAS (0.1.1) [software]. Zenodo. https://doi.org/10.5281/zenodo.23842730

## **Contact**

The project is currently being maintained by **Louis-Vincent Grand'Maison**.

Feel free to contact me by email or LinkedIn:

Email - [lvgra@ulaval.ca](mailto:lvgra@ulaval.ca)

LinkedIn - [grandmaison-lv](https://www.linkedin.com/in/grandmaison-lv/)

## **Acknowledgments & Funding**

This software is developed by Louis-Vincent Grand'Maison as part of a PhD
project. The maintenance and development of this project is supported by several
research scholarships:

* Fonds de recherche du Québec – Nature et technologies (FRQNT) (Master's
  Scholarship 2024-2025)
* Natural Sciences and Engineering Research Council of Canada (NSERC) (Master's
  Scholarship 2025-2026)
* Fonds de recherche du Québec – Nature et technologies (FRQNT) (PhD Scholarship
  2026-present)

## **License**

`Spectral LAS` is distributed under the Apache License, Version 2.0.

This license includes a world-wide, no-charge, royalty-free, irrevocable patent
license for all users.

See [LICENSE](https://github.com/Louis-Gm/spectral-las/blob/main/LICENSE) for
more information on licensing and copyright.

([Back to Top](#about-the-project))
