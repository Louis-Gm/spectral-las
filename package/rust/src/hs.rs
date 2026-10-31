use std::fs::File;
use std::io::{BufRead, BufReader, Error, ErrorKind};
use memmap2::MmapOptions;
use crate::ortho::ProjectedPixel;
/// Contains the parsed dimensionality and data interleaving format of the ENVI hyperspectral cube.
pub struct EnviMetadata {
    pub samples: usize,
    pub lines: usize,
    pub bands: usize,
    pub interleave: String,
}
/// Parses the ENVI header to determine the array dimensions and memory interleave format.
pub fn parse_envi_header(path: &str) -> std::io::Result<EnviMetadata> {
    let reader = BufReader::new(File::open(path)?);
    let mut meta = EnviMetadata { samples: 0, lines: 0, bands: 0, interleave: String::from("bsq") };
    for line in reader.lines().filter_map(|l| l.ok()) {
        if let Some((key, val)) = line.split_once('=') {
            let k = key.trim().to_lowercase();
            let v = val.trim().to_lowercase();
            match k.as_str() {
                "samples" => meta.samples = v.parse().unwrap_or(0),
                "lines" => meta.lines = v.parse().unwrap_or(0),
                "bands" => meta.bands = v.parse().unwrap_or(0),
                "interleave" => meta.interleave = v,
                _ => {}
            }
        }
    }
    Ok(meta)
}
/// Extracts a specific 32-bit floating point spectral value from the memory-mapped hyperspectral cube based on its interleave format.
pub fn read_spectrum_value(
    mmap: &[u8],
    interleave: &str,
    line: usize,
    sample: usize,
    band: usize,
    lines: usize,
    samples: usize,
    bands: usize
) -> f32 {
    let offset = match interleave {
        "bil" => (line * bands * samples) + (band * samples) + sample,
        "bip" => (line * samples * bands) + (sample * bands) + band,
        _ => (band * lines * samples) + (line * samples) + sample,
    };
    let byte_offset = offset * 4;
    if byte_offset + 4 <= mmap.len() {
        let bytes: [u8; 4] = [
            mmap[byte_offset],
            mmap[byte_offset + 1],
            mmap[byte_offset + 2],
            mmap[byte_offset + 3]
        ];
        f32::from_le_bytes(bytes)
    } else {
        f32::NAN
    }
}
/// Rasterizes projected sensor pixels into a uniform 2D spatial grid using Gaussian influence
/// and off-nadir view-angle attenuation to resolve footprint aliasing and swath-overlap zippering.
///
/// # Arguments
/// * `chunk_grid` - A mutable, flat 1D slice representing the 3D chunk grid `[bands * rows * cols]`.
/// * `quality_buffer` - A mutable, flat 1D slice representing the Z-buffer/Quality metric `[rows * cols]`.
/// * `hdr_path` - The absolute path to the ENVI header file.
/// * `bin_path` - The absolute path to the ENVI binary payload.
/// * `projected_pixels` - A slice of `ProjectedPixel` structs containing ray-traced spatial intersections.
/// * `band_indices` - The specific spectral bands to extract from the raw payload.
/// * `chunk_cols` - The width of the spatial chunk in pixels.
/// * `chunk_rows` - The height of the spatial chunk in pixels.
/// * `chunk_min_x` - The minimum Easting coordinate of the chunk.
/// * `chunk_max_y` - The maximum Northing coordinate of the chunk.
/// * `ortho_res` - The spatial resolution of the chunk grid in meters.
pub fn rasterize_into_chunk(
    chunk_grid: &mut [f32],
    quality_buffer: &mut [f64],
    hdr_path: &str,
    bin_path: &str,
    projected_pixels: &[ProjectedPixel],
    band_indices: &[usize],
    chunk_cols: usize,
    chunk_rows: usize,
    chunk_min_x: f64,
    chunk_max_y: f64,
    ortho_res: f64,
) -> std::io::Result<()> {
    let meta = crate::hs::parse_envi_header(hdr_path)?;
    let file = File::open(bin_path)?;
    let mmap = unsafe { MmapOptions::new().map(&file)? };
    // The maximum search radius for splatting influence (typically 1.5x resolution)
    let search_radius = 1;
    let max_sq_dist = (ortho_res * 1.5).powi(2);
    for px in projected_pixels {
        // Calculate exact sub-pixel floating coordinates
        let exact_col = (px.utm_x - chunk_min_x) / ortho_res;
        let exact_row = (chunk_max_y - px.utm_y) / ortho_res;
        let center_col = exact_col.floor() as isize;
        let center_row = exact_row.floor() as isize;
        // Attenuate quality strictly based on how far the pixel is from the center of the sensor.
        // This ensures the flight line passing directly overhead inherently wins overlap zones, eliminating the zipper.
        let center_sensor_pixel = meta.samples as f64 / 2.0;
        let pixel_deviation = (px.sample_idx as f64 - center_sensor_pixel).abs();
        let off_nadir_penalty = 1.0 + (pixel_deviation / center_sensor_pixel).powi(2) * 5.0;
        let base_quality = px.nadir_distance * off_nadir_penalty;
        for dr in -search_radius..=search_radius {
            for dc in -search_radius..=search_radius {
                let r = center_row + dr;
                let c = center_col + dc;
                if c >= 0 && c < chunk_cols as isize && r >= 0 && r < chunk_rows as isize {
                    // Calculate exact Euclidean distance from the ray-hit to the cell center
                    let cell_center_x = chunk_min_x + (c as f64 * ortho_res) + (ortho_res / 2.0);
                    let cell_center_y = chunk_max_y - (r as f64 * ortho_res) - (ortho_res / 2.0);
                    let sq_dist = (px.utm_x - cell_center_x).powi(2) + (px.utm_y - cell_center_y).powi(2);
                    if sq_dist <= max_sq_dist {
                        let spatial_idx = (r as usize) * chunk_cols + (c as usize);
                        // Apply a smooth Gaussian distance weight to the splat
                        // This prevents jagged borders by favoring exact center hits while still filling holes
                        let spatial_weight = sq_dist / max_sq_dist;
                        let effective_quality = base_quality * (1.0 + spatial_weight);
                        if effective_quality < quality_buffer[spatial_idx] {
                            quality_buffer[spatial_idx] = effective_quality;
                            for (i, &b) in band_indices.iter().enumerate() {
                                let val = crate::hs::read_spectrum_value(
                                    &mmap, &meta.interleave, px.line_idx, px.sample_idx, b,
                                    meta.lines, meta.samples, meta.bands
                                );
                                let flat_idx = (i * chunk_rows * chunk_cols) + spatial_idx;
                                chunk_grid[flat_idx] = val;
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(())
}
