import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const btnPrimary =
  "inline-flex items-center justify-center rounded-lg bg-accent px-4 py-2 text-[15px] font-semibold text-white hover:bg-accent-strong disabled:cursor-default disabled:opacity-50";

export const btnGhost =
  "inline-flex items-center justify-center rounded-lg border border-line bg-pane px-3 py-1.5 text-[14px] font-medium text-muted hover:border-line-strong hover:bg-accent-soft-2 hover:text-accent disabled:cursor-default disabled:opacity-50";

export const btnDanger =
  "inline-flex items-center justify-center rounded-lg bg-danger px-4 py-2 text-[15px] font-semibold text-white hover:bg-[#9b1c16] disabled:cursor-default disabled:opacity-50";

export const inputClass =
  "w-full rounded-lg border border-line bg-pane px-3 py-2.5 text-[15px] text-ink outline-none placeholder:text-muted/70 focus:border-accent focus:ring-2 focus:ring-accent/20";

interface ModalProps {
  open: boolean;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
}

export default function Modal({ open, title, children, footer, onClose }: ModalProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const root = panelRef.current;
    const el = root?.querySelector<HTMLElement>("input, textarea, button");
    el?.focus();
    if (el instanceof HTMLInputElement) el.select();
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-[rgba(24,38,32,0.4)]"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative z-10 w-full max-w-[420px] rounded-xl border border-line bg-pane shadow-[0_12px_40px_rgba(24,38,32,0.18)]"
      >
        <h2 id={titleId} className="border-b border-line px-5 py-3.5 text-[17px] font-semibold text-ink">
          {title}
        </h2>
        <div className="px-5 py-4 text-[15px] leading-relaxed text-ink">{children}</div>
        {footer && (
          <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export function SecretInput({
  value,
  onChange,
  placeholder,
  id,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  id?: string;
}) {
  return (
    <input
      id={id}
      type="password"
      autoComplete="off"
      spellCheck={false}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    />
  );
}
