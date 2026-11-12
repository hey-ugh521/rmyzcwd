// 程序化生成预览图 / 音频（仅 mock 模式）。不依赖任何外部资源 / CDN。
// 预览图：由 seed + prompt 推导的确定性抽象作品；音频：柔和正弦和弦 WAV。

export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type RGB = [number, number, number];
interface Palette { bg: RGB; hues: RGB[]; name: string }

const PALETTES: Record<string, Palette> = {
  sunset: { name: "落日", bg: [26, 16, 38], hues: [[255, 122, 89], [255, 77, 141], [255, 196, 92], [139, 124, 255]] },
  neon: { name: "霓虹都市", bg: [10, 12, 26], hues: [[34, 211, 238], [255, 77, 141], [139, 124, 255], [96, 165, 250]] },
  forest: { name: "林间", bg: [12, 24, 20], hues: [[74, 222, 128], [34, 211, 238], [163, 230, 53], [251, 191, 36]] },
  portrait: { name: "人像", bg: [22, 16, 32], hues: [[255, 77, 141], [167, 156, 255], [255, 196, 92], [233, 234, 244]] },
  ocean: { name: "深海", bg: [8, 18, 32], hues: [[34, 211, 238], [96, 165, 250], [74, 222, 128], [139, 124, 255]] },
  mono: { name: "墨白", bg: [16, 17, 22], hues: [[233, 234, 244], [150, 155, 178], [139, 124, 255], [100, 105, 128]] },
  bloom: { name: "花丛", bg: [26, 14, 28], hues: [[255, 77, 141], [255, 154, 162], [251, 191, 36], [167, 156, 255]] },
};

function pickPalette(prompt: string, rnd: () => number): Palette {
  const p = prompt.toLowerCase();
  const keys = Object.keys(PALETTES);
  if (/sunset|dusk|黄昏|落日|夕阳|golden/.test(p)) return PALETTES.sunset;
  if (/city|neon|street|都市|霓虹|夜|cyber|街/.test(p)) return PALETTES.neon;
  if (/forest|tree|nature|green|林|树|自然|山/.test(p)) return PALETTES.forest;
  if (/girl|portrait|face|人|少女|肖像|character|1girl/.test(p)) return PALETTES.portrait;
  if (/sea|ocean|water|海|水|浪|underwater/.test(p)) return PALETTES.ocean;
  if (/flower|bloom|rose|花|樱|spring/.test(p)) return PALETTES.bloom;
  if (/mono|ink|sketch|black|white|墨|素描|黑白/.test(p)) return PALETTES.mono;
  return PALETTES[keys[Math.floor(rnd() * keys.length)]];
}

const rgba = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export interface ArtOptions { seed: number; prompt: string; width: number; height: number }

