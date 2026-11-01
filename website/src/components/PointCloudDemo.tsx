import { useEffect, useRef, useState } from "react";

type Mode = "geometry" | "truecolor" | "cir" | "ndvi";

const MODES: { id: Mode; label: string; desc: string; call: string }[] = [
  { id: "geometry", label: "Raw XYZ", desc: "Uncolored LiDAR geometry, shaded by elevation", call: "cloud.xyz" },
  { id: "truecolor", label: "True color", desc: "Bands 640 / 550 / 460 nm → R G B", call: "Synthetic RGB display; not a package viewer API" },
  { id: "cir", label: "CIR", desc: "Color-infrared: 850 / 650 / 550 nm → R G B", call: "Synthetic CIR display; not a package viewer API" },
  { id: "ndvi", label: "NDVI", desc: "(NIR − Red) / (NIR + Red), per point", call: "Synthetic NDVI display; not measured processing output" },
];

// Bands: B(460) G(550) R(650) RE(720) NIR(850)
const MATERIALS: number[][] = [
  [0.04, 0.1, 0.05, 0.25, 0.5], // 0 vegetation
  [0.05, 0.12, 0.07, 0.28, 0.45], // 1 grass
  [0.12, 0.16, 0.22, 0.26, 0.3], // 2 soil
  [0.09, 0.08, 0.04, 0.02, 0.01], // 3 water
  [0.08, 0.1, 0.3, 0.34, 0.38], // 4 roof
  [0.28, 0.3, 0.31, 0.32, 0.34], // 5 concrete
  [0.07, 0.08, 0.085, 0.09, 0.1], // 6 asphalt
  [0.05, 0.07, 0.1, 0.15, 0.22], // 7 bark
];

function mulberry32(a: number) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Scene {
  n: number;
  pos: Float32Array;
  spec: Float32Array;
}

