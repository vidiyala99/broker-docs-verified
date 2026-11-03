import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Line, Queue } from "../api/types";

/** D5: poll every 3 s while something is in progress and the page is visible. */
export const POLL_MS = 3000;

export interface QueueData {
  queue: Queue | null;
  error: string | null;
  /** Fetch now (Retry, after an action). Clears an error and lets polling resume. */
  reload: () => void;
  /** Local (optimistic) change; the next fetch replaces it. */
  update: (fn: (q: Queue) => Queue) => void;
}

/**
 * The work queue of one line. Polls while `in_progress` is non-empty and `document.visibilityState` is visible; stops
 * otherwise and resumes (with an immediate fetch) when the tab is shown again. An error stops polling until `reload`
 * (Retry) or the next navigation (a new mount). Responses that arrive out of order are dropped.
 */
export function useQueue(line: Line): QueueData {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef<Queue | null>(null);
  const failed = useRef(false);

  const load = useCallback(() => {
    window.clearTimeout(timer.current);
    const n = ++seq.current;
    api.queue(line).then(
      (q) => {
        if (n !== seq.current) return;
        failed.current = false;
        latest.current = q;
        setQueue(q);
        setError(null);
        if (q.in_progress.length > 0 && document.visibilityState === "visible") {
          timer.current = window.setTimeout(load, POLL_MS);
        }
      },
      (e: unknown) => {
        if (n !== seq.current) return;
        failed.current = true;
        setError(e instanceof ApiError ? e.message : "Couldn't load the work queue.");
      },
    );
  }, [line]);

  useEffect(() => {
    latest.current = null;
    failed.current = false;
    setQueue(null);
    setError(null);
    load();
    const onVis = () => {
      if (document.visibilityState !== "visible") {
        window.clearTimeout(timer.current);
        return;
      }
      if (!failed.current && latest.current && latest.current.in_progress.length > 0) load();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      seq.current += 1; // drop any answer still in flight
      window.clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load]);

  const update = useCallback((fn: (q: Queue) => Queue) => {
    setQueue((q) => {
      if (!q) return q;
      const next = fn(q);
      latest.current = next;
      return next;
    });
  }, []);

  return { queue, error, reload: load, update };
}
