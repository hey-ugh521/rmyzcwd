import {
  useEffect, useId, useLayoutEffect, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "../lib/utils";
import type { StatusTone } from "../api/normalize";
import { normalizeStatus, statusText, statusTone, kindText, kindTone } from "../api/normalize";
import { IconCheck, IconChevronDown, IconSpinner } from "./icons";

/* ------------------------------------------------------------------ tone */
export const toneText: Record<StatusTone, string> = {
  brand: "text-brand-soft", magenta: "text-magenta", cyan: "text-cyan",
  success: "text-success", warning: "text-warning", danger: "text-danger",
  info: "text-info", muted: "text-muted",
};
const toneBadge: Record<StatusTone, string> = {
  brand: "bg-brand/12 text-brand-soft border-brand/25",
  magenta: "bg-magenta/12 text-magenta border-magenta/25",
  cyan: "bg-cyan/12 text-cyan border-cyan/25",
  success: "bg-success/12 text-success border-success/25",
  warning: "bg-warning/12 text-warning border-warning/25",
  danger: "bg-danger/12 text-danger border-danger/25",
  info: "bg-info/12 text-info border-info/25",
  muted: "bg-muted/10 text-muted border-line",
};
const toneBg: Record<StatusTone, string> = {
  brand: "bg-brand", magenta: "bg-magenta", cyan: "bg-cyan", success: "bg-success",
  warning: "bg-warning", danger: "bg-danger", info: "bg-info", muted: "bg-faint",
};

/* ---------------------------------------------------------------- Button */
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "soft" | "danger";
  size?: "sm" | "md" | "lg" | "icon";
  loading?: boolean;
  icon?: ReactNode;
};
export function Button({
  variant = "ghost", size = "md", loading, icon, className, children, disabled, ...rest
}: ButtonProps) {
  return (
    <button
      className={cn(
        "btn",
        variant === "primary" && "btn-primary",
        variant === "ghost" && "btn-ghost",
        variant === "soft" && "btn-soft",
        variant === "danger" && "btn-danger",
        size === "sm" && "btn-sm",
        size === "lg" && "btn-lg",
        size === "icon" && "btn-icon",
        className
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <IconSpinner size={size === "sm" ? 14 : 16} /> : icon}
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ Card */
export function Card({ className, children, hover, ...rest }:
  { className?: string; children: ReactNode; hover?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("card", hover && "card-hover", className)} {...rest}>
      {children}
    </div>
  );
}

/* ----------------------------------------------------------------- Badge */
export function Badge({ tone = "muted", className, children, dot }:
  { tone?: StatusTone; className?: string; children: ReactNode; dot?: boolean }) {
  return (
    <span className={cn("badge", toneBadge[tone], className)}>
      {dot && <span className={cn("led", toneBg[tone])} />}
      {children}
    </span>
  );
}

export function StatusBadge({ status, className }: { status: unknown; className?: string }) {
  const n = normalizeStatus(status);
  const tone = statusTone(n);
  const live = n === "running" || n === "pending";
  return (
    <span className={cn("badge", toneBadge[tone], className)}>
      <span className={cn("led", toneBg[tone], live && "led-live")} />
      {statusText(n)}
    </span>
  );
}

export function KindBadge({ kind, className }: { kind: unknown; className?: string }) {
  const tone = kindTone(kind);
  return <span className={cn("badge", toneBadge[tone], className)}>{kindText(kind)}</span>;
}

export function Led({ tone = "muted", live, className }:
  { tone?: StatusTone; live?: boolean; className?: string }) {
  return <span className={cn("led", toneBg[tone], live && "led-live", className)} />;
}

/* --------------------------------------------------------------- Spinner */
export function Spinner({ size = 18, className }: { size?: number; className?: string }) {
  return <IconSpinner size={size} className={cn("text-brand-soft", className)} />;
}

/* ----------------------------------------------------------- ProgressBar */
export function ProgressBar({ value, indeterminate, tone = "brand", className }:
  { value?: number; indeterminate?: boolean; tone?: StatusTone; className?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-raised", className)}>
      <div
        className={cn("h-full rounded-full transition-all duration-500", toneBg[tone],
          indeterminate && "animate-[shimmer_1.4s_infinite] w-1/3")}
        style={indeterminate ? undefined : { width: `${Math.max(0, Math.min(100, value ?? 0))}%` }}
      />
    </div>
  );
}

/* -------------------------------------------------------------- Skeleton */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} />;
}

