"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useRef } from "react";
import { useApp } from "./app-provider";

export function LogDrawer() {
  const { log, running, drawerOpen, setDrawerOpen, clearLog } = useApp();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [log]);

  return (
    <Dialog.Root open={drawerOpen} onOpenChange={setDrawerOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="drawer">
          <div className="drawer-head">
            <Dialog.Title className="drawer-title">
              {running ? <span className="pulse" /> : null}
              {running === "scan" ? "Scanning…" : running === "commit" ? "Committing…" : "Activity Log"}
            </Dialog.Title>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn" onClick={clearLog} disabled={!!running || log.length === 0}>Clear</button>
              <Dialog.Close asChild>
                <button className="btn">Close</button>
              </Dialog.Close>
            </div>
          </div>
          <div ref={scrollRef} className="drawer-log">
            {log.length === 0 && (
              <div className="log-line log-line-info">(no output yet — trigger a scan or commit)</div>
            )}
            {log.map((l, i) => (
              <div key={i} className={`log-line log-line-${l.kind}`}>{l.text}</div>
            ))}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
