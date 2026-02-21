#!/usr/bin/env python3
"""manon-admin.py — CLI for Manon SaaS admin operations.

Usage:
    python scripts/manon-admin.py tenants list
    python scripts/manon-admin.py tenants create <name> [--tier pro]
    python scripts/manon-admin.py tenants update <id> --tier pro
    python scripts/manon-admin.py keys list <tenant_id>
    python scripts/manon-admin.py keys create <tenant_id> [--label "my key"]
    python scripts/manon-admin.py keys revoke <tenant_id> <key>

Env vars:
    MANON_API_URL     — API base URL (default: http://localhost:3700)
    MANON_ADMIN_SECRET — admin secret (or use --secret)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

API_URL = os.environ.get("MANON_API_URL", "http://localhost:3700")
ADMIN_SECRET = os.environ.get("MANON_ADMIN_SECRET", "")


def _headers(secret: str) -> dict:
    return {"X-Admin-Secret": secret}


def _print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _request(method: str, path: str, secret: str, **kwargs):
    url = f"{API_URL}{path}"
    try:
        r = httpx.request(method, url, headers=_headers(secret), timeout=30, **kwargs)
    except httpx.ConnectError:
        print(f"Error: cannot connect to {API_URL}", file=sys.stderr)
        sys.exit(1)
    if r.status_code >= 400:
        print(f"Error {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    return r.json()


# ── Commands ──────────────────────────────────────────
def cmd_tenants_list(args):
    data = _request("GET", "/admin/tenants", args.secret)
    if not data:
        print("No tenants found.")
        return
    for t in data:
        print(f"  {t['id']}  {t['name']:<20s}  tier={t['tier']}  repos={t.get('repo_count', '?')}  keys={t.get('key_count', '?')}  created={t['created_at']}")


def cmd_tenants_create(args):
    data = _request("POST", "/admin/tenants", args.secret, json={"name": args.name, "tier": args.tier})
    print(f"Created tenant: {data['id']}")
    print(f"  Name: {data['name']}")
    print(f"  Tier: {data['tier']}")
    print(f"  API Key: {data['api_key']}")


def cmd_tenants_update(args):
    params = {}
    if args.tier:
        params["tier"] = args.tier
    if args.name:
        params["name"] = args.name
    data = _request("PATCH", f"/admin/tenants/{args.id}", args.secret, params=params)
    print(f"Updated tenant {data['id']}: tier={data['tier']} name={data['name']}")


def cmd_keys_list(args):
    data = _request("GET", f"/admin/tenants/{args.tenant_id}/keys", args.secret)
    if not data:
        print("No keys found.")
        return
    for k in data:
        status = "active" if k["active"] else "revoked"
        print(f"  {k['key']}  label={k['label']}  status={status}  created={k['created_at']}")


def cmd_keys_create(args):
    data = _request("POST", f"/admin/tenants/{args.tenant_id}/keys", args.secret, params={"label": args.label})
    print(f"Created key: {data['key']}")
    print(f"  Label: {data['label']}")


def cmd_keys_revoke(args):
    data = _request("DELETE", f"/admin/tenants/{args.tenant_id}/keys/{args.key}", args.secret)
    print(f"Key revoked: {data['key']}")


# ── CLI ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Manon SaaS Admin CLI")
    parser.add_argument("--secret", default=ADMIN_SECRET, help="Admin secret (or set MANON_ADMIN_SECRET)")
    parser.add_argument("--api-url", default=API_URL, help="API base URL")
    sub = parser.add_subparsers(dest="group", required=True)

    # tenants
    t_sub = sub.add_parser("tenants").add_subparsers(dest="action", required=True)

    t_sub.add_parser("list")

    t_create = t_sub.add_parser("create")
    t_create.add_argument("name")
    t_create.add_argument("--tier", default="free")

    t_update = t_sub.add_parser("update")
    t_update.add_argument("id")
    t_update.add_argument("--tier")
    t_update.add_argument("--name")

    # keys
    k_sub = sub.add_parser("keys").add_subparsers(dest="action", required=True)

    k_list = k_sub.add_parser("list")
    k_list.add_argument("tenant_id")

    k_create = k_sub.add_parser("create")
    k_create.add_argument("tenant_id")
    k_create.add_argument("--label", default="admin-created")

    k_revoke = k_sub.add_parser("revoke")
    k_revoke.add_argument("tenant_id")
    k_revoke.add_argument("key")

    args = parser.parse_args()
    if args.api_url != API_URL:
        global API_URL
        API_URL = args.api_url

    dispatch = {
        ("tenants", "list"): cmd_tenants_list,
        ("tenants", "create"): cmd_tenants_create,
        ("tenants", "update"): cmd_tenants_update,
        ("keys", "list"): cmd_keys_list,
        ("keys", "create"): cmd_keys_create,
        ("keys", "revoke"): cmd_keys_revoke,
    }
    fn = dispatch.get((args.group, args.action))
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
