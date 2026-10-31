import { site } from "../site";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { GithubIcon as Github } from "./icons";
import { Logo } from "./Logo";
const LINKS = [
  { href: "#features", label: "Features" },
  { href: "#quickstart", label: "Quickstart" },
  
  { href: "#architecture", label: "Architecture" },
  { href: "#research", label: "Research" },
  { href: "#docs", label: "Docs" },
];
export default function Navbar() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-all ${
        scrolled ? "border-b border-white/5 bg-[#060912]/80 backdrop-blur-xl" : "bg-transparent"
      }`}
    >
      <nav className="mx-auto flex h-16 max-w-7xl items-center justify-between px-5 lg:px-8">
        <a href="#top" className="flex items-center gap-3">
          <Logo />
          <span className="hidden rounded-full border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[10px] text-slate-400 sm:inline">
            {site.version}
          </span>
        </a>
        <div className="hidden items-center gap-1 lg:flex">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} className="rounded-md px-3 py-2 text-sm text-slate-400 transition hover:text-white">
              {l.label}
            </a>
          ))}
        </div>
        <div className="hidden items-center gap-3 lg:flex">
          {site.repositoryUrl && (
            <a href={site.repositoryUrl} className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-slate-300">
              <Github className="h-4 w-4" /> GitHub
            </a>
          )}
          <a href="#quickstart" className="rounded-lg bg-white px-4 py-1.5 text-sm font-semibold text-slate-900 transition hover:bg-slate-200">
            Get started
          </a>
        </div>
        <button type="button" className="text-slate-300 lg:hidden" onClick={() => setOpen(!open)} aria-label="Toggle menu" aria-expanded={open} aria-controls="mobile-navigation">
          {open ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </nav>
      {open && (
        <div id="mobile-navigation" className="border-t border-white/5 bg-[#060912]/95 px-5 pb-5 backdrop-blur-xl lg:hidden">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} onClick={() => setOpen(false)} className="block py-3 text-slate-300">
              {l.label}
            </a>
          ))}
          <a href="#quickstart" onClick={() => setOpen(false)} className="mt-2 block rounded-lg bg-white px-4 py-2.5 text-center font-semibold text-slate-900">
            Get started
          </a>
        </div>
      )}
    </header>
  );
}