function buildScene(): Scene {
  const rnd = mulberry32(7);
  const pos: number[] = [];
  const spec: number[] = [];

  const ground = (x: number, z: number) =>
    0.07 * Math.sin(2.3 * x) * Math.cos(1.7 * z) +
    0.04 * Math.sin(4 * x + 1.3) * Math.sin(3 * z);

  const push = (x: number, y: number, z: number, mat: number, mod = 0) => {
    pos.push(x, y, z);

    const base = MATERIALS[mat];
    for (let b = 0; b < 5; b++) {
      let v = base[b] * (0.88 + rnd() * 0.24);

      if (mod !== 0 && (mat === 0 || mat === 1)) {
        // stress: red up, NIR down
        if (b === 2) v += mod * 0.12;
        if (b >= 3) v -= mod * 0.22;
        if (b === 1) v += mod * 0.03;
      }

      spec.push(Math.max(0.005, v));
    }
  };

  const riverZ = (x: number) => 0.32 * Math.sin(2.2 * x) + 0.5;
  const inBuilding = (x: number, z: number) =>
    x > 0.12 && x < 0.62 && z > -0.68 && z < -0.22;

  // terrain
  const G = 74;
  for (let i = 0; i < G; i++) {
    for (let j = 0; j < G; j++) {
      const x = -1 + (2 * (i + rnd() * 0.9)) / G;
      const z = -1 + (2 * (j + rnd() * 0.9)) / G;

      if (inBuilding(x, z)) continue;

      let y = ground(x, z);
      let mat = 1;
      let mod = 0;

      if (Math.abs(z - riverZ(x)) < 0.085) {
        mat = 3;
        y = -0.09 + rnd() * 0.004;
      } else if (Math.abs(x + 0.45 - 0.08 * z) < 0.055) {
        mat = 6;
      } else if (
        Math.sin(5 * x + 0.5) * Math.cos(6 * z) + rnd() * 0.35 >
        0.62
      ) {
        mat = 2;
      } else {
        mod = Math.max(0, Math.sin(3 * x - 2 * z) * 0.5 - 0.1);
      }

      push(x, y, z, mat, mod);
    }
  }

  // building
  const bx0 = 0.15,
    bx1 = 0.6,
    bz0 = -0.65,
    bz1 = -0.25;
  const base = ground((bx0 + bx1) / 2, (bz0 + bz1) / 2);
  const top = base + 0.3;

  for (let i = 0; i <= 22; i++) {
    for (let j = 0; j <= 20; j++) {
      const x = bx0 + ((bx1 - bx0) * i) / 22;
      const z = bz0 + ((bz1 - bz0) * j) / 20;
      const ridge =
        0.06 *
        (1 - Math.abs((z - (bz0 + bz1) / 2) / ((bz1 - bz0) / 2)));

      push(x, top + ridge, z, 4);
    }
  }

  for (let k = 0; k < 520; k++) {
    const side = Math.floor(rnd() * 4);
    const t = rnd();
    const y = base + rnd() * (top - base);
    let x = 0,
      z = 0;

    if (side === 0) {
      x = bx0 + t * (bx1 - bx0);
      z = bz0;
    }
    if (side === 1) {
      x = bx0 + t * (bx1 - bx0);
      z = bz1;
    }
    if (side === 2) {
      x = bx0;
      z = bz0 + t * (bz1 - bz0);
    }
    if (side === 3) {
      x = bx1;
      z = bz0 + t * (bz1 - bz0);
    }

    push(x, y, z, 5);
  }

  // trees
  const trees: [number, number, number][] = [];
  let tries = 0;

  while (trees.length < 13 && tries < 400) {
    tries++;

    const x = -0.9 + rnd() * 1.8;
    const z = -0.9 + rnd() * 1.8;

    if (inBuilding(x, z) || (x > 0.0 && x < 0.75 && z > -0.8 && z < -0.1))
      continue;
    if (Math.abs(z - riverZ(x)) < 0.2) continue;
    if (Math.abs(x + 0.45 - 0.08 * z) < 0.16) continue;
    if (trees.some(([tx, tz]) => Math.hypot(tx - x, tz - z) < 0.24)) continue;

    trees.push([x, z, trees.length % 5 === 3 ? 1 : 0]);
  }

  for (const [tx, tz, stressed] of trees) {
    const gy = ground(tx, tz);
    const r = 0.09 + rnd() * 0.06;
    const cy = gy + 0.16 + r;

    for (let k = 0; k < 30; k++)
      push(
        tx + (rnd() - 0.5) * 0.015,
        gy + rnd() * 0.18,
        tz + (rnd() - 0.5) * 0.015,
        7
      );

    for (let k = 0; k < 300; k++) {
      const u = rnd() * 2 - 1;
      const th = rnd() * Math.PI * 2;
      const rr = r * (0.7 + 0.3 * Math.cbrt(rnd()));
      const s = Math.sqrt(1 - u * u);

      push(
        tx + rr * s * Math.cos(th),
        cy + rr * u * 0.95,
        tz + rr * s * Math.sin(th),
        0,
        stressed ? 0.75 + rnd() * 0.25 : rnd() * 0.12
      );
    }
  }

  return {
    n: pos.length / 3,
    pos: new Float32Array(pos),
    spec: new Float32Array(spec),
  };
}

function lerpStops(
  v: number,
  stops: [number, number, number, number][]
) {
  if (v <= stops[0][0]) return stops[0].slice(1) as number[];

  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const a = stops[i - 1],
        b = stops[i];
      const t = (v - a[0]) / (b[0] - a[0]);

      return [
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
        a[3] + (b[3] - a[3]) * t,
      ];
    }
  }

  return stops[stops.length - 1].slice(1) as number[];
}

const NDVI_STOPS: [number, number, number, number][] = [
  [-0.7, 40, 90, 200],
  [-0.05, 120, 110, 120],
  [0.12, 170, 110, 60],
  [0.3, 225, 200, 80],
  [0.5, 140, 210, 70],
  [0.8, 15, 130, 60],
];

