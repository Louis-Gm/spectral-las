export const SPECTRUM = ["#7c3aed", "#3b82f6", "#22c55e", "#eab308", "#ef4444"];
/**
 * Five points in a row, stepping through the spectrum.
 * `width` is the full width of the mark; height is width / 5.
 */
export function LogoMark({ width = 70, className = "" }: { width?: number; className?: string }) {
  const h = width / 5;
  return (
    <svg
      width={width}
      height={h}
      viewBox="0 0 100 20"
      className={className}
      role="img"
      aria-label="Spectral LAS logo"
    >
      {SPECTRUM.map((c, i) => (
        <circle key={c} cx={10 + i * 20} cy={10} r={7} fill={c} />
      ))}
    </svg>
  );
}
export function Logo({ width = 62, showText = true }: { width?: number; showText?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark width={width} />
      {showText && (
        <span className="text-[1.15rem] font-semibold tracking-tight text-white">
          Spectral <span className="spectral-text">LAS</span>
        </span>
      )}
    </span>
  );
}
