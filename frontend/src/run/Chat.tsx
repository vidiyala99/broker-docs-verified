import { useEffect, useId, useRef, useState, type MouseEvent } from "react";
import { ArrowUp, CaretDown, Check, Question, Sparkle } from "@phosphor-icons/react";
import { api, ApiError } from "../api/client";
import type { Artifact, EditResult, Message } from "../api/types";
import { Segments } from "../report/Report";
import type { OpenSource } from "../report/Chip";

/**
 * Follow-up chat (state table: typing indicator, disabled with reason, "Couldn't answer" with
 * Retry, answers with chips, scan-read citations as dashed chips via the chip data).
 */
export function Chat({
  runId,
  artifact,
  proposal = false,
  onOpen,
  onEdited,
  open: controlledOpen,
  onToggle,
}: {
  runId: number;
  proposal?: boolean;
  artifact: Artifact | null;
  onOpen: OpenSource;
  onEdited: (res: EditResult) => void;
  /** Collapse state (the header toggle). Uncontrolled when omitted; starts open. */
  open?: boolean;
  onToggle?: (open: boolean) => void;
}) {
  const [ownOpen, setOwnOpen] = useState(true);
  const [viaPointer, setViaPointer] = useState(false);
  const bodyId = useId();
  const [messages, setMessages] = useState<Message[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<{ question: string; message: string } | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const hasReport = Boolean(artifact);
  const artifactId = artifact?.id;

  useEffect(() => {
    setMessages([]);
    setError(null);
    if (!runId) return;
    api.messages(runId).then(setMessages, () => undefined);
  }, [runId]);

  useEffect(() => {
    if (!artifactId) {
      setSuggestions([]);
      return;
    }
    api.suggestions(runId).then(setSuggestions, () => setSuggestions([]));
  }, [runId, artifactId]);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [messages, pending, error, controlledOpen, ownOpen]);

  const ask = async (question: string) => {
    const q = question.trim();
    if (!q || pending) return;
    setPending(q);
    setError(null);
    setDraft("");
    try {
      const res = await api.ask(runId, q);
      setMessages((m) => [...m, res.user_message, res.assistant_message]);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Couldn't answer.";
      setError({ question: q, message: msg });
    } finally {
      setPending(null);
    }
  };

  const apply = async (m: Message, editId: number) => {
    const key = `${m.id}:${editId}`;
    setApplying(key);
    setApplyError(null);
    try {
      const res = await api.applyEdit(runId, m.id, editId);
      setMessages((all) => all.map((x) => (x.id === m.id ? res.message : x)));
      onEdited(res);
    } catch (e) {
      setApplyError(e instanceof ApiError ? e.message : "Couldn't apply the edit. Retry.");
    } finally {
      setApplying(null);
    }
  };

  const chips = artifact?.chips ?? {};
  const rowLabel = (blockId: string, rowKey: string) => {
    for (const s of artifact?.content.sections ?? []) {
      for (const b of s.blocks) {
        if (b.type === "table" && b.id === blockId) return b.rows.find((r) => r.key === rowKey)?.label ?? rowKey;
      }
    }
    return rowKey.replace(/_/g, " ");
  };
  const showSuggestions = hasReport && suggestions.length > 0 && messages.length === 0 && !pending;
  const open = controlledOpen ?? ownOpen;
  const toggle = (e: MouseEvent<HTMLButtonElement>) => {
    setViaPointer(e.detail > 0);
    setOwnOpen(!open);
    onToggle?.(!open);
  };
  const summary = !hasReport
    ? proposal
      ? "After the proposal is generated"
      : "After the report is written"
    : pending
      ? "Answering..."
      : messages.length
        ? `${messages.length} ${messages.length === 1 ? "message" : "messages"}`
        : proposal
          ? "Ask about this proposal"
          : "Ask about these quotes";

  return (
    <section className={`chat${hasReport ? "" : " chat--idle"}${open ? "" : " chat--collapsed"}`} aria-labelledby={`${bodyId}-title`}>
      <h2 className="chat__head" id={`${bodyId}-title`}>
        <button type="button" className="chat__toggle" aria-expanded={open} aria-controls={bodyId} onClick={toggle}>
          <span className="chat__title">Chat</span>
          {open && messages.length > 0 && <span className="accordion__count">{messages.length}</span>}
          {!open && <span className="accordion__summary">{summary}</span>}
          <CaretDown size={16} aria-hidden className="chat__caret" />
        </button>
      </h2>
      <div id={bodyId} className={`chat__body${viaPointer ? " fade-in" : ""}`} hidden={!open}>
      <div className="chat__log" ref={list} aria-live="polite" aria-relevant="additions">
        {!hasReport && <p className="chat__empty">{proposal ? "Generate a proposal to ask questions about its sources." : "Run a comparison to ask questions."}</p>}
        {hasReport && !messages.length && !pending && !error && !showSuggestions && (
          <p className="chat__empty">Answers cite the page they come from.</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`msg msg--${m.role}`}>
            {m.role === "user" ? (
              <p className="msg__bubble">{m.content}</p>
            ) : (
              <div className="msg__answer">
                <span className="msg__who">
                  <Sparkle size={14} aria-hidden /> Answer
                </span>
                <p>
                  <Segments segments={m.segments} chips={chips} onOpen={onOpen} />
                </p>
                {m.proposed_edits.map((e) => (
                  <div key={e.id} className="proposal">
                    <p className="proposal__what">
                      Set {e.column_key}: {rowLabel(e.block_id, e.row_key)} to <strong>{e.value ?? "Not specified"}</strong>
                      {e.current_value != null && <span className="proposal__was"> (now {e.current_value})</span>}
                    </p>
                    {e.reason && <p className="proposal__why">{e.reason}</p>}
                    {e.status === "applied" ? (
                      <span className="proposal__done">
                        <Check size={14} aria-hidden /> Applied as version {e.applied_version}
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--secondary btn--sm"
                        disabled={applying != null}
                        onClick={() => void apply(m, e.id)}
                      >
                        {applying === `${m.id}:${e.id}` ? "Applying..." : "Accept edit"}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {pending && (
          <>
            <div className="msg msg--user">
              <p className="msg__bubble">{pending}</p>
            </div>
            <div className="typing" role="status">
              <span className="typing__dots" aria-hidden>
                <span />
                <span />
                <span />
              </span>
              <span>Answering...</span>
            </div>
          </>
        )}
        {error && (
          <div className="chat__error" role="alert">
            <p>{error.message.startsWith("Couldn't answer") ? error.message : `Couldn't answer. ${error.message}`}</p>
            <button type="button" className="btn btn--secondary btn--sm" onClick={() => void ask(error.question)}>
              Retry
            </button>
          </div>
        )}
        {applyError && (
          <p className="chat__error" role="alert">
            {applyError}
          </p>
        )}
      </div>
      {showSuggestions && (
        <div className="suggestions">
          <p className="suggestions__title">
            <Question size={16} aria-hidden /> Suggestions
          </p>
          {suggestions.slice(0, 3).map((s) => (
            <button key={s} type="button" className="suggestion" onClick={() => void ask(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(draft);
        }}
      >
        <label htmlFor="chat-input" className="sr-only">
          Ask a question about this report
        </label>
        <textarea
          id="chat-input"
          className="chat__input"
          rows={1}
          maxLength={4000}
          placeholder={hasReport ? (proposal ? "Ask about this proposal's sources" : "Ask about these quotes") : proposal ? "Generate a proposal to ask questions" : "Run a comparison to ask questions"}
          value={draft}
          disabled={!hasReport}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask(draft);
            }
          }}
        />
        <button type="submit" className="icon-btn icon-btn--primary" aria-label="Send question" disabled={!hasReport || !draft.trim() || pending != null}>
          <ArrowUp size={16} weight="bold" aria-hidden />
        </button>
      </form>
      </div>
    </section>
  );
}