function computeColors(
  scene: Scene,
  mode: Mode
): Uint8ClampedArray {
  const { n, pos, spec } = scene;
  const out = new Uint8ClampedArray(n * 3);
  const tone = (v: number, k: number) =>
    255 * Math.pow(Math.min(1, v * k), 0.6);

  for (let i = 0; i < n; i++) {
    const s = i * 5;
    const B = spec[s],
      Gr = spec[s + 1],
      R = spec[s + 2],
      NIR = spec[s + 4];

    let r = 0,
      g = 0,
      b = 0;

    if (mode === "geometry") {
      const y = pos[i * 3 + 1];
      const v = 70 + (y + 0.12) * 330;
      r = v * 0.85;
      g = v * 0.92;
      b = v * 1.08;
    } else if (mode === "truecolor") {
      r = tone(R, 2.6);
      g = tone(Gr, 2.6);
      b = tone(B, 2.6);
    } else if (mode === "cir") {
      r = tone(NIR, 1.7);
      g = tone(R, 2.8);
      b = tone(Gr, 2.8);
    } else {
      const ndvi = (NIR - R) / (NIR + R);
      [r, g, b] = lerpStops(ndvi, NDVI_STOPS);
    }

    out[i * 3] = r;
    out[i * 3 + 1] = g;
    out[i * 3 + 2] = b;
  }

  return out;
}

