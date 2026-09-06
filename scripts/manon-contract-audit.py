#!/usr/bin/env python3
"""Contract audit CLI — runs with no model and no server, for hooks and CI.

    manon-contract-audit.py <project_path> [options]

    --json                 machine-readable output
    --tables a,b           subset of: endpoints, configs, states, envelope
    --limit N              rows shown per table in text mode (default 8)
    --baseline <repo_id>   compare against the stored baseline and print the delta
    --update-baseline      rewrite the baseline after reporting
    --fail-on <mode>       exit 1 on: dead | any | new  (default: never)

``--fail-on new`` is the gate mode: it fails only on surfaces that were not
already there, so turning the audit on does not block every push on day one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.contract_audit import TABLES, audit_project  # noqa: E402
from core.contract_audit.report import (  # noqa: E402
    diff_baseline,
    load_baseline,
    render,
    render_delta,
    save_baseline,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("project_path")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tables", default=",".join(TABLES))
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--baseline", default="")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--fail-on", choices=("dead", "any", "new", "never"), default="never")
    parser.add_argument("--exclude", action="append", default=[],
                        help="fnmatch pattern(s) kept out of the evidence corpus — "
                             "e.g. the ratchet's own baseline outputs (self-reference)")
    parser.add_argument("--no-project-excludes", action="store_true",
                        help="ignore the runtime index's custom_excludes — the ratchet "
                             "audits the repo as versioned, not as configured locally")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(args.project_path).expanduser()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    tables = tuple(t.strip() for t in args.tables.split(",") if t.strip())
    unknown = [t for t in tables if t not in TABLES]
    if unknown:
        print(f"unknown table(s): {', '.join(unknown)}; known: {', '.join(TABLES)}", file=sys.stderr)
        return 2

    result = audit_project(str(root), tables=tables, extra_excludes=args.exclude,
                           use_project_excludes=not args.no_project_excludes)

    baseline = load_baseline(args.baseline) if args.baseline else {}
    new_findings = diff_baseline(result, baseline)[0] if args.baseline else []

    if args.json:
        payload = dict(result)
        if args.baseline:
            payload["new_since_baseline"] = [f["id"] for f in new_findings]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.baseline:
        delta = render_delta(result, baseline)
        print(delta if delta else "[manon] 契约对账：无新增死面")
    else:
        print(render(result, limit=args.limit))

    if args.update_baseline and args.baseline:
        save_baseline(args.baseline, result)

    if args.fail_on == "dead" and result["dead"]:
        return 1
    if args.fail_on == "any" and (result["dead"] or result["suspect"]):
        return 1
    if args.fail_on == "new" and new_findings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
