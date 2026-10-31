import { ScanLine, Satellite, Move3d, Eye, Palette } from "lucide-react";
import { SectionHeader } from "./Features";
const STEPS = [
  { icon: ScanLine, t: "Read", d: "LAS point chunks, image windows and memory-mapped ENVI spectra.", c: "#7c3aed" },
  { icon: Satellite, t: "Configure", d: "Explicit pushbroom geometry, normalized trajectory, line times and coordinate references.", c: "#3b82f6" },
  { icon: Move3d, t: "Project", d: "Search synchronized scan-plane crossings and select supported sensor samples.", c: "#06b6d4" },
  { icon: Eye, t: "Select", d: "Two-pass point-sampled visibility; not a continuous-surface occlusion guarantee.", c: "#22c55e" },
  { icon: Palette, t: "Transform", d: "Preserve spectra, add RGB, calculate bands or reduce point attributes into a raster.", c: "#f97316" },
];
export default function Pipeline() {
  return (
    <section className="relative border-y border-white/5 bg-white/[0.015] py-24 lg:py-28">
      <div className="mx-auto max-w-7xl px-5 lg:px-8">
        <SectionHeader
          eyebrow="How it works"
          title="From photons to points, rigorously"
          body="Sensor conventions and validation requirements are explicit; accuracy must be checked against acquisition data."
        />
        <div className="relative mt-16">
          <div className="spectral-bg absolute top-6 right-[10%] left-[10%] hidden h-px opacity-50 lg:block" />
          <ol className="grid gap-8 lg:grid-cols-5 lg:gap-6">
            {STEPS.map((s, i) => (
              <li key={s.t} className="relative flex gap-4 lg:flex-col lg:items-center lg:text-center">
                <div
                  className="relative z-10 flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border bg-[#0a0f1c]"
                  style={{ borderColor: `${s.c}66`, boxShadow: `0 0 24px ${s.c}33` }}
                >
                  <s.icon className="h-5 w-5" style={{ color: s.c }} />
                </div>
                <div className="lg:mt-5">
                  <div className="font-mono text-[11px] text-slate-500">0{i + 1}</div>
                  <h3 className="mt-1 font-semibold text-white">{s.t}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{s.d}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
