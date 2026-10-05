"""cli.py — the LifeOS command line.

One subcommand today: `replay`, which rebuilds a batch's journal side from the
file the batch stored (`finance.api.replay`). It opens its own session from
the same session factory the API uses (`finance.db.get_sessionmaker` — the
accessor `finance.api.deps.get_session` is built on), runs the replay inside
one transaction, prints a one-line report, and exits 0. A refused replay exits
1; anything else — a broken command line, an unknown failure — exits 2.
"""

from __future__ import annotations

import argparse
import sys

from finance.api.replay import ReplayError, replay_batch
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
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - the console script calls main()
    raise SystemExit(main())
