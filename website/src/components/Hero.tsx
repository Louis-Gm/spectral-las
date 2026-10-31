import { site } from "../site";
import { useState } from "react";
import { ArrowRight, Check, Copy, BookOpen } from "lucide-react";
import PointCloudDemo from "./PointCloudDemo";
export function CopyCommand({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          if (!navigator.clipboard || !window.isSecureContext) {
            window.prompt("Copy this command:", cmd);
            return;
          }
          await navigator.clipboard.writeText(cmd);
          setCopied(true);
          setTimeout(() => setCopied(false), 1600);
        } catch {
          setCopied(false);
          window.prompt("Copy this command:", cmd);
        }
      }}
      aria-label={copied ? "Command copied" : "Copy installation command"}
      className="group inline-flex items-center gap-3 rounded-lg border border-white/10 bg-black/40 px-4 py-2.5 font-mono text-sm text-slate-200 transition hover:border-white/20"
    >
      <span className="text-slate-500">$</span>
      <span>{cmd}</span>
      {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4 text-slate-500 group-hover:text-slate-300" />}
    </button>
  );
}
const BADGES = [
  { k: "status", v: "pre-alpha · PyPI", c: "bg-sky-500/80" },
  { k: "python", v: "3.10 – 3.12", c: "bg-indigo-500/80" },
  { k: "backend", v: "Rust / PyO3", c: "bg-orange-500/80" },
  { k: "package license", v: "Apache-2.0", c: "bg-slate-500/80" },
];
export default function Hero() {
  return (
    <section className="relative overflow-hidden pt-32 pb-20 lg:pt-40 lg:pb-28">
      <div className="grid-bg pointer-events-none absolute inset-0 [mask-image:radial-gradient(ellipse_at_top,black_30%,transparent_75%)]" />
      <div className="pointer-events-none absolute -top-40 left-1/2 h-[520px] w-[900px] -translate-x-1/2 rounded-full bg-indigo-600/20 blur-[120px]" />
      <div className="pointer-events-none absolute top-40 right-0 h-[300px] w-[400px] rounded-full bg-cyan-500/10 blur-[100px]" />
      <div className="relative mx-auto grid max-w-7xl items-center gap-14 px-5 lg:grid-cols-[1.05fr_1fr] lg:px-8">
        <div>
          <a
            href="#architecture"
            className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 py-1 pl-1 pr-3 text-xs text-slate-300 transition hover:border-white/20"
          >
            <span className="spectral-bg rounded-full px-2 py-0.5 font-semibold text-white">New</span>
            A focused Python package with a Rust backend
            <ArrowRight className="h-3 w-3" />
          </a>
          <h1 className="mt-6 text-4xl font-bold leading-[1.08] tracking-tight text-white sm:text-5xl lg:text-[3.6rem]">
          Bring <span className="spectral-text">full spectrum color</span>
          <br />
          to your point clouds.
          </h1>

          <p className="mt-6 max-w-xl text-lg leading-relaxed text-slate-400">
            Spectral LAS allows for flexible point cloud colorization workflows using either forward or reverse projection methods. 
            <br />
            The package is designed to work with any image type, including panchromatic, RGB, multispectral and hyperspectral imagery. 
            Colorization using derivative raster products such as spectral indices is also supported.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <CopyCommand cmd={site.installCommand} />
            <a
              href="#quickstart"
              className="inline-flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-slate-900 transition hover:bg-slate-200"
            >
              Quickstart <ArrowRight className="h-4 w-4" />
            </a>
            <a
              href={site.docsUrl}
              className="inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium text-slate-300 transition hover:text-white"
            >
              <BookOpen className="h-4 w-4" /> Read the docs
            </a>
          </div>
          <div className="mt-8 flex flex-wrap gap-2">
            {BADGES.map((b) => (
              <span key={b.k} className="inline-flex overflow-hidden rounded text-[11px] font-medium">
                <span className="bg-slate-700/80 px-2 py-0.5 text-slate-200">{b.k}</span>
                <span className={`${b.c} px-2 py-0.5 text-white`}>{b.v}</span>
              </span>
            ))}
          </div>
        </div>
        <div className="relative">
          <PointCloudDemo />
        </div>
      </div>
    </section>
  );
}