/* ------------------------------------------------------------ EmptyState */
export function EmptyState({ icon, title, desc, action, className }:
  { icon?: ReactNode; title: string; desc?: string; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center py-14 px-6", className)}>
      {icon && (
        <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl border border-line-soft bg-raised/60 text-faint">
          {icon}
        </div>
      )}
      <p className="text-sm font-medium text-ink">{title}</p>
      {desc && <p className="mt-1.5 max-w-sm text-xs leading-relaxed text-muted">{desc}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------- Field */
export function Field({ label, hint, children, className, htmlFor }:
  { label?: string; hint?: string; children: ReactNode; className?: string; htmlFor?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      {label && <label className="label" htmlFor={htmlFor}>{label}</label>}
      {children}
      {hint && <p className="mt-1 text-[11px] leading-snug text-faint">{hint}</p>}
    </div>
  );
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn("input", className)} {...rest} />;
}
export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn("textarea", className)} {...rest} />;
}

/* ---------------------------------------------------------------- Select */
export interface SelectOption { value: string; label: string; hint?: string; disabled?: boolean }
export function Select({
  value, onChange, options, placeholder = "请选择", className, size = "md", disabled, ariaLabel,
}: {
  value: string; onChange: (v: string) => void; options: SelectOption[];
  placeholder?: string; className?: string; size?: "sm" | "md"; disabled?: boolean; ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0, up: false });
  const selected = options.find((o) => o.value === value);
  const id = useId();

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return;
    const place = () => {
      const r = btnRef.current!.getBoundingClientRect();
      const maxH = 280;
      const up = window.innerHeight - r.bottom < maxH + 16 && r.top > maxH;
      setPos({
        top: up ? r.top - 6 : r.bottom + 6,
        left: r.left, width: r.width, up,
      });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!btnRef.current?.contains(e.target as Node) && !popRef.current?.contains(e.target as Node))
        setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); btnRef.current?.focus(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(options.length - 1, a + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const o = options[active];
        if (o && !o.disabled) { onChange(o.value); setOpen(false); btnRef.current?.focus(); }
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open, active, options, onChange]);

  const openMenu = () => {
    const idx = options.findIndex((o) => o.value === value);
    setActive(idx >= 0 ? idx : 0);
    setOpen(true);
  };

  return (
    <>
      <button
        ref={btnRef} id={id} type="button" disabled={disabled} aria-label={ariaLabel}
        aria-haspopup="listbox" aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openMenu())}
        className={cn(
          "input flex items-center justify-between gap-2 text-left",
          size === "sm" && "h-7 text-xs px-2.5",
          disabled && "opacity-50 pointer-events-none", className
        )}
      >
        <span className={cn("truncate", !selected && "text-faint")}>
          {selected ? selected.label : placeholder}
        </span>
        <IconChevronDown size={15} className={cn("flex-none text-faint transition-transform", open && "rotate-180")} />
      </button>
      {open && createPortal(
        <div
          ref={popRef} role="listbox"
          className="fixed z-[120] max-h-[280px] overflow-y-auto rounded-lg border border-line bg-overlay/95 glass p-1 shadow-pop animate-scale-in"
          style={{
            top: pos.up ? undefined : pos.top,
            bottom: pos.up ? window.innerHeight - pos.top : undefined,
            left: pos.left, width: Math.max(pos.width, 160),
            transformOrigin: pos.up ? "bottom" : "top",
          }}
        >
          {options.map((o, i) => (
            <button
              key={o.value} type="button" role="option" aria-selected={o.value === value}
              disabled={o.disabled}
              onMouseEnter={() => setActive(i)}
              onClick={() => { if (!o.disabled) { onChange(o.value); setOpen(false); } }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                i === active ? "bg-brand/15 text-ink" : "text-muted",
                o.disabled && "opacity-40 pointer-events-none"
              )}
            >
              <span className="flex-1 min-w-0">
                <span className="block truncate">{o.label}</span>
                {o.hint && <span className="block truncate text-[11px] text-faint">{o.hint}</span>}
              </span>
              {o.value === value && <IconCheck size={15} className="flex-none text-brand-soft" />}
            </button>
          ))}
          {options.length === 0 && <div className="px-2.5 py-3 text-xs text-faint">无可选项</div>}
        </div>,
        document.body
      )}
    </>
  );
}

