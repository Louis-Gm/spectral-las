from pathlib import Path
import multiprocessing

PARAMETERS = {
    "working_directory": Path(r"C:\Temp_traitement\rgb painting\tiles"),
    "output_subfolder": "rgb",
    "prefix": "1km",
    "tile_id_digits": 11,
    "lidar_acquisition": None,
    "ortho_acquisition": None,
    "workers": 24,
    "native_threads_per_worker": 1,
    "point_chunk": 2_000_000,
    "block_cache_mb": 256.0,
    "gdal_cache_mb": 64,
    "rgb_bands": (0, 1, 2),
    "unmatched_rgb": (0, 0, 0),
    "coverage_tolerance": 0.01,
    "resume": True,
    "skip_unpaired": True,
    "limit_pairs": None,
    "dry_run": False,
    "log_every_tiles": 1,
    "detailed_tile_logging": True,
    "live_progress": True,
    "progress_interval_seconds": 5.0,
    "heartbeat_seconds": 10.0,
    "merge_outputs": True,
    "merged_output_name": "merged_pcd_rgb.laz",
    "merge_chunk_points": 2_000_000,
    "merge_native_threads": 24,
    "image_loading": "auto",
    "raster_memory_budget_gib": 48.0,
}

AUDIT = {
    "enabled": True,
    "write_reports_in_dry_run": False,
}


def main():
    from spectral_las.pointcloud.ortho_rgb_batch import run_rgb_tile_batch
    if AUDIT["enabled"]:
        from spectral_las.pointcloud.ortho_rgb_audit import audit_tile_inputs
        summary=audit_tile_inputs(
            working_directory=PARAMETERS["working_directory"],output_subfolder=PARAMETERS["output_subfolder"],
            prefix=PARAMETERS["prefix"],tile_id_digits=PARAMETERS["tile_id_digits"],
            lidar_acquisition=PARAMETERS["lidar_acquisition"],ortho_acquisition=PARAMETERS["ortho_acquisition"],
            write_reports=(not PARAMETERS["dry_run"] or AUDIT["write_reports_in_dry_run"]),
        )
        print(f"Input audit: {summary['matched_pairs']:,} complete pairs; "
              f"{summary['unmatched_lidar_files']:,} LiDAR files missing imagery; "
              f"{summary['unmatched_ortho_files']:,} images missing LiDAR; "
              f"{len(summary['ambiguous_tile_ids']):,} ambiguous tile IDs; "
              f"{summary['malformed_filenames']:,} malformed filenames; "
              f"{summary['excluded_by_acquisition_filter']:,} acquisition-filter exclusions",flush=True)
        for label,path in summary["reports"].items(): print(f"  {label}: {path}",flush=True)
    report=run_rgb_tile_batch(**PARAMETERS)
    if report.get("failed",0): raise SystemExit(1)


if __name__=="__main__":
    multiprocessing.freeze_support()
    main()
