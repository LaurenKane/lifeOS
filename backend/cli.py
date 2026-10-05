"""cli.py — the LifeOS command line.

Two subcommands: `replay`, which rebuilds a batch's journal side from the
file the batch stored (`finance.api.replay`), and `sweep`, which links
unmatched transfer legs (`finance.api.sweep`). Each opens its own session
from the same session factory the API uses (`finance.db.get_sessionmaker` —
the accessor `finance.api.deps.get_session` is built on), runs inside one
transaction, prints a one-line report, and exits 0. A refused replay exits
1; anything else — a broken command line, an unknown failure — exits 2.

There is no worker container (PHASE2-PLAN §3c): the sweep is run by host
cron, e.g. `0 3 * * * lifeos-cli sweep`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from finance.api.replay import ReplayError, replay_batch
from finance.api.sweep import sweep_unmatched
from finance.db import get_sessionmaker

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    """The `lifeos-cli` command line: one subcommand per maintenance task."""
    parser = argparse.ArgumentParser(
        prog="lifeos-cli", description="LifeOS maintenance commands"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    replay = subcommands.add_parser(
        "replay", help="rebuild a batch's journal side from its stored file"
    )
    replay.add_argument(
        "--batch-id", type=int, required=True, help="the import_batch id"
    )
    sweep = subcommands.add_parser(
        "sweep", help="link unmatched transfer legs in a date window"
    )
    sweep.add_argument(
        "--from",
        dest="from_",
        type=str,
        default=None,
        help="first entry date in scope (YYYY-MM-DD), default 90 days back",
    )
    sweep.add_argument(
        "--to",
        type=str,
        default=None,
        help="last entry date in scope (YYYY-MM-DD), default today",
    )
    return parser


def _replay(*, batch_id: int) -> int:
    """Run one replay in its own transaction. 0 on success, else 1 or 2."""
    try:
        factory = get_sessionmaker()
    except Exception as exc:
        print(f"cannot open a session: {exc}", file=sys.stderr)
        return 2
    try:
        with factory() as session:
            with session.begin():
                report = replay_batch(session, batch_id=batch_id)
    except ReplayError as exc:
        print(f"replay of batch {batch_id} refused: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"replay of batch {batch_id} failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"replay batch {report.batch_id}: parsed={report.parsed_rows} "
        f"reposted={report.reposted} pending={report.restored_pending} "
        f"categorized={report.category_rules_applied}"
    )
    return 0


def _parse_day(value: str | None, *, flag: str) -> dt.date | None:
    """One YYYY-MM-DD flag, or None when the flag was not given."""
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{flag} must be YYYY-MM-DD, got {value!r}") from None


def _sweep(*, from_: str | None, to: str | None) -> int:
    """Run one sweep in its own transaction. 0 on success, else 2."""
    try:
        start = _parse_day(from_, flag="--from")
        end = _parse_day(to, flag="--to")
    except ValueError as exc:
        print(f"sweep refused: {exc}", file=sys.stderr)
        return 2
    try:
        factory = get_sessionmaker()
    except Exception as exc:
        print(f"cannot open a session: {exc}", file=sys.stderr)
        return 2
    try:
        with factory() as session:
            with session.begin():
                report = sweep_unmatched(session, from_=start, to_=end)
    except Exception as exc:
        print(f"sweep failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"sweep examined={report.outbounds_examined} "
        f"auto={report.auto_matched} "
        f"multi={report.review_queued_multi} "
        f"low_confidence={report.review_queued_low_confidence} "
        f"already_matched={report.already_matched} "
        f"skipped={report.skipped_by_user}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: The argument list, without the program name. None reads
            `sys.argv`, which is what the console script passes.

    Returns:
        0 on success, 1 on a refused replay, 2 on a broken command line or an
        unknown failure. (`argparse` errors exit 2 themselves, via `SystemExit`
        rather than a return, which is the same code by the same convention.)
    """
    args = vars(_build_parser().parse_args(argv))
    command = str(args["command"])
    if command == "replay":
        return _replay(batch_id=int(args["batch_id"]))
    if command == "sweep":
        return _sweep(from_=args["from_"], to=args["to"])
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - the console script calls main()
    raise SystemExit(main())
