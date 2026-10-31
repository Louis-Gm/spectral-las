import { Cpu, Crosshair, Layers, ShieldCheck, Database, Puzzle } from "lucide-react";
const STATS = [
  { v: "3", u: "subpackages", l: "pointcloud · raster · adapters" },
  { v: "Python", u: "+ Rust", l: "Readable orchestration, native kernels" },
  { v: "Windowed", u: "I/O", l: "Image windows and LAS chunks" },
  { v: "Explicit", u: "inputs", l: "Camera, trajectory and time conventions" },
];
const SECTORS = ["Remote sensing labs", "Precision agriculture", "Forestry & ecology", "Mining & geology", "Infrastructure inspection", "National mapping agencies"];
const FEATURES = [
  { icon: Cpu, title: "Rust core, Python API", body: "Projection and candidate-selection kernels use Rust, PyO3 and Rayon. Ordinary Python functions coordinate file access and outputs.", tag: "PyO3 · Rayon", color: "from-orange-500/20" },
  { icon: Crosshair, title: "Configured pushbroom geometry", body: "Supply trajectory, line times, boresight and lever-arm parameters explicitly. The Headwall adapter translates vendor files into those inputs.", tag: "Pushbroom · Trajectory", color: "from-cyan-500/20" },
  { icon: Layers, title: "Spectra and band calculations", body: "Store row-aligned spectra beside LAS files, create RGB attributes, select or remove image bands, and calculate named image expressions.", tag: "Spectra · RGB · Expressions", color: "from-emerald-500/20" },
  { icon: ShieldCheck, title: "Visible assumptions", body: "CRS, height datum and time references are declared inputs. Outputs record processing parameters; remaining geometry and resource limitations are documented.", tag: "Configuration · Metadata", color: "from-violet-500/20" },
  { icon: Database, title: "Memory-conscious processing", body: "Use windowed image I/O, chunked LAS processing, candidate spools and disk-partitioned rasterization. Painting still retains a global depth map; DSM mosaics use an explicit surface cap.", tag: "Windows · Chunks · Scratch disk", color: "from-sky-500/20" },
  { icon: Puzzle, title: "Focused integrations", body: "LAS/LAZ through laspy, image access through Rasterio, and NumPy arrays for explicit flight data. LAZ requires an optional compression backend.", tag: "laspy · Rasterio · NumPy", color: "from-rose-500/20" },
];
export default function Features() {
  return (
    <>
      <section className="border-y border-white/5 bg-white/[0.015]">
        <div className="mx-auto grid max-w-7xl grid-cols-2 gap-px px-5 lg:grid-cols-4 lg:px-8">
          {STATS.map((s) => (
            <div key={s.l} className="py-8 lg:px-6">
              <div className="flex items-baseline gap-1.5">
                <span className="text-3xl font-bold tracking-tight text-white">{s.v}</span>
                {s.u && <span className="font-mono text-sm text-slate-500">{s.u}</span>}
              </div>
              <p className="mt-1 text-sm text-slate-400">{s.l}</p>
            </div>
          ))}
        </div>
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-8 gap-y-3 border-t border-white/5 px-5 py-5 text-sm text-slate-500 lg:px-8">
          <span className="font-mono text-[11px] uppercase tracking-widest text-slate-600">Designed for</span>
          {SECTORS.map((s) => (
            <span key={s} className="font-medium text-slate-400">{s}</span>
          ))}
        </div>
      </section>
      <section id="features" className="relative py-24 lg:py-32">
        <div className="mx-auto max-w-7xl px-5 lg:px-8">
          <SectionHeader
            eyebrow="Capabilities"
            title="Built for the full fusion workflow"
            body="From raw sensor data to analysis-ready colored point clouds. The pre-alpha package keeps its supported operations and limitations explicit."
          />
          <div className="mt-16 grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div
                key={f.title}
                className="group relative overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-b from-white/[0.04] to-transparent p-7 transition hover:border-white/15"
              >
                <div className={`pointer-events-none absolute -top-16 -right-16 h-40 w-40 rounded-full bg-gradient-to-br ${f.color} to-transparent blur-2xl transition group-hover:scale-125`} />
                <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-white/10 bg-white/5">
                  <f.icon className="h-5 w-5 text-white" />
                </div>
                <h3 className="mt-5 text-lg font-semibold text-white">{f.title}</h3>
                <p className="mt-2.5 text-[15px] leading-relaxed text-slate-400">{f.body}</p>
                <p className="mt-5 font-mono text-[11px] text-slate-500">{f.tag}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
export function SectionHeader({ eyebrow, title, body, center = true }: { eyebrow: string; title: string; body?: string; center?: boolean }) {
  return (
    <div className={center ? "mx-auto max-w-2xl text-center" : "max-w-2xl"}>
      <p className="font-mono text-xs font-medium uppercase tracking-[0.2em] text-cyan-400/90">{eyebrow}</p>
      <h2 className="mt-3 text-3xl font-bold tracking-tight text-white sm:text-4xl">{title}</h2>
      {body && <p className="mt-4 text-lg leading-relaxed text-slate-400">{body}</p>}
    </div>
  );
}