/* ---------------------------------------------------------------- Slider */
export function Slider({
  value, onChange, min = 0, max = 100, step = 1, className, ariaLabel, format,
}: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number;
  className?: string; ariaLabel?: string; format?: (v: number) => string;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div className="relative flex-1 h-5 flex items-center">
        <div className="absolute h-1.5 w-full rounded-full bg-raised overflow-hidden">
          <div className="h-full rounded-full" style={{
            width: `${pct}%`,
            background: "linear-gradient(90deg, rgb(var(--c-brand)), rgb(var(--c-magenta)))",
          }} />
        </div>
        <input
          type="range" min={min} max={max} step={step} value={value} aria-label={ariaLabel}
          onChange={(e) => onChange(Number(e.target.value))}
          className="relative w-full h-5 appearance-none bg-transparent cursor-pointer
            [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4
            [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-ink
            [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-brand
            [&::-webkit-slider-thumb]:shadow-[0_0_0_4px_rgb(var(--c-brand)/0.18)]
            [&::-webkit-slider-thumb]:transition-transform hover:[&::-webkit-slider-thumb]:scale-110
            [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full
            [&::-moz-range-thumb]:bg-ink [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-brand"
        />
      </div>
      <span className="tnum w-12 flex-none text-right text-xs text-muted">
        {format ? format(value) : value}
      </span>
    </div>
  );
}

/* --------------------------------------------------------------- Stepper */
export function Stepper({
  value, onChange, min = 0, max = 9999, step = 1, className, ariaLabel, suffix,
}: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number;
  className?: string; ariaLabel?: string; suffix?: string;
}) {
  const set = (v: number) => onChange(Math.max(min, Math.min(max, v)));
  return (
    <div className={cn("flex items-center rounded-md border border-line bg-base/60 h-9 overflow-hidden", className)}>
      <button type="button" aria-label="减少" onClick={() => set(value - step)}
        className="h-full w-8 flex-none text-muted hover:text-ink hover:bg-raised transition-colors">−</button>
      <input
        type="number" value={value} aria-label={ariaLabel}
        onChange={(e) => set(Number(e.target.value))}
        className="tnum h-full w-full min-w-0 bg-transparent text-center text-sm outline-none
          [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
      />
      {suffix && <span className="pr-1 text-[11px] text-faint">{suffix}</span>}
      <button type="button" aria-label="增加" onClick={() => set(value + step)}
        className="h-full w-8 flex-none text-muted hover:text-ink hover:bg-raised transition-colors">+</button>
    </div>
  );
}

/* ---------------------------------------------------------------- Toggle */
export function Toggle({ checked, onChange, label, disabled, size = "md" }:
  { checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean; size?: "sm" | "md" }) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex flex-none items-center rounded-full transition-colors duration-200 border",
        size === "md" ? "h-6 w-11" : "h-5 w-9",
        checked ? "bg-brand/80 border-brand" : "bg-raised border-line",
        disabled && "opacity-45 pointer-events-none"
      )}
    >
      <span className={cn(
        "absolute rounded-full bg-ink transition-transform duration-200 shadow",
        size === "md" ? "h-4 w-4" : "h-3.5 w-3.5",
        checked ? (size === "md" ? "translate-x-6" : "translate-x-4.5") : "translate-x-1"
      )} style={checked && size === "sm" ? { transform: "translateX(18px)" } : undefined} />
    </button>
  );
}

/* ------------------------------------------------------------------ Tabs */
export function Tabs<T extends string>({ tabs, value, onChange, className, size = "md" }:
  { tabs: { value: T; label: ReactNode; count?: number }[]; value: T; onChange: (v: T) => void; className?: string; size?: "sm" | "md" }) {
  return (
    <div className={cn("inline-flex items-center gap-1 rounded-lg border border-line-soft bg-base/50 p-1", className)}>
      {tabs.map((t) => (
        <button
          key={t.value} type="button" onClick={() => onChange(t.value)}
          className={cn(
            "relative inline-flex items-center gap-2 rounded-md font-medium transition-all duration-150",
            size === "md" ? "h-8 px-3.5 text-sm" : "h-7 px-3 text-xs",
            value === t.value ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink"
          )}
        >
          {t.label}
          {t.count != null && (
            <span className={cn("tnum rounded-full px-1.5 text-[10px]",
              value === t.value ? "bg-brand/20 text-brand-soft" : "bg-raised text-faint")}>
              {t.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- Tooltip */
export function Tooltip({ label, children, side = "top" }:
  { label: string; children: ReactNode; side?: "top" | "bottom" | "right" }) {
  const [show, setShow] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  useLayoutEffect(() => {
    if (!show || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const top = side === "bottom" ? r.bottom + 8 : side === "right" ? r.top + r.height / 2 : r.top - 8;
    const left = side === "right" ? r.right + 8 : r.left + r.width / 2;
    setPos({ top, left });
  }, [show, side]);
  return (
    <span ref={ref} className="inline-flex" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      {show && createPortal(
        <span
          className="pointer-events-none fixed z-[130] -translate-x-1/2 rounded-md border border-line bg-overlay px-2 py-1 text-[11px] text-ink shadow-pop animate-fade-in whitespace-nowrap"
          style={{
            top: side === "top" ? pos.top : pos.top,
            left: pos.left,
            transform: side === "top" ? "translate(-50%,-100%)" : side === "right" ? "translate(0,-50%)" : "translate(-50%,0)",
          }}
        >{label}</span>,
        document.body
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ Chip */
export function Chip({ active, onClick, children, className }:
  { active?: boolean; onClick?: () => void; children: ReactNode; className?: string }) {
  return (
    <button type="button" onClick={onClick} className={cn("chip", active && "chip-active", className)}>
      {children}
    </button>
  );
}