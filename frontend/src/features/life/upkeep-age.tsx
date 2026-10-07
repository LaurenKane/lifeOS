/* UpkeepAge — the sentence beside an Upkeep: when it was last done, and the
 * rhythm it was given.
 *
 * Both halves of it are stated, because they are different kinds of thing: the
 * date and the day-count are facts the receipts earned, and the aim is intent.
 * The "~" before the aim is that distinction in one glyph — "aim every ~7d" is
 * not a deadline, and nothing on this line ever says overdue (ADR 0011).
 */
import React from "react";
import { agoPhrase, daysSince } from "./upkeep-when";
import type { UpkeepSummary } from "./types";

export const UpkeepAge: React.FC<{ upkeep: UpkeepSummary }> = ({ upkeep }) => {
  const last = upkeep.last_done_at;
  return (
    <span className="text-[0.75rem] leading-snug text-ink-quiet">
      last done {agoPhrase(daysSince(last))}
      {last !== null && (
        <>
          {" · "}
          <time dateTime={last}>{new Date(last).toLocaleDateString()}</time>
        </>
      )}
      {upkeep.aim_days !== null && (
        <>
          {" · aim every ~"}
          {upkeep.aim_days}
          {"d"}
        </>
      )}
    </span>
  );
};
