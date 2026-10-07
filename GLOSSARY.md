# LifeOS

LifeOS is a single-user personal web app built as independent modules: finance
(live, vocabulary documented in `PRODUCT.md`) and `life` (personal
life-management, currently in discovery). This glossary defines the `life`
module's language. If the two modules ever need to argue over one word, the
finance terms merge here.

## Language

### The four kinds

**Thought**:
Anything captured in one line, before any sorting. A Thought carries no
deadline, no project and no required decisions. It stays a Thought until it
becomes an Action, a Goal or an Upkeep — or rests in the Inbox indefinitely,
which is a normal state, not debt.
_Avoid_: idea, brain dump, note, item

**Goal**:
Something you are becoming or finishing, bigger than one sitting. A Goal
carries a why, a Minimum and a Current thing. Never overdue, never failing.
_Avoid_: project, objective, resolution

**Action**:
A step doable in one sitting. May carry a date, an urgent flag, subactions and
membership of a Goal — none of which are required.
_Avoid_: task, to-do, todo, item

**Upkeep**:
Recurring household or self maintenance, defined by a usual cadence and the
moment it was last done. Never overdue and never accumulating backlog;
skipping one only moves its next opportunity.
_Avoid_: habit, chore, routine, maintenance

**Aim**:
How often the user intends an Upkeep to happen, set at creation. Kept beside
the earned cadence of receipts; the Aim is intent, never a deadline.
_Avoid_: due date, target, schedule, frequency

### Serving today

**Receipt**:
A record that an Upkeep was done at a moment — the fact from which the
earned cadence derives. Captures may file receipts only on confident
matches; the undo of a capture-created receipt deletes only that capture's
own receipt.
_Avoid_: log, entry, check-in

**Inbox**:
Where unsorted Thoughts rest. A willing resting place, not a queue to be
cleared.
_Avoid_: queue, backlog, GTD inbox

**Do now list**:
The capped list of Actions chosen for today, shown on the first screen. The
cap counts whole entries only — an Action's subactions never inflate it.
_Avoid_: today view, daily list

**Dashboard**:
The app's root screen. Composes the today list, a vision strip, a finance
summary and the pixel graph from both modules at the app layer.
_Avoid_: overview, home, dashboard page

**Minimum**:
The smallest engagement of a Goal that counts as keeping it, e.g. "pick up the
guitar for five minutes". Honest text, meant to survive a bad day.
_Avoid_: streak, target, tier, habit chain

**Current thing**:
A Goal's single focus right now. One per Goal: choosing a new one replaces the
old.
_Avoid_: next action, focus

### Showing why

**Vision board**:
Images and phrases that show the life being built, each tagged so a relevant
few can surface on the first screen.
_Avoid_: mood board, dream wall

**Wishlist**:
A thing wanted eventually, attached to a Goal rather than standing alone. Not
an Action; carries no date and no pressure.
_Avoid_: someday/maybe, shopping list
