import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
type Lang = "python" | "rust" | "bash" | "bibtex" | "yaml";
const KW: Record<Lang, string[]> = {
  python: ["import", "from", "as", "def", "return", "for", "in", "with", "if", "else", "None", "True", "False", "lambda", "class", "print"],
  rust: ["use", "fn", "let", "mut", "pub", "struct", "impl", "match", "Ok", "Err", "Result", "self", "true", "false"],
  bash: ["pip", "conda", "uv", "cargo", "spectral-las", "install", "add"],
  bibtex: [],
  yaml: [],
};
function highlight(code: string, lang: Lang): ReactNode[] {
  const kw = KW[lang].join("|");
  const commentRe = lang === "rust" ? "\\/\\/.*$" : lang === "bibtex" ? "%.*$" : "#.*$";
  const parts = [
    `(${commentRe})`,
    `("(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*')`,
    `(\\b\\d[\\d_]*(?:\\.\\d+)?(?:e\\d+)?%?\\b)`,
    kw ? `(\\b(?:${kw})\\b)` : `(\\b\\B)`,
    `([A-Za-z_][\\w]*)(?=\\s*[(!])`,
    `(@\\w+|✓|\\$ )`,
    `(\\b[a-z_]+)(?==[^=])`,
  ];
  const re = new RegExp(parts.join("|"), "gm");
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(code))) {
    if (m[0].length === 0) {
      re.lastIndex++;
      continue;
    }
    if (m.index > last) out.push(code.slice(last, m.index));
    const [, com, str, num, key, fn, special, kwarg] = m;
    let cls = "";
    if (com) cls = "text-slate-500 italic";
    else if (str) cls = "text-emerald-300";
    else if (num) cls = "text-amber-300";
    else if (key) cls = "text-violet-400";
    else if (fn) cls = "text-sky-300";
    else if (special) cls = special.startsWith("✓") ? "text-emerald-400" : special.startsWith("$") ? "text-slate-500" : "text-orange-300";
    else if (kwarg) cls = "text-rose-300";
    out.push(
      <span key={k++} className={cls}>
        {m[0]}
      </span>
    );
    last = m.index + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  return out;
}
export default function CodeBlock({
  code,
  lang,
  filename,
  className = "",
}: {
  code: string;
  lang: Lang;
  filename?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className={`overflow-hidden rounded-xl border border-white/10 bg-[#0b1020] ${className}`}>
      <div className="flex items-center justify-between border-b border-white/5 bg-white/[0.02] px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]/80" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]/80" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]/80" />
          {filename && <span className="ml-3 font-mono text-xs text-slate-400">{filename}</span>}
        </div>
        <button
          onClick={() => {
            navigator.clipboard?.writeText(code);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-slate-400 transition hover:bg-white/5 hover:text-white"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="code-scroll overflow-x-auto p-5 font-mono text-[13px] leading-[1.7] text-slate-200">
        <code>{highlight(code, lang)}</code>
      </pre>
    </div>
  );
}
