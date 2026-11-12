import { useEffect, useRef } from "react";
import { useUI } from "../store/ui";
import type { Task } from "../api/types";
import { normalizeStatus } from "../api/normalize";

// 极轻量的本机提示音（WebAudio 合成，无外部资源）。默认开启，顶栏可一键静音。
let ctx: AudioContext | null = null;
let gestured = false;

function ensure(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AC) return null;
  if (!ctx) ctx = new AC();
  if (ctx.state === "suspended" && gestured) void ctx.resume();
  return ctx;
}

if (typeof window !== "undefined") {
  const mark = () => { gestured = true; ensure(); };
  window.addEventListener("pointerdown", mark, { once: true });
  window.addEventListener("keydown", mark, { once: true });
}

type SoundName = "submit" | "success" | "error" | "cancel" | "click";

const RECIPES: Record<SoundName, { f: number[]; type: OscillatorType; dur: number; gain: number }> = {
  submit: { f: [660, 880], type: "sine", dur: 0.12, gain: 0.05 },
  success: { f: [659, 988], type: "sine", dur: 0.26, gain: 0.055 },
  error: { f: [180, 120], type: "triangle", dur: 0.24, gain: 0.05 },
  cancel: { f: [520, 320], type: "sine", dur: 0.16, gain: 0.045 },
  click: { f: [420], type: "sine", dur: 0.05, gain: 0.03 },
};

export function playSound(name: SoundName) {
  if (useUI.getState().muted) return;
  const ac = ensure();
  if (!ac || ac.state !== "running") return;
  const r = RECIPES[name];
  const now = ac.currentTime;
  const master = ac.createGain();
  master.gain.value = r.gain;
  master.connect(ac.destination);
  r.f.forEach((freq, i) => {
    const osc = ac.createOscillator();
    const g = ac.createGain();
    osc.type = r.type;
    osc.frequency.value = freq;
    const start = now + i * (r.dur / r.f.length) * 0.7;
    g.gain.setValueAtTime(0, start);
    g.gain.linearRampToValueAtTime(1, start + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, start + r.dur);
    osc.connect(g); g.connect(master);
    osc.start(start); osc.stop(start + r.dur + 0.02);
  });
}

/** 监听任务终态变化，播放对应提示音（成功 / 失败 / 取消）。 */
export function useTaskChime(tasks: Task[] | undefined) {
  const seen = useRef<Set<string>>(new Set());
  const primed = useRef(false);
  useEffect(() => {
    if (!tasks) return;
    const terminal = tasks.filter((t) => ["completed", "failed", "cancelled"].includes(normalizeStatus(t.status)));
    if (!primed.current) {
      // 首次加载只记录基线，不为历史任务发声
      terminal.forEach((t) => seen.current.add(t.id));
      primed.current = true;
      return;
    }
    for (const t of terminal) {
      if (seen.current.has(t.id)) continue;
      seen.current.add(t.id);
      const st = normalizeStatus(t.status);
      if (st === "completed") playSound("success");
      else if (st === "failed") playSound("error");
      else if (st === "cancelled") playSound("cancel");
    }
  }, [tasks]);
}