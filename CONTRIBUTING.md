# Contributing to Spectral LAS

Thank you for considering a contribution. Spectral LAS is pre-alpha software:
its Python API, Rust implementation, supported sensors, and documentation may
change between releases.

## Ways to contribute

- Report reproducible bugs through the bug-report issue form.
- Propose features through the feature-request issue form.
- Improve documentation, examples, error messages, and acquisition guidance.
- Contribute code after discussing substantial API or geometry changes first.

## Development setup

Use Python 3.10-3.12 and a Rust toolchain for package development.

```sh
python -m pip install "maturin>=1.7,<2"
maturin develop --release
```

For the website, use Node.js 22.12 or newer:

```sh
cd website
npm install
npm run dev
```

## Before opening a pull request

- Keep package and website changes separate where practical.
- Run `maturin develop --release` and exercise the affected workflow locally.
- For website changes, run:

```sh
cd website
npm run typecheck
npm run build
```

- Describe the acquisition context, limitations, and validation performed.
- Do not commit proprietary imagery, LAS/LAZ files, trajectories, credentials,
  or large generated artifacts.
- Do not commit `node_modules/`, `website/dist/`, or local `.env` files.

## Code expectations

- Prefer explicit configuration over silent inference.
- Preserve clear separation between Python orchestration and Rust kernels.
- Document coordinate systems, vertical datums, time references, and units.
- Avoid claiming accuracy, sensor support, or performance without validation.
- Keep the public API small and backward compatibility explicit.

## Issue reporting

Use the GitHub issue templates. Include the package version, operating system,
Python version, minimal reproduction, and complete error output. Do not include
proprietary acquisition data.
