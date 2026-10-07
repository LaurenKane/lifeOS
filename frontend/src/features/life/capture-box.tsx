/* CaptureBox — the "what's on your mind?" itself.
 *
 * One input. Submitting it never asks you anything; the echo below states what
 * was heard and what was created, with two affordances: retap to change the
 * parsed date(s) or wording, and undo if the interpretation was wrong (Q22).
 * An unparseable line is simply a Thought. */
import React from "react";
import { Link } from "react-router-dom";
import { capture, editAction, undoCapture } from "./api";
import {
  ActionSummary,
  CaptureResponse,
  ThoughtSummary,
} from "./types";

type Echo =
  | { kind: "thought"; thought: ThoughtSummary }
  | { kind: "action"; thought: ThoughtSummary; action: ActionSummary }
  | { kind: "receipt"; thought: ThoughtSummary; upkeepTitle: string }
  | { kind: "complete"; thought: ThoughtSummary };

export const CaptureBox: React.FC<{ onCaptured?: () => void }> = ({
  onCaptured,
}) => {
  const [text, setText] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [echo, setEcho] = React.useState<Echo | null>(null);

  const onEcho = React.useCallback(
    (body: CaptureResponse) => {
      setEcho(
        body.upkeep_choice === null && body.created_receipt_id === null && body.created_action === null
          ? { kind: "thought", thought: body.thought }
          : body.created_action !== null
            ? { kind: "action", thought: body.thought, action: body.created_action }
            : body.created_receipt_id !== null
              ? {
                  kind: "receipt",
                  thought: body.thought,
                  upkeepTitle: body.receipted_upkeep?.title ?? "your upkeep",
                }
              : { kind: "complete", thought: body.thought },
      );
      onCaptured?.();
    },
    [onCaptured],
  );

  const submit = React.useCallback(
    async (event?: React.FormEvent) => {
      event?.preventDefault();
      const value = text.trim();
      if (value === "" || busy) return;
      setBusy(true);
      setError(null);
      try {
        const body = await capture(value);
        setEcho(null);
        setText("");
        onEcho(body);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(false);
      }
    },
    [text, busy, onEcho],
  );

  /* RETAP — change a parsed date on the echo without a form cycle. The parser
   * runs on the server, and reinterpreting the words HERE would be a second
   * parser: only the date it already settled on is moved. */
  const bumpDate = React.useCallback(
    async (days: number) => {
      if (echo === null || echo.kind !== "action") return;
      try {
        const d = new Date();
        d.setDate(d.getDate() + days);
        await editAction(echo.action.id, {
          due_date: d.toISOString().slice(0, 10),
        });
        setEcho({ ...echo, action: { ...echo.action, due_date: d.toISOString().slice(0, 10) } });
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    },
    [echo],
  );

  const undo = React.useCallback(async () => {
    if (echo === null) return;
    try {
      await undoCapture(echo.thought.id);
      setEcho(null);
      onCaptured?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [echo, onCaptured]);

  return (
    <form onSubmit={(event) => void submit(event)} aria-label="capture">
      <label htmlFor="capture-input" className="eyebrow sr-only">
        What's on your mind?
      </label>
      <input
        id="capture-input"
        value={text}
        placeholder="What's on your mind?"
        onChange={(event) => setText(event.target.value)}
        autoComplete="off"
        className={
          "w-full rounded-md bg-panel px-4 py-3 text-[1rem] text-ink " +
          "placeholder:text-ink-quieter focus:outline-none focus-visible:ring-2 focus-visible:ring-lime"
        }
      />
      {text === "" && (
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.75rem] text-ink-quiet">
          <span>dates and words like "tomorrow", "friday", "urgent" are heard</span>
          <span>press enter to capture</span>
          <span>
            <Link className="underline" to="/life/inbox">
              the pile
            </Link>{" "}
            keeps everything
          </span>
        </p>
      )}
      {error !== null && (
        <p role="alert" className="mt-2 text-[0.8125rem] text-money-out">
          {error}
        </p>
      )}
      {echo !== null && <EchoLine echo={echo} onUndo={() => void undo()} onBumpDate={(d) => void bumpDate(d)} />}
      <button type="submit" className="sr-only">
        Capture
      </button>
      {busy && <span className="sr-only">capturing…</span>}
    </form>
  );
};

const EchoLine: React.FC<{
  echo: Echo;
  onUndo: () => void;
  onBumpDate: (days: number) => void;
}> = ({ echo, onUndo, onBumpDate }) => {
  const what =
    echo.kind === "action"
      ? `an action for${echo.action.due_date !== null ? ` ${echo.action.due_date}` : " today"}`
      : echo.kind === "receipt"
        ? `a receipt for ${echo.upkeepTitle}`
        : echo.kind === "complete"
          ? "an upkeep receipt"
          : "a thought in the pile";
  return (
    <div
      role="status"
      className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded bg-panel px-4 py-3 text-[0.8125rem]"
    >
      <span className="leading-relaxed">
        Saved. That became {what}. Your words stay yours, in the pile too.
      </span>
      {echo.kind === "action" && (
        <span className="flex gap-2">
          <button type="button" onClick={() => onBumpDate(1)} className="underline text-ink-quiet hover:text-ink">
            it's tomorrow's thing
          </button>
          <button type="button" onClick={() => onBumpDate(-1)} className="underline text-ink-quiet hover:text-ink">
            it was yesterday's
          </button>
        </span>
      )}
      <button type="button" onClick={onUndo} className="underline text-ink-quiet hover:text-ink">
        undo
      </button>
    </div>
  );
};
