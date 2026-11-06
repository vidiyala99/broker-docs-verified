import { useEffect, useRef } from "react";
import { isDemo } from "../config";
import { api } from "./client";
import type { RunDocument, RunEvent, RunStatus } from "./types";

export interface RunEventHandlers {
  onStatus: (status: RunStatus, error: string | null, retryable: boolean) => void;
  onDocument: (doc: RunDocument) => void;
  onUsage?: (usage: { tokens_in: number; tokens_out: number; cost_usd: number }) => void;
  onArtifact: (info: { artifact_id: number; version: number; fallback: boolean }) => void;
  /** run.end or run.snapshot: the stream is over; refetch the run and its artifact. */
  onEnd: () => void;
  onConnection?: (state: "open" | "reconnecting") => void;
}

const TYPES = ["run.status", "document.status", "run.usage", "artifact.ready", "run.end", "run.snapshot"] as const;

/**
 * Follows GET /api/runs/{id}/events. EventSource resends Last-Event-ID on its own reconnects;
 * when the browser gives up (readyState CLOSED) we reopen with ?last_event_id=N so the
 * stream resumes after the last event we saw. The server closes the stream after run.end.
 */
export function useRunEvents(runId: number | null, active: boolean, handlers: RunEventHandlers, generation = 0) {
  const ref = useRef(handlers);
  ref.current = handlers;
  // Survives resubscribes, so a new stream resumes after the last event seen (for example
  // after the run.end of an earlier execution when a retry is already running).
  const last = useRef<{ runId: number | null; id: number | undefined }>({ runId: null, id: undefined });
  if (last.current.runId !== runId) last.current = { runId, id: undefined };

  useEffect(() => {
    if (runId == null || !active || isDemo()) return; // demo: seeded runs are finished, nothing streams
    let source: EventSource | null = null;
    let ended = false;
    let retryTimer: number | undefined;

    const handle = (raw: MessageEvent<string>) => {
      let ev: RunEvent;
      try {
        ev = JSON.parse(raw.data) as RunEvent;
      } catch {
        return;
      }
      if (typeof ev.id === "number") last.current.id = ev.id;
      const h = ref.current;
      const d = ev.data;
      switch (ev.type) {
        case "run.status":
          h.onStatus(d.status as RunStatus, (d.error as string | null) ?? null, Boolean(d.retryable));
          break;
        case "document.status":
          h.onDocument(d as unknown as RunDocument);
          break;
        case "run.usage":
          h.onUsage?.(d as { tokens_in: number; tokens_out: number; cost_usd: number });
          break;
        case "artifact.ready":
          h.onArtifact(d as { artifact_id: number; version: number; fallback: boolean });
          break;
        case "run.end":
        case "run.snapshot":
          ended = true;
          source?.close();
          h.onEnd();
          break;
      }
    };

    const open = () => {
      source = new EventSource(api.eventsUrl(runId, last.current.id));
      source.onopen = () => ref.current.onConnection?.("open");
      for (const t of TYPES) source.addEventListener(t, handle as EventListener);
      source.onerror = () => {
        if (ended) return;
        ref.current.onConnection?.("reconnecting");
        if (source?.readyState === EventSource.CLOSED) {
          source.close();
          retryTimer = window.setTimeout(open, 3000);
        }
      };
    };
    open();
    return () => {
      ended = true;
      window.clearTimeout(retryTimer);
      source?.close();
    };
  }, [runId, active, generation]);
}
