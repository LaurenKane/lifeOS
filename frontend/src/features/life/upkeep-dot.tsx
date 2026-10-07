/* UpkeepDot — the aging indicator, and the aging RULE.
 *
 * fresh: inside the trim (or no trim yet, so no judgment is possible)
 * amber: past the trim, within the ~2-day catch-up window, or quietly beyond.
 * There is deliberately no red: being later is information, and ADR 0011 says
 * the Dashboard never renders an overdue state.
 *
 * The day-count comes from `upkeep-when`, so the dot and the UpkeepAge line
 * beside it can only ever disagree if the RULE disagrees with itself.
 */
import React from "react";
import { daysSince } from "./upkeep-when";
import type { UpkeepSummary } from "./types";

export const UpkeepDot: React.FC<{ upkeep: UpkeepSummary }> = ({ upkeep }) => {
  const since = daysSince(upkeep.last_done_at);
  const aim = upkeep.aim_days;
  let state: "fresh" | "aging" | "aged";
  if (since === null || aim === null) {
    state = "fresh";
  } else if (since <= aim) {
    state = "fresh";
  } else if (since <= aim + 2) {
    state = "aging";
  } else {
    state = "aged";
  }
  const tint =
    /* The deliberate palette: fresh = bright lime; aging = a calmer cyan;
     * aged = ink at a quarter. Blue-grey-ward, never red — the amber of the
     * scheme is the fade, not an alarm state (Q26: "never red"). */
    state === "fresh" ? "bg-lime" : state === "aging" ? "bg-cyan" : "bg-ink/25";
  return (
    <span
      role="img"
      aria-label={`upkeep ${upkeep.title} is ${state}`}
      className={"inline-block h-2.5 w-2.5 rounded-full " + tint}
    />
  );
};
