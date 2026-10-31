import { GraduationCap, Building2, Check } from "lucide-react";
import { SectionHeader } from "./Features";
const GROUPS = [
  { title: "For researchers", icon: GraduationCap, items: [
    "Explicit camera, trajectory and time conventions",
    "Spectral rows remain paired with point-cloud rows",
    "Image expressions and point-level transformation callbacks",
    "Documented limits rather than unverified accuracy claims",
  ] },
  { title: "For developers", icon: Building2, items: [
    "Three focused Python subpackages",
    "A private Rust extension built through Maturin",
    "Vendor interpretation isolated in adapters",
    "A separate website build with no Python dependency",
  ] },
];
export default function Audiences() {
  return (
    <section id="research" className="relative py-24 lg:py-32">
      <div className="mx-auto max-w-7xl px-5 lg:px-8">
        <SectionHeader eyebrow="Who it is for" title="A clear foundation for spectral-spatial workflows"
          body="Validate processing against your acquisition data before treating outputs as scientific or production deliverables." />
        <div className="mt-14 grid gap-6 lg:grid-cols-2">
          {GROUPS.map((group) => (
            <div key={group.title} className="rounded-2xl border border-white/10 bg-white/[0.02] p-8">
              <group.icon className="h-6 w-6 text-cyan-300" />
              <h3 className="mt-4 text-xl font-semibold text-white">{group.title}</h3>
              <ul className="mt-6 space-y-3">
                {group.items.map((item) => <li key={item} className="flex gap-3 text-slate-300"><Check className="mt-1 h-4 w-4 shrink-0 text-cyan-300" />{item}</li>)}
              </ul>
            </div>
          ))}
        </div>
        <p className="mt-8 text-sm text-slate-500">Project citation metadata lives in CITATION.cff. No publication DOI, endorsements or commercial support commitments are claimed by this project.</p>
      </div>
    </section>
  );
}
