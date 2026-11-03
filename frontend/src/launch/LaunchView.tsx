import { useEffect, useState } from "react";
import { ArrowRight, ListChecks, ShieldCheck } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { Line, Workspace } from "../api/types";
import { LineChoice } from "../personal/LineChoice";
import { Accordion } from "../ui/Accordion";
import { LeftPanel, type Viewport } from "../shell/layout";
import { Chat } from "../run/Chat";
import { QueueView } from "../queue/QueueView";
import { useQueue } from "../queue/useQueue";
import { isEmpty } from "../queue/queueUtil";
import { ClientField } from "../queue/ClientField";
import { DropZone, FileRows, useUploads } from "./Upload";

export function LaunchView({
  workspace,
  viewport,
  line: entryLine,
}: {
  workspace: Workspace;
  viewport: Viewport;
  /** Preset from the entry point (`#/?line=personal` from Personal lines; the rail means commercial). */
  line: Line;
}) {
  const uploads = useUploads(workspace.limits);
  // O5: Compare Quotes has no wizard, so the line choice sits on this upload screen, defaulted from the entry point.
  const [line, setLine] = useState<Line>(entryLine);
  useEffect(() => setLine(entryLine), [entryLine]);
  const [client, setClient] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const ready = uploads.rows.filter((r) => r.status === "ready" && r.doc);
  const busy = uploads.rows.some((r) => r.status === "uploading");
  // Work queue (spec section 2): the main area shows the entry line's queue instead of Recent runs.
  const data = useQueue(entryLine);
  const queueEmpty = data.queue != null && !data.error && isEmpty(data.queue);

  const start = async () => {
    setStarting(true);
    setStartError(null);
    try {
      const res = await api.startRun(ready.map((r) => r.doc!.id), line, client);
      uploads.clear();
      window.location.hash = `#/runs/${res.run.id}`;
    } catch (e) {
      setStartError(e instanceof ApiError ? e.message : "Couldn't start the run. Retry.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="workspace">
      <LeftPanel viewport={viewport} label="Compare Quotes panel">
        <div className="panel-scroll">
          <Accordion title="Upload" count={ready.length} defaultOpen>
            <div className="upload">
              {!queueEmpty ? (
                <DropZone limits={workspace.limits} onFiles={uploads.add} />
              ) : (
                uploads.rows.length === 0 && <p className="hint">Add quote PDFs with the drop zone on the right.</p>
              )}
              <FileRows rows={uploads.rows} onRemove={uploads.remove} />
              <ClientField value={client} onChange={setClient} />
              <LineChoice value={line} onChange={setLine} />
              {startError && (
                <p className="inline-error" role="alert">
                  {startError}
                </p>
              )}
              <button
                type="button"
                className="btn btn--primary btn--block"
                disabled={!ready.length || busy || starting}
                onClick={() => void start()}
              >
                {starting ? "Starting..." : ready.length ? `Compare ${ready.length} ${ready.length === 1 ? "quote" : "quotes"}` : "Compare quotes"}
                {!starting && <ArrowRight size={16} aria-hidden />}
              </button>
              {!workspace.model_configured && (
                <p className="hint">No model key is set on the server, so documents can't be read until one is configured.</p>
              )}
            </div>
          </Accordion>
          <Accordion title="Input parameters">
            <p className="hint">Compare Quotes has no parameters. Column order follows the upload order.</p>
          </Accordion>
        </div>
        <Chat runId={0} artifact={null} onOpen={() => undefined} onEdited={() => undefined} />
      </LeftPanel>

      <main className="pane" id="main" tabIndex={-1} aria-labelledby="pane-title">
        <div className="tabs" role="presentation">
          <span className="tab is-active">
            <ListChecks size={16} aria-hidden /> Work queue
          </span>
        </div>
        <div className="pane__body pane__body--launch">
          <h1 id="pane-title" className="page-title">
            Compare Quotes
          </h1>
          <QueueView
            data={data}
            line={entryLine}
            empty={
              <div className="queue-empty queue-empty--launch">
                <h2 className="queue-empty__title">No {entryLine === "personal" ? "personal-lines" : "commercial"} work yet.</h2>
                <DropZone limits={workspace.limits} onFiles={uploads.add} large />
                {uploads.rows.length > 0 && <p className="hint">Your files are listed in the Upload panel. Start the comparison there.</p>}
                <a href={`#/policy-check/new${entryLine === "personal" ? "?line=personal" : ""}`} className="btn btn--secondary queue-empty__alt">
                  <ShieldCheck size={18} aria-hidden /> Check a renewal instead
                </a>
              </div>
            }
          />
        </div>
      </main>
    </div>
  );
}