export default function PointCloudDemo() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>("cir");
  const [fps, setFps] = useState(60);

  const stateRef = useRef({
    scene: null as Scene | null,
    colors: null as Uint8ClampedArray | null,
    prev: null as Uint8ClampedArray | null,
    changeT: 0,
    yaw: 0.7,
    pitch: 0.55,
    dragging: false,
    lastX: 0,
    lastY: 0,
    cache: {} as Partial<Record<Mode, Uint8ClampedArray>>,
  });

  // mode change
  useEffect(() => {
    const st = stateRef.current;
    if (!st.scene) st.scene = buildScene();
    if (!st.cache[mode]) st.cache[mode] = computeColors(st.scene, mode);

    st.prev = st.colors ?? st.cache[mode]!;
    st.colors = st.cache[mode]!;
    st.changeT = performance.now();
  }, [mode]);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const wrap = wrapRef.current!;
    const ctx = canvas.getContext("2d")!;
    const st = stateRef.current;

    if (!st.scene) st.scene = buildScene();

    let W = 0,
      H = 0,
      dpr = 1;
    let img: ImageData | null = null;
    let depth = new Float32Array(1);
    let raf = 0;
    let frames = 0,
      fpsT = performance.now();

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = wrap.getBoundingClientRect();

      W = Math.max(1, Math.floor(rect.width * dpr));
      H = Math.max(1, Math.floor(rect.height * dpr));

      canvas.width = W;
      canvas.height = H;
      img = ctx.createImageData(W, H);
      depth = new Float32Array(W * H);
    };

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    const render = (now: number) => {
      raf = requestAnimationFrame(render);

      if (!img || !st.scene || !st.colors) return;

      if (!st.dragging) st.yaw += 0.0028;

      const { n, pos } = st.scene;
      const data = img.data;

      data.fill(0);
      depth.fill(1e9);

      const cy = Math.cos(st.yaw),
        sy = Math.sin(st.yaw);
      const cp = Math.cos(st.pitch),
        sp = Math.sin(st.pitch);

      const D = 3.3;
      const F = Math.min(W, H * 1.35) * 1.18;

      // Orthographic projection: constant screen scale regardless of depth.
      const projectionScale = F / D;

      const t = Math.min(1.25, (now - st.changeT) / 1100);
      const sweep = t * 2.5 - 1.25; // x threshold from -1.25 to 1.875

      const colors = st.colors,
        prev = st.prev ?? colors;

      for (let i = 0; i < n; i++) {
        const x = pos[i * 3],
          y = pos[i * 3 + 1],
          z = pos[i * 3 + 2];

        const x1 = x * cy - z * sy;
        const z1 = x * sy + z * cy;
        const y2 = y * cp - z1 * sp;
        const z2 = y * sp + z1 * cp;

        const zz = z2 + D;

        const sx = Math.round(W / 2 + x1 * projectionScale);
        const syy = Math.round(H * 0.52 - y2 * projectionScale);

        // Fixed point size; depth is used only for occlusion.
        const ps = Math.max(1, Math.round(dpr * 2.3));

        const useNew = x < sweep;
        const src = useNew ? colors : prev;

        let r = src[i * 3],
          g = src[i * 3 + 1],
          b = src[i * 3 + 2];

        const shade = 1.12 - (z2 + 1.2) * 0.17;
        r *= shade;
        g *= shade;
        b *= shade;

        const dist = Math.abs(x - sweep);
        if (t < 1.25 && dist < 0.05) {
          const k = 1 - dist / 0.05;
          r += (255 - r) * k;
          g += (255 - g) * k;
          b += (255 - b) * k;
        }

        for (let dy = 0; dy < ps; dy++) {
          const py = syy + dy;
          if (py < 0 || py >= H) continue;

          for (let dx = 0; dx < ps; dx++) {
            const px = sx + dx;
            if (px < 0 || px >= W) continue;

            const idx = py * W + px;

            if (zz < depth[idx]) {
              depth[idx] = zz;

              const o = idx * 4;
              data[o] = r;
              data[o + 1] = g;
              data[o + 2] = b;
              data[o + 3] = 255;
            }
          }
        }
      }

      ctx.putImageData(img, 0, 0);

      frames++;
      if (now - fpsT > 1000) {
        setFps(Math.round((frames * 1000) / (now - fpsT)));
        frames = 0;
        fpsT = now;
      }
    };

    raf = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  const onDown = (e: React.PointerEvent) => {
    const st = stateRef.current;
    st.dragging = true;
    st.lastX = e.clientX;
    st.lastY = e.clientY;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onMove = (e: React.PointerEvent) => {
    const st = stateRef.current;
    if (!st.dragging) return;

    st.yaw += (e.clientX - st.lastX) * 0.008;
    st.pitch = Math.max(
      0.1,
      Math.min(1.3, st.pitch + (e.clientY - st.lastY) * 0.006)
    );

    st.lastX = e.clientX;
    st.lastY = e.clientY;
  };

  const onUp = () => {
    stateRef.current.dragging = false;
  };

  const current = MODES.find((m) => m.id === mode)!;
  const n = stateRef.current.scene?.n ?? 0;

  return (
    <div className="spectral-border relative rounded-2xl bg-[#0a0f1c]/90 shadow-2xl shadow-indigo-950/40 backdrop-blur">
      <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-slate-700" />
          <span className="h-2.5 w-2.5 rounded-full bg-slate-700" />
          <span className="h-2.5 w-2.5 rounded-full bg-slate-700" />
          <span className="ml-3 font-mono text-xs text-slate-300">
            Synthetic browser demonstration
          </span>
        </div>
        <span className="font-mono text-[11px] text-emerald-400/80">
          ● live · {fps} fps
        </span>
      </div>

      <div className="flex flex-wrap gap-1 border-b border-white/5 p-2">
        {MODES.map((m) => (
          <button
            key={m.id}
            onClick={() => setMode(m.id)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              mode === m.id
                ? "bg-white/10 text-white ring-1 ring-white/15"
                : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div
        ref={wrapRef}
        className="relative h-[320px] cursor-grab touch-none select-none active:cursor-grabbing sm:h-[380px]"
        style={{
          background:
            "radial-gradient(ellipse at 50% 40%, rgba(49,46,129,0.25), transparent 70%)",
        }}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={onUp}
      >
        <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />

        <div className="pointer-events-none absolute left-3 top-3 rounded-md bg-black/40 px-2 py-1 font-mono text-[10px] text-[#f2de83] backdrop-blur">
          drag to orbit
        </div>

        {mode === "ndvi" && (
          <div className="pointer-events-none absolute bottom-3 right-3 w-36 rounded-md bg-black/50 p-2 backdrop-blur">
            <div
              className="h-1.5 rounded-full"
              style={{
                background:
                  "linear-gradient(90deg, rgb(40,90,200), rgb(120,110,120), rgb(170,110,60), rgb(225,200,80), rgb(140,210,70), rgb(15,130,60))",
              }}
            />
            <div className="mt-1 flex justify-between font-mono text-[9px] text-slate-400">
              <span>-0.7</span>
              <span>0</span>
              <span>0.8</span>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-white/5 px-4 py-3">
        <div className="font-mono text-[12px] text-slate-300">
          <span className="text-slate-500">&gt;&gt;&gt; </span>
          {current.call}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
          <span>{current.desc}</span>
          <span className="font-mono">
            {n.toLocaleString()} pts · 5 bands (demo)
          </span>
        </div>
      </div>
    </div>
  );
}