# Delegation model routing

How the orchestrator session delegates to specialists via the omc-slim suite
(`~/.config/opencode/oh-my-opencode-slim.json`, preset `budget-go-v3`). The
user's OpenCode Go plan is ~$10/month: treat inference spends as real money,
keep handoffs tight, and prefer free models for throwaway work.

## The mapping (as configured), for OpenCode subagent dispatches

| Agent role | Model | Usable from the agent harness? |
|---|---|---|
| fixer (implementation) | `opencode-go/glm-5.3-flash` | Yes — verified online 2026-10-07 |
| oracle / council (hard review, trade-offs) | `opencode-go/minimax-m3` (thinking) | Yes — verified online 2026-10-07 |
| explorer / librarian / designer (Qwen role) | `opencode-go/qwen3.8-flash` | Yes — verified online 2026-10-07 (substitute) |
| observer (image reading) | `opencode/mimo-v2.6-flash-free` | Yes |
| explorer / librarian / designer as configured | `obit/unsloth/Qwen3.8-27B-GGUF` | **No** — local-only model; not registered in the harness |

The Qwen roles substitute `opencode-go/qwen3.8-flash` — the same Qwen family
the local GGUF wraps — per the owner's confirmed routing (2026-10-07): when
the file says Qwen, this harness uses that model family via OpenCode Go.

## Defaults for this repo's sessions

1. **Implementation** (any execution task with files to write): the fixer
   mapping — `opencode-go/glm-5.3-flash`.
2. **Architectural judgement, trade-off review, hard debugging**: the oracle
   mapping — `opencode-go/minimax-m3`.
3. **Scouting / UI implementation (Qwen roles)**: the explorer/librarian/
   designer mapping — substitute `opencode-go/qwen3.8-flash` (the local GGUF
   is not reachable from the harness; the Qwen family through OpenCode Go is
   the chosen equivalent).
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
