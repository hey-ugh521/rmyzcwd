import { create } from "zustand";

export type ToastTone = "success" | "error" | "warning" | "info";
export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  desc?: string;
  action?: { label: string; onClick: () => void };
  duration?: number;
}

interface FeedbackState {
  toasts: Toast[];
  push: (t: Omit<Toast, "id">) => string;
  dismiss: (id: string) => void;
  confirmState: ConfirmRequest | null;
  confirm: (req: Omit<ConfirmRequest, "resolve">) => Promise<boolean>;
  resolveConfirm: (ok: boolean) => void;
}

export interface ConfirmRequest {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "brand";
  resolve: (ok: boolean) => void;
}

let seq = 0;
export const useFeedback = create<FeedbackState>((set, get) => ({
  toasts: [],
  push: (t) => {
    const id = `toast_${++seq}`;
    const toast: Toast = { duration: 4200, ...t, id };
    set((s) => ({ toasts: [...s.toasts.slice(-4), toast] }));
    if (toast.duration && toast.duration > 0) {
      setTimeout(() => get().dismiss(id), toast.duration);
    }
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  confirmState: null,
  confirm: (req) =>
    new Promise<boolean>((resolve) => {
      set({ confirmState: { ...req, resolve } });
    }),
  resolveConfirm: (ok) => {
    const cur = get().confirmState;
    set({ confirmState: null });
    cur?.resolve(ok);
  },
}));

export const toast = {
  success: (title: string, desc?: string) => useFeedback.getState().push({ tone: "success", title, desc }),
  error: (title: string, desc?: string) => useFeedback.getState().push({ tone: "error", title, desc, duration: 6000 }),
  warning: (title: string, desc?: string) => useFeedback.getState().push({ tone: "warning", title, desc }),
  info: (title: string, desc?: string) => useFeedback.getState().push({ tone: "info", title, desc }),
  undoable: (title: string, undo: () => void, desc?: string) =>
    useFeedback.getState().push({ tone: "info", title, desc, action: { label: "撤销", onClick: undo }, duration: 6500 }),
};

export const confirmAction = (req: Omit<ConfirmRequest, "resolve">) =>
  useFeedback.getState().confirm(req);