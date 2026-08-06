"use client";

// In-app replacement for window.confirm / window.prompt.
// Radix Dialog + one hook `useConfirm()` returning a promise:
//   const answer = await confirm({ title, message, ... });
// answer is `null` on cancel/close. For plain confirms it's `""` on ok.
// For prompt-style calls (opts.input truthy) it's the entered text.

import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useRef, useState } from "react";

export type ConfirmOpts = {
  title: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  /** When set, renders a text input; the promise resolves to its value. */
  input?: { label?: string; placeholder?: string; initial?: string };
};

type Pending = ConfirmOpts & {
  resolve: (v: string | null) => void;
};

export function useConfirm() {
  const [pending, setPending] = useState<Pending | null>(null);
  const [value, setValue] = useState("");

  // Stable reference for the returned confirm() so callers can put it in deps.
  const ref = useRef<((opts: ConfirmOpts) => Promise<string | null>) | null>(null);
  if (!ref.current) {
    ref.current = (opts: ConfirmOpts) =>
      new Promise<string | null>((resolve) => {
        setValue(opts.input?.initial ?? "");
        setPending({ ...opts, resolve });
      });
  }

  const close = useCallback((answer: string | null) => {
    if (!pending) return;
    pending.resolve(answer);
    setPending(null);
  }, [pending]);

  const dialog = pending ? (
    <Dialog.Root
      open
      onOpenChange={(o) => { if (!o) close(null); }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content
          className="modal"
          style={{ maxWidth: 440, width: "90vw" }}
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => {
            // If there's an input, let it grab focus; otherwise let Radix pick.
            if (pending.input) e.preventDefault();
          }}
        >
          <div className="modal-head">
            <Dialog.Title style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
              {pending.title}
            </Dialog.Title>
            {pending.message && (
              <div className="mute" style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.5 }}>
                {pending.message}
              </div>
            )}
          </div>

          {pending.input && (
            <div style={{ padding: "18px 22px" }}>
              {pending.input.label && (
                <div className="mute" style={{
                  fontSize: 11, textTransform: "uppercase",
                  letterSpacing: "0.06em", fontWeight: 500, marginBottom: 6,
                }}>{pending.input.label}</div>
              )}
              <input
                autoFocus
                placeholder={pending.input.placeholder}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") close(value);
                  if (e.key === "Escape") close(null);
                }}
                style={{
                  width: "100%", height: 34, padding: "0 10px", fontSize: 13,
                  background: "var(--surface)", border: "1px solid var(--border-strong)",
                  borderRadius: 3, color: "var(--ink)", fontFamily: "var(--font-sans)",
                }}
              />
            </div>
          )}

          <div className="modal-foot" style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="btn" onClick={() => close(null)}>
              {pending.cancelLabel ?? "Cancel"}
            </button>
            <button
              className={pending.danger ? "btn" : "btn btn-primary"}
              style={pending.danger
                ? { color: "white", background: "var(--err)", borderColor: "var(--err)" }
                : undefined}
              onClick={() => close(pending.input ? value : "")}
            >
              {pending.confirmLabel ?? "Confirm"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  ) : null;

  return { confirm: ref.current!, dialog };
}
