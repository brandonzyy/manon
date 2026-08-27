#!/usr/bin/env python3
"""Manage .dao quality report for the dao skill.

Commands:
  read <project>                           → JSON of open issues (exists/open/issues)
  init <project>                           → create .dao/ and empty issues.json
  add <project> <layer> <principle> <desc> [approach]  → add issue, print new ID
  done <project> <id> <commit>             → mark issue resolved (✅)
  wip <project> <id> <note>               → mark in-progress (🟡) with note
  changed <project>                        → list files changed since last run
  render <project>                         → regenerate quality-report.md from issues.json
"""
import sys
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime

STATUS_OPEN = "🔴 待处理"
STATUS_WIP  = "🟡 进行中"
STATUS_DONE = "✅ 已完成"

LAYERS = [
    ("A", "架构层 (A1-A7)"),
    ("M", "模块层 (M1-M4)"),
    ("C", "代码层 (C1-C8)"),
]


def dao_dir(project_path):
    return Path(project_path) / ".dao"


def issues_path(project_path):
    return dao_dir(project_path) / "issues.json"


def load_issues(project_path):
    f = issues_path(project_path)
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8"))


def save_issues(project_path, issues):
    issues_path(project_path).write_text(
        json.dumps(issues, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    render(project_path, issues)


def next_id(issues, layer):
    prefix = layer[0].upper()
    nums = [
        int(i["id"].split("-")[1])
        for i in issues
        if i["id"].startswith(prefix + "-") and i["id"].split("-")[1].isdigit()
    ]
    return f"{prefix}-{(max(nums) + 1 if nums else 1):03d}"


def render(project_path, issues):
    """Regenerate .dao/quality-report.md from issues list."""
    project_name = Path(project_path).resolve().name
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# 代码质量报告 — {project_name}",
        "",
        f"> 由 /assurance（结构简化循环）自动维护 | 最后更新: {now}",
        "",
    ]
    for prefix, label in LAYERS:
        layer_issues = [i for i in issues if i["id"].startswith(prefix + "-")]
        lines += [
            f"## {label}",
            "",
            "| ID | 原则 | 描述 | 状态 | 解决思路 | 进展 | Commit |",
            "|----|------|------|------|---------|------|--------|",
        ]
        for i in layer_issues:
            lines.append(
                f"| {i['id']} | {i['principle']} | {i['desc']} "
                f"| {i['status']} | {i.get('approach','')} "
                f"| {i.get('progress','')} | {i.get('commit','—')} |"
            )
        lines.append("")

    report = dao_dir(project_path) / "quality-report.md"
    report.write_text("\n".join(lines), encoding="utf-8")


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_read(project_path):
    issues = load_issues(project_path)
    open_issues = [i for i in issues if i["status"] != STATUS_DONE]
    print(json.dumps({
        "exists": issues_path(project_path).exists(),
        "open": len(open_issues),
        "issues": open_issues,
    }, ensure_ascii=False))


def cmd_init(project_path):
    d = dao_dir(project_path)
    d.mkdir(exist_ok=True)
    if not issues_path(project_path).exists():
        save_issues(project_path, [])
        print("initialized")
    else:
        print("already exists")


def cmd_add(project_path, layer, principle, desc, approach=""):
    issues = load_issues(project_path)
    issue_id = next_id(issues, layer)
    issues.append({
        "id": issue_id,
        "layer": layer,
        "principle": principle,
        "desc": desc,
        "status": STATUS_OPEN,
        "approach": approach,
        "progress": "",
        "commit": "—",
    })
    save_issues(project_path, issues)
    print(issue_id)


DAO_MARKER = Path.home() / ".dao_plan_active"


def cmd_arm(project_path, issue_id, skill_dir, repo_id):
    """Write dao marker before EnterPlanMode so ExitPlanMode hook can inject commit command."""
    ts = datetime.now().isoformat()
    DAO_MARKER.write_text(
        f"{project_path}|||{issue_id}|||{skill_dir}|||{repo_id}|||{ts}",
        encoding="utf-8",
    )
    print(f"dao marker set: {issue_id}")


def cmd_done(project_path, issue_id, commit):
    issues = load_issues(project_path)
    for i in issues:
        if i["id"] == issue_id:
            i["status"] = STATUS_DONE
            i["commit"] = commit
            break
    save_issues(project_path, issues)
    # Remove dao session marker so post_commit hook stops injecting reminders
    DAO_MARKER.unlink(missing_ok=True)
    print(f"{issue_id} → done")


def cmd_wip(project_path, issue_id, note):
    issues = load_issues(project_path)
    for i in issues:
        if i["id"] == issue_id:
            i["status"] = STATUS_WIP
            i["progress"] = note
            break
    save_issues(project_path, issues)
    print(f"{issue_id} → in progress")


def _git(project_path, *args):
    """Run git command, return stdout; return '' on error (cross-platform)."""
    r = subprocess.run(
        ["git", "-C", str(project_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout if r.returncode == 0 else ""


def cmd_changed(project_path):
    """List source files changed since last run; update .dao/.last-run timestamp."""
    last_run_file = dao_dir(project_path) / ".last-run"
    if last_run_file.exists():
        since = last_run_file.read_text().strip()
        out = _git(project_path, "log", "--name-only", "--pretty=format:", f"--after={since}")
    else:
        out = _git(project_path, "diff", "--name-only", "HEAD~5..HEAD")
    last_run_file.write_text(datetime.now().isoformat())
    files = sorted({f for f in out.strip().split("\n") if f and not f.startswith(".")})
    print(json.dumps(files))


def cmd_render(project_path):
    render(project_path, load_issues(project_path))
    print("rendered")


# ── main ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "read":    lambda a: cmd_read(a[0]),
    "init":    lambda a: cmd_init(a[0]),
    "add":     lambda a: cmd_add(a[0], a[1], a[2], a[3], a[4] if len(a) > 4 else ""),
    "arm":     lambda a: cmd_arm(a[0], a[1], a[2], a[3]),
    "done":    lambda a: cmd_done(a[0], a[1], a[2]),
    "wip":     lambda a: cmd_wip(a[0], a[1], a[2]),
    "changed": lambda a: cmd_changed(a[0]),
    "render":  lambda a: cmd_render(a[0]),
}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass  # Python < 3.7 fallback
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    try:
        COMMANDS[args[0]](args[1:])
    except (IndexError, TypeError):
        print("Usage error. Run without args to see help.")
        sys.exit(1)


if __name__ == "__main__":
    main()
