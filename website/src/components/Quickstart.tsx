import { useState } from "react";
import { Terminal, Sparkles, Waves, FileCode2, Box } from "lucide-react";
import CodeBlock from "./CodeBlock";
import { SectionHeader } from "./Features";
const EXAMPLES = [
  {
    id: "paint", icon: Sparkles, title: "Paint a point cloud",
    desc: "Headwall inputs become explicit flight configuration.", file: "paint.py", lang: "python" as const,
    code: "from spectral_las.adapters.headwall import load_flight\nfrom spectral_las.pointcloud import paint_pointcloud\n\nflight = load_flight(\n    \"flight_directory\",\n    crs=\"EPSG:32619\",\n    height_datum=\"ellipsoidal\",\n    gps_utc_offset_seconds=18.0,  # Example: verify for the acquisition epoch.\n)\npaint_pointcloud(flight, \"input.las\", \"painted.las\", point_chunk=100_000)\n# Outputs: LAS + .spectra.npy + .spectralpc.json\n# LAS and flight heights must use the same vertical datum."
  },
  {
    id: "bands", icon: Waves, title: "Calculate image bands",
    desc: "Windowed expressions with explicit zero-based band indices.", file: "bands.py", lang: "python" as const,
    code: "from spectral_las.raster import select_bands, calculate_bands\n\nselect_bands(\"image.tif\", \"selected.tif\", [10, 30, 50])\ncalculate_bands(\n    \"image.tif\", \"index.tif\",\n    {\"ndvi\": \"(nir-red)/(nir+red)\"},\n    {\"red\": 30, \"nir\": 50},  # Choose indices matching your wavelengths.\n    tile_size=512,\n)"
  },
  {
    id: "rasterize", icon: Box, title: "Rasterize point attributes",
    desc: "Disk partitions avoid a complete output grid per worker.", file: "rasterize.py", lang: "python" as const,
    code: "from spectral_las.raster import rasterize_pointcloud\n\nrasterize_pointcloud(\n    \"painted.las\", \"spectral_mean.tif\",\n    resolution=0.2,  # LAS coordinate units\n    fields=(\"hs:10\", \"hs:30\"),\n    reducer=\"mean\",\n    point_chunk=100_000,\n    tile_size=256,\n)\n# Scratch disk grows with selected points; chunks and tiles control buffers."
  },
  {
    id: "configure", icon: FileCode2, title: "Use another configuration",
    desc: "No Headwall filenames are required by the Rust constructor.", file: "configure.py", lang: "python" as const,
    code: "from pathlib import Path\nfrom spectral_las import CameraConfig, FlightConfig, SwathConfig\nfrom spectral_las.trajectory import read_trajectory\n\ntrajectory = read_trajectory(\"trajectory.txt\", angles=\"radians\")\n# line_times: your synchronized, strictly increasing NumPy time vector\nflight = FlightConfig(\n    CameraConfig(samples=640, field_of_view_degrees=40.0),\n    trajectory,\n    (SwathConfig(0, Path(\"cube.hdr\"), Path(\"cube.bin\"), line_times),),\n    crs=\"EPSG:32619\", height_datum=\"ellipsoidal\",\n)\n# Geometry is the current pushbroom model, not an arbitrary camera plugin."
  }
];
const INSTALL = [
  { id: "pypi", cmd: "python -m pip install spectral-las" },
  { id: "laz", cmd: 'python -m pip install "spectral-las[laz]"' },
  { id: "source", cmd: "python -m pip install ." },
  { id: "editable", cmd: "maturin develop --release" },
];
export default function Quickstart() {
  const [active, setActive] = useState(EXAMPLES[0].id);
  const [inst, setInst] = useState("pypi");
  const ex = EXAMPLES.find((e) => e.id === active)!;
  const installCmd = INSTALL.find((i) => i.id === inst)!;
  return (
    <section id="quickstart" className="relative py-24 lg:py-32">
      <div className="pointer-events-none absolute inset-x-0 top-1/3 h-96 bg-gradient-to-r from-violet-600/5 via-cyan-500/5 to-orange-500/5 blur-3xl" />
      <div className="relative mx-auto max-w-7xl px-5 lg:px-8">
        <SectionHeader
          eyebrow="Quickstart"
          title="Readable Python, compiled speed"
          body="Install spectral-las from PyPI with Python 3.10–3.12. Source builds require Rust; examples require your own imagery, calibration and point-cloud data."
        />
        <div className="mt-14 grid gap-6 lg:grid-cols-[320px_1fr]">
          <div className="flex flex-col gap-2">
            {EXAMPLES.map((e) => (
              <button
                key={e.id}
                onClick={() => setActive(e.id)}
                className={`flex items-start gap-3 rounded-xl border p-4 text-left transition ${
                  active === e.id
                    ? "border-white/15 bg-white/[0.06]"
                    : "border-transparent hover:border-white/5 hover:bg-white/[0.03]"
                }`}
              >
                <e.icon className={`mt-0.5 h-5 w-5 shrink-0 ${active === e.id ? "text-cyan-300" : "text-slate-500"}`} />
                <div>
                  <div className={`text-sm font-semibold ${active === e.id ? "text-white" : "text-slate-300"}`}>{e.title}</div>
                  <div className="mt-0.5 text-[13px] text-slate-500">{e.desc}</div>
                </div>
              </button>
            ))}
            <div className="mt-4 rounded-xl border border-white/10 bg-black/30 p-4">
              <div className="flex flex-wrap gap-1">
                {INSTALL.map((i) => (
                  <button
                    key={i.id}
                    onClick={() => setInst(i.id)}
                    className={`rounded-md px-2.5 py-1 font-mono text-xs transition ${
                      inst === i.id ? "bg-white/10 text-white" : "text-slate-500 hover:text-slate-300"
                    }`}
                  >
                    {i.id}
                  </button>
                ))}
              </div>
              <div className="mt-3 break-all font-mono text-[13px] text-slate-200">
                <span className="text-slate-500">$ </span>
                {installCmd.cmd}
              </div>
            </div>
          </div>
          <div className="min-w-0">
            <CodeBlock key={ex.id} code={ex.code} lang={ex.lang} filename={ex.file} className="shadow-2xl shadow-black/40" />
          </div>
        </div>
      </div>
    </section>
  );
}
