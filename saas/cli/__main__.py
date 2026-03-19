"""manon-cli — command-line interface for Manon SaaS API.

Usage:
    python -m saas.cli repos list
    python -m saas.cli repos create --name myrepo --git-url https://...
    python -m saas.cli repos delete <id>
    python -m saas.cli index-status <repo_id>
    python -m saas.cli search <repo_id> "authentication"
    python -m saas.cli graph <repo_id> "ClassName"
    python -m saas.cli impact <repo_id>
    python -m saas.cli usage

Env vars:
    MANON_API_URL   (default: http://localhost:3700)
    MANON_API_KEY   (required)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .client import ManonClient


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _client() -> ManonClient:
    url = _env("MANON_API_URL", "http://localhost:3700")
    key = _env("MANON_API_KEY")
    if not key:
        print("Error: set MANON_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)
    return ManonClient(url, key)


def _pp(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


# ── Command handlers ──────────────────────────────────

def cmd_repos(args):
    c = _client()
    if args.sub == "list":
        repos = c.list_repos()
        if not repos:
            print("No repos.")
            return
        for r in repos:
            icon = {"done": "+", "indexing": "~", "error": "x"}.get(r["index_status"], "-")
            print(f"  {icon} {r['id']}  {r['name']:<20s}  {r['index_status']}")
    elif args.sub == "create":
        result = c.create_repo(args.name, git_url=args.git_url or "", branch=args.branch, local_path=args.local_path)
        print(f"Created repo {result['id']} ({result['name']})")
    elif args.sub == "get":
        _pp(c.get_repo(args.id))
    elif args.sub == "delete":
        c.delete_repo(args.id)
        print(f"Deleted {args.id}")


def cmd_index_status(args):
    c = _client()
    _pp(c.index_status(args.repo_id))


def cmd_search(args):
    c = _client()
    result = c.search(args.repo_id, args.query, top_k=args.top_k, depth=args.depth)
    if result.get("context"):
        print(result["context"])
    else:
        _pp(result)


def cmd_graph(args):
    c = _client()
    _pp(c.graph(args.repo_id, args.symbol, depth=args.depth))


def cmd_impact(args):
    c = _client()
    _pp(c.impact(args.repo_id, commit=args.commit, max_depth=args.max_depth))


def cmd_usage(args):
    c = _client()
    _pp(c.usage(days=args.days))


# ── Argument parser ───────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    p = argparse.ArgumentParser(prog="manon-cli", description="Manon SaaS CLI")
    sub = p.add_subparsers(dest="command")

    rp = sub.add_parser("repos", help="Manage repos")
    rs = rp.add_subparsers(dest="sub")
    rs.add_parser("list", help="List repos")
    rc = rs.add_parser("create", help="Create repo")
    rc.add_argument("--name", required=True)
    rc.add_argument("--git-url", default="")
    rc.add_argument("--branch", default="main")
    rc.add_argument("--local-path", default=None)
    rg = rs.add_parser("get", help="Get repo details")
    rg.add_argument("id")
    rd = rs.add_parser("delete", help="Delete repo")
    rd.add_argument("id")

    isp = sub.add_parser("index-status", help="Check index status")
    isp.add_argument("repo_id")

    sp = sub.add_parser("search", help="Semantic search")
    sp.add_argument("repo_id")
    sp.add_argument("query")
    sp.add_argument("--top-k", type=int, default=10)
    sp.add_argument("--depth", type=int, default=1)

    gp = sub.add_parser("graph", help="Graph traversal")
    gp.add_argument("repo_id")
    gp.add_argument("symbol")
    gp.add_argument("--depth", type=int, default=1)

    imp = sub.add_parser("impact", help="Impact analysis")
    imp.add_argument("repo_id")
    imp.add_argument("--commit", default="HEAD")
    imp.add_argument("--max-depth", type=int, default=2)

    up = sub.add_parser("usage", help="View usage stats")
    up.add_argument("--days", type=int, default=30)

    return p


def main():
    p = _build_arg_parser()
    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(1)

    handlers = {
        "repos": cmd_repos,
        "index-status": cmd_index_status,
        "search": cmd_search,
        "graph": cmd_graph,
        "impact": cmd_impact,
        "usage": cmd_usage,
    }
    try:
        handlers[args.command](args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
