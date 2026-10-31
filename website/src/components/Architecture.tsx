import { ShieldCheck, Bug, FileCheck2, PackageCheck, GitBranch, Lock } from "lucide-react";
import { SectionHeader } from "./Features";
const LAYERS = [
  { name: "spectral_las", lang: "Python", desc: "Three subpackages: pointcloud, raster and adapters. Shared configuration and trajectory modules keep inputs explicit.", chips: ["pointcloud", "raster", "adapters"], border: "border-sky-400/30", dot: "bg-sky-400" },
  { name: "spectral_las._native", lang: "PyO3 extension", desc: "A private compiled module built with Maturin. Flight construction accepts normalized arrays and explicit image paths.", chips: ["Maturin", "PyO3", "NumPy"], border: "border-violet-400/30", dot: "bg-violet-400" },
  { name: "package/rust/src", lang: "Rust", desc: "Fusion, ENVI sampling and pushbroom geometry stay in cohesive modules. The native kernels use Rayon where implemented.", chips: ["fusion.rs", "hs.rs", "ortho.rs"], border: "border-orange-400/40", dot: "bg-orange-400" },
];
const QUALITY = [
  { icon: ShieldCheck, t: "Explicit resource limits", d: "Image windows, point chunks and scratch partitions control working buffers. Global painting state is not yet under a hard RAM budget." },
  { icon: Bug, t: "Build verification", d: "Release packaging checks cover wheel imports and source builds. Validate numerical results against your own acquisition data." },
  { icon: FileCheck2, t: "Declared geometry", d: "Coordinates, angles, time references and height datums are supplied explicitly rather than inferred silently." },
  { icon: PackageCheck, t: "Independent builds", d: "Root-level Maturin and Cargo configuration builds the Python/Rust package. Vite builds website/ separately." },
  { icon: GitBranch, t: "Small public API", d: "Version 0.1.0 is available on PyPI. This pre-alpha release does not imply a stable API or universal sensor support." },
  { icon: Lock, t: "Local processing core", d: "The included processing functions work with local files. Website hosting and project links are configured separately." },
];
export default function Architecture() {
  return (
    <section id="architecture" className="relative border-y border-white/5 bg-white/[0.015] py-24 lg:py-32">
      <div className="mx-auto grid max-w-7xl gap-16 px-5 lg:grid-cols-2 lg:px-8">
        <div>
          <SectionHeader
            center={false}
            eyebrow="Architecture"
            title="Python on the surface, Rust underneath"
            body="Python coordinates datasets and outputs. Rust handles the extracted projection kernels. The website is a separate frontend, not a package dependency."
          />
          <div className="mt-10 space-y-3">
            {LAYERS.map((l, i) => (
              <div key={l.name}>
                <div className={`rounded-xl border ${l.border} bg-[#0a0f1c] p-5`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2.5">
                      <span className={`h-2 w-2 rounded-full ${l.dot}`} />
                      <span className="font-mono text-sm font-semibold text-white">{l.name}</span>
                    </div>
                    <span className="rounded-full border border-white/10 px-2 py-0.5 text-[11px] text-slate-400">{l.lang}</span>
                  </div>
                  <p className="mt-2 text-sm text-slate-400">{l.desc}</p>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {l.chips.map((c) => (
                      <span key={c} className="rounded-md bg-white/5 px-2 py-0.5 font-mono text-[11px] text-slate-300">
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
                {i < LAYERS.length - 1 && (
                  <div className="flex justify-center py-1">
                    <div className="h-5 w-px bg-gradient-to-b from-white/20 to-white/5" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="lg:pt-24">
          <div className="grid gap-4 sm:grid-cols-2">
            {QUALITY.map((q) => (
              <div key={q.t} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-5 transition hover:border-white/15">
                <q.icon className="h-5 w-5 text-emerald-300" />
                <h3 className="mt-3 font-semibold text-white">{q.t}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{q.d}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-white/[0.07] bg-black/30 px-5 py-4 font-mono text-xs text-slate-400">
            <span>package/ → Python + Rust</span>
            <span>website/ → React + Vite</span>
            <span>No shared build outputs</span>
          </div>
        </div>
      </div>
    </section>
  );
}
