mod fusion;
mod hs;
mod ortho;
use pyo3::prelude::*;

#[pymodule]
fn _native(_py:Python<'_>,module:&PyModule)->PyResult<()> {
    module.add_class::<fusion::Flight>()?;
    Ok(())
}
