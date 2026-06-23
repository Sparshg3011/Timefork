"""Command-line tool for Timefork: list, inspect, fork, and diff runs.

  timefork ls
  timefork show RUN
  timefork fork RUN --at K [--system-prompt FILE] [--set key=value ...]
  timefork diff RUN_A RUN_B
"""

import argparse
from pathlib import Path

from .diff import _describe, diff_runs
from .events import connect, read_events
from .fork import children_of, fork_run


def cmd_ls(args):
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_id, status, agent_name, parent_run_id, fork_seq "
            "FROM runs ORDER BY created_at DESC LIMIT %s",
            (args.limit,),
        ).fetchall()
    for run_id, status, agent, parent, fork_seq in rows:
        lineage = f"  (fork of {parent[:8]} @ {fork_seq})" if parent else ""
        print(f"{run_id}  {status:10} {agent}{lineage}")


def cmd_show(args):
    with connect() as conn:
        info = conn.execute(
            "SELECT agent_name, status, parent_run_id, fork_seq FROM runs WHERE run_id = %s",
            (args.run,),
        ).fetchone()
        if info is None:
            raise SystemExit(f"no run with id {args.run}")
        events = read_events(conn, args.run)
        forks = children_of(conn, args.run)
    agent, status, parent, fork_seq = info
    print(f"run {args.run}  [{status}]  agent={agent}")
    if parent:
        print(f"  forked from {parent} at step {fork_seq}")
    if forks:
        print(f"  forks: {[(c[:8], s) for c, s in forks]}")
    print("  events:")
    for e in events:
        print(f"    {e.seq:>3}  {_describe(e)}")


def cmd_fork(args):
    patch = {}
    if args.system_prompt:
        patch["system_prompt"] = Path(args.system_prompt).read_text()
    for kv in args.set or []:
        key, _, value = kv.partition("=")
        patch[key] = value
    with connect() as conn:
        child_id = fork_run(conn, args.run, args.at, patch)
    print(child_id)


def cmd_diff(args):
    with connect() as conn:
        d = diff_runs(conn, args.a, args.b)
    print(f"shared prefix: {d['shared']} steps; first divergence at seq {d['diverge_at']}")
    for r in d["rows"]:
        if r.same:
            print(f"  {r.seq:>3}  =  {r.a}")
        else:
            print(f"  {r.seq:>3}  X  A: {r.a or '-'}")
            print(f"          B: {r.b or '-'}")
    print("(a fork is a fresh experiment, not proof of what the parent would have done)")


def main():
    parser = argparse.ArgumentParser(
        prog="timefork", description="Durable agent runtime: inspect, fork, and diff runs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ls", help="list recent runs")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("show", help="show a run's events and lineage")
    p.add_argument("run")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("fork", help="fork a run at a step, with a patch")
    p.add_argument("run")
    p.add_argument("--at", type=int, required=True, help="step to fork at")
    p.add_argument("--system-prompt", help="file whose contents become the system_prompt patch")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", help="a patch entry (repeatable)")
    p.set_defaults(func=cmd_fork)

    p = sub.add_parser("diff", help="diff two runs")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_diff)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
