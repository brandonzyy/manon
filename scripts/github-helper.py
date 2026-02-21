"""GitHub 操作工具 — 创建 repo、推送代码、管理 token。

Usage:
    python scripts/github-helper.py auth <token>          # 保存 token
    python scripts/github-helper.py create <name> [--org xxx] [--private]  # 创建 repo
    python scripts/github-helper.py push <name> <local_dir> [--org xxx]    # 创建 + 推送
    python scripts/github-helper.py sync [local_dir]      # 推送到已有 remote
    python scripts/github-helper.py whoami                # 查看当前用户
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "manon" / "github_token"


def _save_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip())
    print(f"Token saved to {TOKEN_FILE}")


def _load_token() -> str:
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    env = os.environ.get("GITHUB_TOKEN", "")
    if env:
        return env
    print("Error: no token. Run: python scripts/github-helper.py auth <token>", file=sys.stderr)
    sys.exit(1)


def _api(method: str, path: str, body: dict | None = None) -> dict:
    """Call GitHub REST API."""
    import urllib.request
    token = _load_token()
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"GitHub API error {e.code}: {err}", file=sys.stderr)
        sys.exit(1)


def cmd_auth(args):
    _save_token(args.token)
    # verify
    try:
        user = _api("GET", "/user")
        print(f"Authenticated as: {user['login']}")
    except SystemExit:
        print("Warning: token saved but verification failed", file=sys.stderr)


def cmd_whoami(args):
    user = _api("GET", "/user")
    print(f"User: {user['login']}")
    print(f"Name: {user.get('name', '')}")
    print(f"URL:  {user['html_url']}")


def cmd_create(args):
    body = {"name": args.name, "private": args.private, "auto_init": False}
    if args.org:
        result = _api("POST", f"/orgs/{args.org}/repos", body)
    else:
        result = _api("POST", "/user/repos", body)
    print(f"Created: {result['html_url']}")
    print(f"Clone:   {result['clone_url']}")
    return result


def cmd_push(args):
    local_dir = Path(args.local_dir).resolve()
    if not local_dir.exists():
        print(f"Error: {local_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # create repo on GitHub
    body = {"name": args.name, "private": args.private, "auto_init": False}
    if args.org:
        result = _api("POST", f"/orgs/{args.org}/repos", body)
    else:
        result = _api("POST", "/user/repos", body)
    clone_url = result["clone_url"]
    print(f"Created: {result['html_url']}")

    # inject token into clone URL for push auth
    token = _load_token()
    auth_url = clone_url.replace("https://", f"https://x-access-token:{token}@")

    # init git if needed, add remote, push
    os.chdir(local_dir)
    cmds = []
    if not (local_dir / ".git").exists():
        cmds.append(["git", "init"])
        cmds.append(["git", "add", "-A"])
        cmds.append(["git", "commit", "-m", "Initial commit"])

    # check if origin exists
    r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True)
    if r.returncode == 0:
        cmds.append(["git", "remote", "set-url", "origin", auth_url])
    else:
        cmds.append(["git", "remote", "add", "origin", auth_url])

    cmds.append(["git", "push", "-u", "origin", "master"])

    for cmd in cmds:
        print(f"  $ {' '.join(cmd[:3])}...")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  Error: {r.stderr[:300]}", file=sys.stderr)
            sys.exit(1)

    # reset remote to clean URL (no token)
    subprocess.run(["git", "remote", "set-url", "origin", clone_url], capture_output=True)
    print(f"Pushed to {result['html_url']}")


def _git_push_with_token(local_dir: Path, remote: str = "origin", branch: str = ""):
    """Temporarily inject token into remote URL, push, then restore."""
    os.chdir(local_dir)
    token = _load_token()

    # get current remote URL
    r = subprocess.run(["git", "remote", "get-url", remote], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error: remote '{remote}' not found", file=sys.stderr)
        sys.exit(1)
    clean_url = r.stdout.strip()

    # detect branch
    if not branch:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        branch = r.stdout.strip() or "master"

    # inject token
    if "github.com" in clean_url:
        auth_url = clean_url.replace("https://", f"https://x-access-token:{token}@")
    else:
        auth_url = clean_url
    subprocess.run(["git", "remote", "set-url", remote, auth_url], capture_output=True)

    # push
    print(f"  Pushing {branch} → {remote} ...")
    r = subprocess.run(["git", "push", "-u", remote, branch], capture_output=True, text=True)
    # restore clean URL
    subprocess.run(["git", "remote", "set-url", remote, clean_url], capture_output=True)

    if r.returncode != 0:
        print(f"  Error: {r.stderr[:300]}", file=sys.stderr)
        sys.exit(1)
    print(f"  Done: {clean_url}")


def cmd_sync(args):
    local_dir = Path(args.local_dir).resolve()
    if not (local_dir / ".git").exists():
        print(f"Error: {local_dir} is not a git repo", file=sys.stderr)
        sys.exit(1)
    _git_push_with_token(local_dir, remote=args.remote, branch=args.branch)


def main():
    p = argparse.ArgumentParser(prog="github-helper", description="GitHub 操作工具")
    sub = p.add_subparsers(dest="command")

    ap = sub.add_parser("auth", help="Save GitHub token")
    ap.add_argument("token")

    sub.add_parser("whoami", help="Show current user")

    cp = sub.add_parser("create", help="Create GitHub repo")
    cp.add_argument("name")
    cp.add_argument("--org", default="")
    cp.add_argument("--private", action="store_true")

    pp = sub.add_parser("push", help="Create repo + push local dir")
    pp.add_argument("name")
    pp.add_argument("local_dir")
    pp.add_argument("--org", default="")
    pp.add_argument("--private", action="store_true")

    sp = sub.add_parser("sync", help="Push to existing GitHub remote")
    sp.add_argument("local_dir", nargs="?", default=".")
    sp.add_argument("--remote", default="origin")
    sp.add_argument("--branch", default="")

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(1)

    {"auth": cmd_auth, "whoami": cmd_whoami, "create": cmd_create, "push": cmd_push, "sync": cmd_sync}[args.command](args)


if __name__ == "__main__":
    main()
