# Delegation model routing

How the orchestrator session delegates to specialists via the omc-slim suite
(`~/.config/opencode/oh-my-opencode-slim.json`, preset `budget-go-v3`). The
user's OpenCode Go plan is ~$10/month: treat inference spends as real money,
keep handoffs tight, and prefer free models for throwaway work.

## The mapping (as configured), for subagent dispatches

| Agent role | Model | Usable from the agent harness? |
|---|---|---|
| fixer (implementation) | `opencode-go/glm-5.3-flash` | Yes — verified online 2026-10-07 |
| oracle / council (hard review, trade-offs) | `opencode-go/minimax-m3` (thinking) | Yes — verified online 2026-10-07 |
| observer (image reading) | `opencode/mimo-v2.6-flash-free` | Yes |
| explorer / librarian / designer (Qwen role) | `obit/unsloth/Qwen3.8-27B-GGUF` | **No** — local-only; not registered in the harness |

OWNER RULE (2026-10-07): when a task maps to a Qwen model, ONLY the `obit/`
Qwen is acceptable. OpenCode-hosted Qwen models are NOT substitutes and must
not be used for any Qwen-role delegation — if `obit/...` is unreachable, fall
back per "Failure handling" (free model or orchestrator's own) and report
which was used.

## Defaults for this repo's sessions

1. **Implementation** (any execution task with files to write): the fixer
   mapping — `opencode-go/glm-5.3-flash`.
2. **Architectural judgement, trade-off review, hard debugging**: the oracle
   mapping — `opencode-go/minimax-m3`.
3. **Scouting / UI design (Qwen roles)**: `obit/unsloth/Qwen3.8-27B-GGUF`
   ONLY (owner rule — never an OpenCode-hosted Qwen). Unreachable from the
   harness: fall back per rule 4 and say so.
4. The orchestrator (planning, coordination, final review, commits) stays on
   its own model; it always reviews delegated output before integrating.

## Failure handling

- "Insufficient account funds" on an OpenCode Go model has occurred once
  (2026-10-07, transient). On a failure, retry once; if still failing, fall
  back to the free model and report the fallback in the final summary —
  never silently.
- Local GGUF agents (explorer/librarian/designer as omc-slim defines them)
  require the user's OpenCode TUI; work inside the agent harness substitutes
  per rule 3 and flags it.
