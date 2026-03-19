"""dao-pick.py — 交互式问题选择器

dao 分析完成后运行此脚本，上下键选择要处理的 issue，回车确认。

用法:
  python dao-pick.py <project_path>              # 显示所有 issue
  python dao-pick.py <project_path> --open       # 只显示待处理
  python dao-pick.py <project_path> --layer M    # 只显示指定层（A/M/C）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import questionary
from questionary import Style

# project_path 从第一个非-flag 参数读取，默认为脚本所在目录
_args_raw = sys.argv[1:]
_positional = [a for a in _args_raw if not a.startswith("--")]
PROJECT_PATH = Path(_positional[0]) if _positional else Path(__file__).parent
DAO_DIR = PROJECT_PATH / ".dao"
ISSUES_FILE = DAO_DIR / "issues.json"
DAO_REPORT = Path(__file__).parent / "dao-report.py"

STYLE = Style([
    ("qmark",        "fg:#00bfa5 bold"),
    ("question",     "bold"),
    ("answer",       "fg:#00bfa5 bold"),
    ("pointer",      "fg:#00bfa5 bold"),
    ("highlighted",  "fg:#00bfa5 bold"),
    ("selected",     "fg:#00bfa5"),
    ("separator",    "fg:#555555"),
    ("instruction",  "fg:#888888"),
])

STATUS_ICON = {
    "待处理": "🔴",
    "已修复": "✅",
    "已关闭": "✅",
    "不修复": "⚪",
}

LAYER_LABEL = {"A": "架构", "M": "模块", "C": "代码"}


def load(only_open: bool = False, layer: str | None = None) -> list[dict]:
    issues = json.loads(ISSUES_FILE.read_text(encoding="utf-8"))
    if only_open:
        issues = [i for i in issues if "待处理" in i.get("status", "")]
    if layer:
        issues = [i for i in issues if i.get("layer", "") == layer.upper()]
    return issues


def format_choice(issue: dict) -> questionary.Choice:
    status = issue.get("status", "")
    icon = next((v for k, v in STATUS_ICON.items() if k in status), "❓")
    layer = LAYER_LABEL.get(issue.get("layer", ""), "?")
    principle = issue.get("principle", "")
    desc = issue.get("desc", "")
    # 截断长描述
    if len(desc) > 55:
        desc = desc[:52] + "..."
    title = f"{icon} [{issue['id']}] {layer}/{principle}  {desc}"
    return questionary.Choice(title=title, value=issue["id"])


def show_detail(issue: dict) -> None:
    print()
    print(f"  ID        {issue['id']}")
    print(f"  层/原则   {issue.get('layer','')}/{issue.get('principle','')}")
    print(f"  状态      {issue.get('status','')}")
    print(f"  描述      {issue.get('desc','')}")
    print(f"  思路      {issue.get('approach','')}")
    if issue.get("progress"):
        print(f"  进展      {issue.get('progress','')}")
    print(f"  Commit    {issue.get('commit','—')}")
    print()


def main() -> None:
    args = sys.argv[1:]
    only_open = "--open" in args
    layer = None
    if "--layer" in args:
        idx = args.index("--layer")
        if idx + 1 < len(args):
            layer = args[idx + 1]
            if layer.startswith("--"):
                layer = None

    issues = load(only_open=only_open, layer=layer)

    if not issues:
        print("没有符合条件的 issue。")
        sys.exit(0)

    choices = [format_choice(i) for i in issues]
    choices.append(questionary.Choice(title="── 退出 ──", value="__exit__"))

    while True:
        answer = questionary.select(
            "选择要处理的 issue：",
            choices=choices,
            style=STYLE,
        ).ask()

        if answer is None or answer == "__exit__":
            print("已退出。")
            sys.exit(0)

        issue = next(i for i in issues if i["id"] == answer)
        show_detail(issue)

        action = questionary.select(
            f"对 {answer} 执行：",
            choices=[
                questionary.Choice("📋  生成实施计划（Plan）", value="plan"),
                questionary.Choice("✅  标记已完成", value="done"),
                questionary.Choice("⚪  标记不修复", value="wontfix"),
                questionary.Choice("↩  返回列表", value="back"),
                questionary.Choice("── 退出 ──", value="exit"),
            ],
            style=STYLE,
        ).ask()

        if action is None or action == "exit":
            print("已退出。")
            sys.exit(0)

        elif action == "back":
            continue

        elif action == "plan":
            print(f"\n正在为 {answer} 生成实施计划...\n")
            # 输出 issue ID 供外部脚本捕获，或直接调用 dao-report
            print(f"SELECTED_ISSUE={answer}")
            sys.exit(0)

        elif action == "done":
            commit = questionary.text("输入 commit hash（留空跳过）：", style=STYLE).ask()
            if commit is None:
                continue
            commit = commit.strip() or "—"
            subprocess.run(
                [sys.executable, str(DAO_REPORT), "done", answer, commit],
                cwd=str(PROJECT_PATH),
                check=True,
            )
            # 刷新列表
            issues = load(only_open=only_open, layer=layer)
            choices = [format_choice(i) for i in issues]
            choices.append(questionary.Choice(title="── 退出 ──", value="__exit__"))
            print(f"✅ {answer} 已标记完成。\n")

        elif action == "wontfix":
            subprocess.run(
                [sys.executable, str(DAO_REPORT), "wontfix", answer],
                cwd=str(PROJECT_PATH),
                check=True,
            )
            issues = load(only_open=only_open, layer=layer)
            choices = [format_choice(i) for i in issues]
            choices.append(questionary.Choice(title="── 退出 ──", value="__exit__"))
            print(f"⚪ {answer} 已标记不修复。\n")


if __name__ == "__main__":
    main()