/** 生成确定性抽象作品预览（长边 capped 到 720 以控制性能）。 */
export function generateArtwork({ seed, prompt, width, height }: ArtOptions): string {
  const long = 720;
  const scale = Math.min(1, long / Math.max(width, height));
  const w = Math.max(64, Math.round(width * scale));
  const h = Math.max(64, Math.round(height * scale));
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d")!;
  const rnd = mulberry32(seed ^ hashString(prompt));
  const pal = pickPalette(prompt, rnd);
  const style = Math.floor(rnd() * 4);

  // 背景渐变
  const bg = ctx.createLinearGradient(0, 0, w, h);
  bg.addColorStop(0, rgba(pal.bg, 1));
  bg.addColorStop(1, rgba(pal.hues[0], 0.22));
  ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

  // 大色域光晕
  for (let i = 0; i < 3; i++) {
    const c = pal.hues[Math.floor(rnd() * pal.hues.length)];
    const gx = rnd() * w, gy = rnd() * h, gr = (0.3 + rnd() * 0.5) * Math.max(w, h);
    const g = ctx.createRadialGradient(gx, gy, 0, gx, gy, gr);
    g.addColorStop(0, rgba(c, 0.5)); g.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
  }

  ctx.globalCompositeOperation = "lighter";
  if (style === 0) {
    // 流动丝带
    for (let i = 0; i < 26; i++) {
      const c = pal.hues[i % pal.hues.length];
      ctx.strokeStyle = rgba(c, 0.1 + rnd() * 0.18);
      ctx.lineWidth = 1 + rnd() * 3;
      ctx.beginPath();
      const y0 = rnd() * h, amp = 20 + rnd() * 120, freq = 0.004 + rnd() * 0.01, ph = rnd() * 6;
      ctx.moveTo(0, y0);
      for (let x = 0; x <= w; x += 8) ctx.lineTo(x, y0 + Math.sin(x * freq + ph) * amp);
      ctx.stroke();
    }
  } else if (style === 1) {
    // 散景光斑
    for (let i = 0; i < 60; i++) {
      const c = pal.hues[Math.floor(rnd() * pal.hues.length)];
      const x = rnd() * w, y = rnd() * h, r = 4 + rnd() * (Math.min(w, h) * 0.16);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, rgba(c, 0.4)); g.addColorStop(0.7, rgba(c, 0.12)); g.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    }
  } else if (style === 2) {
    // 几何网格
    const cell = Math.max(28, Math.min(w, h) / (5 + Math.floor(rnd() * 5)));
    for (let x = 0; x < w; x += cell) {
      for (let y = 0; y < h; y += cell) {
        if (rnd() > 0.55) continue;
        const c = pal.hues[Math.floor(rnd() * pal.hues.length)];
        ctx.fillStyle = rgba(c, 0.08 + rnd() * 0.22);
        const pad = cell * 0.14, s = cell - pad * 2;
        if (rnd() > 0.5) { ctx.beginPath(); ctx.arc(x + cell / 2, y + cell / 2, s / 2, 0, 7); ctx.fill(); }
        else ctx.fillRect(x + pad, y + pad, s, s);
      }
    }
  } else {
    // 层叠山峦 / 波
    for (let layer = 0; layer < 5; layer++) {
      const c = pal.hues[layer % pal.hues.length];
      ctx.fillStyle = rgba(c, 0.14 + layer * 0.05);
      ctx.beginPath(); ctx.moveTo(0, h);
      const baseY = h * (0.4 + layer * 0.12), amp = 20 + rnd() * 60, freq = 0.006 + rnd() * 0.008, ph = rnd() * 6;
      for (let x = 0; x <= w; x += 6) ctx.lineTo(x, baseY + Math.sin(x * freq + ph) * amp);
      ctx.lineTo(w, h); ctx.closePath(); ctx.fill();
    }
  }
  ctx.globalCompositeOperation = "source-over";

  // 颗粒
  const grain = Math.floor((w * h) / 90);
  for (let i = 0; i < grain; i++) {
    ctx.fillStyle = rgba([255, 255, 255], rnd() * 0.04);
    ctx.fillRect(rnd() * w, rnd() * h, 1, 1);
  }
  // 暗角
  const vig = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.3, w / 2, h / 2, Math.max(w, h) * 0.75);
  vig.addColorStop(0, "rgba(0,0,0,0)"); vig.addColorStop(1, "rgba(0,0,0,0.45)");
  ctx.fillStyle = vig; ctx.fillRect(0, 0, w, h);

  return cv.toDataURL("image/png");
}

/** 生成柔和和弦 WAV（data URL），由文本确定性推导旋律。 */
export function generateWav(text: string, voice: string, speed = 1): string {
  const sr = 22050;
  const rnd = mulberry32(hashString(text + voice));
  const notes = Math.max(3, Math.min(8, Math.ceil(text.length / 6)));
  const dur = Math.max(1.2, Math.min(4, (notes * 0.34) / Math.max(0.5, speed)));
  const n = Math.floor(sr * dur);
  const scale = [0, 3, 5, 7, 10, 12, 15, 17]; // 五声色彩
  const root = 220 * Math.pow(2, Math.floor(rnd() * 5) / 12);
  const data = new Float32Array(n);
  for (let i = 0; i < notes; i++) {
    const semi = scale[Math.floor(rnd() * scale.length)];
    const freq = root * Math.pow(2, semi / 12);
    const start = Math.floor((i / notes) * n);
    const len = Math.floor((n / notes) * 1.6);
    for (let j = 0; j < len && start + j < n; j++) {
      const t = j / sr;
      const env = Math.min(1, j / (sr * 0.02)) * Math.exp(-3.2 * t);
      const v = (Math.sin(2 * Math.PI * freq * t) * 0.5 +
        Math.sin(2 * Math.PI * freq * 2 * t) * 0.18 +
        Math.sin(2 * Math.PI * freq * 0.5 * t) * 0.22) * env * 0.3;
      data[start + j] += v;
    }
  }
  // 归一化 + 淡出
  let peak = 0;
  for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(data[i]));
  const gain = peak > 0 ? 0.85 / peak : 1;
  const buf = new ArrayBuffer(44 + n * 2);
  const view = new DataView(buf);
  const ws = (off: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); view.setUint32(4, 36 + n * 2, true); ws(8, "WAVE"); ws(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sr, true); view.setUint32(28, sr * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); ws(36, "data"); view.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const fade = i > n - sr * 0.15 ? (n - i) / (sr * 0.15) : 1;
    const s = Math.max(-1, Math.min(1, data[i] * gain * fade));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  let binary = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + 0x8000)));
  return `data:audio/wav;base64,${btoa(binary)}`;
}