import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "../lib/utils";
import { useFeedback } from "../store/feedback";
import { Button } from "./ui";
import {
  IconClose, IconSuccess, IconError, IconWarning, IconInfo, IconTrash,
} from "./icons";

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

/* ----------------------------------------------------------------- Modal */
export function Modal({
  open, onClose, title, subtitle, icon, children, footer, size = "md",
}: {
  open: boolean; onClose: () => void; title?: ReactNode; subtitle?: string;
  icon?: ReactNode; children: ReactNode; footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  useEscape(open, onClose);
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) panelRef.current?.focus(); }, [open]);
  if (!open) return null;
  const width = { sm: "max-w-sm", md: "max-w-lg", lg: "max-w-2xl", xl: "max-w-4xl" }[size];
  return createPortal(
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-base/70 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      <div
        ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true"
        className={cn("relative w-full rounded-xl border border-line bg-surface shadow-pop outline-none animate-scale-in", width)}
      >
        {(title || icon) && (
          <div className="flex items-start gap-3 border-b border-line-soft px-5 py-4">
            {icon && <div className="mt-0.5 flex-none">{icon}</div>}
            <div className="min-w-0 flex-1">
              {title && <h3 className="text-base font-semibold text-ink">{title}</h3>}
              {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
            </div>
            <button onClick={onClose} aria-label="关闭"
              className="flex-none rounded-md p-1.5 text-faint hover:text-ink hover:bg-raised transition-colors">
              <IconClose size={18} />
            </button>
          </div>
        )}
        <div className="px-5 py-4 text-sm text-muted max-h-[70vh] overflow-y-auto">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line-soft px-5 py-3.5">{footer}</div>}
      </div>
    </div>,
    document.body
  );
}

/* ----------------------------------------------------------------- Drawer */
export function Drawer({
  open, onClose, title, subtitle, children, footer, width = 460, headerExtra,
}: {
  open: boolean; onClose: () => void; title?: ReactNode; subtitle?: ReactNode;
  children: ReactNode; footer?: ReactNode; width?: number; headerExtra?: ReactNode;
}) {
  useEscape(open, onClose);
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) panelRef.current?.focus(); }, [open]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[105]">
      <div className="absolute inset-0 bg-base/60 backdrop-blur-[2px] animate-fade-in" onClick={onClose} />
      <div
        ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true"
        className="absolute right-0 top-0 h-full w-full flex flex-col border-l border-line bg-surface shadow-pop outline-none"
        style={{ maxWidth: width, animation: "drawer-in 0.3s cubic-bezier(0.22,1,0.36,1) both" }}
      >
        <div className="flex items-start gap-3 border-b border-line-soft px-5 py-4 flex-none">
          <div className="min-w-0 flex-1">
            {title && <h3 className="truncate text-base font-semibold text-ink">{title}</h3>}
            {subtitle && <div className="mt-0.5 text-xs text-muted">{subtitle}</div>}
          </div>
          {headerExtra}
          <button onClick={onClose} aria-label="关闭"
            className="flex-none rounded-md p-1.5 text-faint hover:text-ink hover:bg-raised transition-colors">
            <IconClose size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex-none border-t border-line-soft px-5 py-3.5">{footer}</div>}
      </div>
      <style>{`@keyframes drawer-in{from{transform:translateX(24px);opacity:0}to{transform:translateX(0);opacity:1}}`}</style>
    </div>,
    document.body
  );
}

/* ------------------------------------------------------------ ConfirmHost */
export function ConfirmHost() {
  const req = useFeedback((s) => s.confirmState);
  const resolve = useFeedback((s) => s.resolveConfirm);
  if (!req) return null;
  const danger = req.tone === "danger";
  return (
    <Modal
      open onClose={() => resolve(false)} size="sm"
      icon={
        <div className={cn("grid h-9 w-9 place-items-center rounded-lg",
          danger ? "bg-danger/12 text-danger" : "bg-brand/12 text-brand-soft")}>
          {danger ? <IconTrash size={18} /> : <IconInfo size={18} />}
        </div>
      }
      title={req.title}
      footer={
        <>
          <Button variant="ghost" onClick={() => resolve(false)}>{req.cancelLabel ?? "取消"}</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={() => resolve(true)} autoFocus>
            {req.confirmLabel ?? "确认"}
          </Button>
        </>
      }
    >
      <p className="leading-relaxed">{req.message ?? "此操作需要确认。"}</p>
    </Modal>
  );
}

/* -------------------------------------------------------------- ToastHost */
const toastIcon = {
  success: <IconSuccess size={18} />, error: <IconError size={18} />,
  warning: <IconWarning size={18} />, info: <IconInfo size={18} />,
};
const toastTone = {
  success: "text-success border-success/30", error: "text-danger border-danger/30",
  warning: "text-warning border-warning/30", info: "text-info border-info/30",
};
export function ToastHost() {
  const toasts = useFeedback((s) => s.toasts);
  const dismiss = useFeedback((s) => s.dismiss);
  return createPortal(
    <div className="pointer-events-none fixed bottom-5 right-5 z-[140] flex w-[360px] max-w-[calc(100vw-2.5rem)] flex-col gap-2.5"
      aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <div key={t.id}
          className={cn("pointer-events-auto flex items-start gap-3 rounded-lg border bg-overlay/95 glass px-4 py-3 shadow-pop animate-fade-up", toastTone[t.tone])}>
          <span className="mt-0.5 flex-none">{toastIcon[t.tone]}</span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-ink">{t.title}</p>
            {t.desc && <p className="mt-0.5 text-xs leading-snug text-muted break-words">{t.desc}</p>}
            {t.action && (
              <button onClick={() => { t.action!.onClick(); dismiss(t.id); }}
                className="mt-2 text-xs font-semibold text-brand-soft hover:underline">
                {t.action.label}
              </button>
            )}
          </div>
          <button onClick={() => dismiss(t.id)} aria-label="关闭提示"
            className="flex-none rounded p-1 text-faint hover:text-ink transition-colors">
            <IconClose size={15} />
          </button>
        </div>
      ))}
    </div>,
    document.body
  );
}