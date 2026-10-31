import { BookOpen, FileText, ArrowRight } from "lucide-react";
import { LogoMark, Logo } from "./Logo";
import { CopyCommand } from "./Hero";
import { GithubIcon } from "./icons";
import { site } from "../site";
const FORMATS = {
  "Point clouds": ["LAS", "LAZ (optional backend)", "Row-aligned spectra"],
  "Images": ["ENVI BIL/BIP/BSQ", "GeoTIFF", "Rasterio-supported window I/O"],
  "Core dependencies": ["NumPy", "laspy", "Rasterio", "pyproj", "PyO3", "Rayon"],
};
export function Ecosystem() {
  return <section className="border-y border-white/5 bg-white/[0.015] py-20">
    <div className="mx-auto grid max-w-7xl gap-10 px-5 md:grid-cols-3 lg:px-8">
      {Object.entries(FORMATS).map(([name, formats]) => <div key={name}>
        <p className="font-mono text-xs uppercase tracking-widest text-slate-500">{name}</p>
        <div className="mt-4 flex flex-wrap gap-2">{formats.map((format) => <span key={format} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-sm text-slate-300">{format}</span>)}</div>
      </div>)}
    </div>
  </section>;
}
export function DocsCTA() {
  return <section id="docs" className="relative py-24 lg:py-32">
    <div className="mx-auto max-w-7xl px-5 lg:px-8">
      <div className="grid gap-4 sm:grid-cols-2">
        <a href={site.docsUrl} className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <BookOpen className="h-5 w-5 text-cyan-300" /><h3 className="mt-4 font-semibold text-white">Source guide <ArrowRight className="inline h-4 w-4" /></h3>
          <p className="mt-2 text-sm text-slate-400">Build instructions, supported operations and current limitations.</p>
        </a>
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <FileText className="h-5 w-5 text-cyan-300" /><h3 className="mt-4 font-semibold text-white">Separate developer workspaces</h3>
          <p className="mt-2 text-sm text-slate-400">package/ contains Python and Rust. website/ contains this frontend. Build manifests and project documents live at the repository root; website/ has an independent frontend build.</p>
        </div>
      </div>
      <div className="spectral-border relative mt-16 overflow-hidden rounded-3xl bg-[#0a0f1c] px-6 py-16 text-center">
        <LogoMark width={150} className="mx-auto" />
        <h2 className="mt-6 text-3xl font-bold tracking-tight text-white">Start with SpectralLAS</h2>
        <p className="mx-auto mt-4 max-w-xl text-lg text-slate-400">The processing package is Apache-2.0. Install version 0.1.0 from PyPI using Python 3.10–3.12. Matching wheels avoid a local Rust build; source builds require Rust.</p>
        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          <CopyCommand cmd={site.installCommand} />
          {site.repositoryUrl && <a href={site.repositoryUrl} className="inline-flex items-center gap-2 rounded-lg border border-white/10 px-5 py-2.5 text-white"><GithubIcon /> Repository</a>}
        </div>
      </div>
    </div>
  </section>;
}
export function Footer() {
  return <footer className="border-t border-white/5 px-5 py-12">
    <div className="mx-auto flex max-w-7xl flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
      <div><Logo /><p className="mt-3 text-sm text-slate-500">Spectral imagery and point-cloud processing with a Rust core.</p></div>
      <div className="flex gap-6 text-sm text-slate-400"><a href={site.docsUrl}>Source guide</a><a href="#quickstart">Quickstart</a><a href={site.pypiUrl}>PyPI</a>{site.repositoryUrl && <a href={site.repositoryUrl}>Repository</a>}</div>
    </div>
  </footer>;
}
