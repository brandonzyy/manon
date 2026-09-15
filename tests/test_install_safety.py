"""安装器写别家配置文件的判据：只动 manon 自己名下的东西、读不懂就不写、写要原子。

判例（2026-09-15）：
- `_update_settings_hooks` 按 `str(整个分组)` 匹配 manon 的文件名，与 post_commit.py 同组的
  `gitee_pr_watch.py --register` 被连组删掉（先后两次），读失败时还会把整份 settings.json 当空表写回；
- `write_mcp_json` 往 ~/.claude.json 顺手塞一条 playwright；
- 退役 skill 按名字 `rm -rf`，同名的用户 skill 一起没了。
每条修复成对：该保留的现场 + 把修复拆掉必须红的现场。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from manon_mcp import _hooks, _safe_config

SAFE_CLI = Path(_safe_config.__file__)


def _desired(home: Path) -> tuple[list, list, list]:
    d = home / ".claude" / "hooks"
    return _hooks._build_claude_hook_entries(
        str(d / "pre_search.py"), str(d / "pre_agent_plan.py"), str(d / "post_commit.py"),
        str(d / "pre_enter_plan.py"), str(d / "stop_dao.py"))


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# ── settings.json：按单条钩子动 ──────────────────────────────────────────
class TestSettingsHooks:
    def test_同组的别人钩子留下(self, tmp_path):
        pre, post, stop = _desired(tmp_path)
        other = {"type": "command", "command": "/usr/bin/python3 /x/gitee_pr_watch.py --register"}
        old_manon = {"type": "command", "command": f"/old/venv/python {tmp_path}/.claude/hooks/post_commit.py"}
        f = tmp_path / ".claude" / "settings.json"
        _write(f, {"env": {"K": "v"}, "permissions": {"allow": ["Bash"]},
                   "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [old_manon, other]}]}})
        assert _hooks._update_settings_hooks(f, pre, post, stop) is True
        got = json.loads(f.read_text(encoding="utf-8"))
        assert got["env"] == {"K": "v"} and got["permissions"] == {"allow": ["Bash"]}
        commands = [h["command"] for g in got["hooks"]["PostToolUse"] for h in g["hooks"]]
        assert other["command"] in commands, "别人和 manon 同组的钩子被连组删掉了"
        assert old_manon["command"] not in commands
        assert post[0]["hooks"][0]["command"] in commands

    def test_manon独占的组原地换内容不挪位置(self, tmp_path):
        pre, post, stop = _desired(tmp_path)
        mine = {"matcher": "Write", "hooks": [{"type": "command", "command": "a.sh"}]}
        f = tmp_path / "settings.json"
        _write(f, {"hooks": {"PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command",
                                           "command": f"py {tmp_path}/.claude/hooks/post_commit.py --old"}]},
            mine]}})
        _hooks._update_settings_hooks(f, pre, post, stop)
        groups = json.loads(f.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
        assert groups == [post[0], mine]

    def test_退役钩子只摘那一条(self, tmp_path):
        pre, post, stop = _desired(tmp_path)
        keep = {"type": "command", "command": "keep.sh"}
        retired = {"type": "command", "command": f"py {tmp_path}/.claude/hooks/post_exit_plan.py"}
        f = tmp_path / "settings.json"
        _write(f, {"hooks": {"PreToolUse": [{"matcher": "ExitPlanMode", "hooks": [retired, keep]}]}})
        _hooks._update_settings_hooks(f, pre, post, stop)
        groups = json.loads(f.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        assert {"matcher": "ExitPlanMode", "hooks": [keep]} in groups

    def test_名字像但不是manon路径的钩子不动(self, tmp_path):
        pre, post, stop = _desired(tmp_path)
        lookalike = {"type": "command", "command": "python3 /opt/tools/post_commit.py"}
        f = tmp_path / "settings.json"
        _write(f, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [lookalike]}]}})
        _hooks._update_settings_hooks(f, pre, post, stop)
        commands = [h["command"] for g in json.loads(f.read_text(encoding="utf-8"))["hooks"]["PostToolUse"]
                    for h in g["hooks"]]
        assert lookalike["command"] in commands

    @pytest.mark.parametrize("body", ['{"env": {"K": 1},', "[]", '{"hooks": []}',
                                      '{"hooks": {"PostToolUse": {}}}'])
    def test_读不懂就不写(self, tmp_path, body):
        pre, post, stop = _desired(tmp_path)
        f = tmp_path / "settings.json"
        f.write_text(body, encoding="utf-8")
        assert _hooks._update_settings_hooks(f, pre, post, stop) is False
        assert f.read_text(encoding="utf-8") == body

    def test_第二次不写且保留权限位(self, tmp_path):
        pre, post, stop = _desired(tmp_path)
        f = tmp_path / "settings.json"
        _write(f, {"hooks": {}})
        os.chmod(f, 0o600)
        assert _hooks._update_settings_hooks(f, pre, post, stop) is True
        body, ino = f.read_bytes(), f.stat().st_ino
        assert _hooks._update_settings_hooks(f, pre, post, stop) is False
        assert f.read_bytes() == body and f.stat().st_ino == ino
        assert stat.S_IMODE(f.stat().st_mode) == 0o600


# ── 客户端 MCP 配置：只动 manon 那一条 ─────────────────────────────────
class TestMcpJson:
    BASE = {"command": "bash", "args": ["/m/launch_mcp.sh"]}

    def test_只动manon那一条且不再塞playwright(self, tmp_path):
        f = tmp_path / ".claude.json"
        _write(f, {"numStartups": 7, "projects": {"/x": {"a": 1}},
                   "mcpServers": {"other": {"command": "o"}}})
        assert _safe_config.upsert_mcp_server(f, ("mcpServers",), self.BASE, "k", "auto") is True
        got = json.loads(f.read_text(encoding="utf-8"))
        assert got["numStartups"] == 7 and got["projects"] == {"/x": {"a": 1}}
        assert set(got["mcpServers"]) == {"other", "manon"}
        assert got["mcpServers"]["manon"] == {**self.BASE, "env": {"MANON_API_KEY": "k"}}

    def test_嵌套容器与type(self, tmp_path):
        f = tmp_path / "config.json"
        _write(f, {"plugins": {"x": True}})
        _safe_config.upsert_mcp_server(f, ("mcp", "servers"), {"type": "stdio", **self.BASE},
                                       "k", "http://u")
        got = json.loads(f.read_text(encoding="utf-8"))
        assert got["plugins"] == {"x": True}
        assert got["mcp"]["servers"]["manon"]["env"] == {"MANON_API_KEY": "k", "MANON_API_URL": "http://u"}
        assert list(got["mcp"]["servers"]["manon"])[0] == "type"

    def test_没变化不写(self, tmp_path):
        f = tmp_path / "mcp.json"
        _safe_config.upsert_mcp_server(f, ("mcpServers",), self.BASE, "k", "auto")
        ino = f.stat().st_ino
        assert _safe_config.upsert_mcp_server(f, ("mcpServers",), self.BASE, "k", "auto") is False
        assert f.stat().st_ino == ino

    def test_读不懂抛错且原文件不动(self, tmp_path):
        f = tmp_path / "mcp.json"
        f.write_text("{broken", encoding="utf-8")
        with pytest.raises(_safe_config.UnreadableConfig):
            _safe_config.upsert_mcp_server(f, ("mcpServers",), self.BASE, "k", "auto")
        assert f.read_text(encoding="utf-8") == "{broken"

    def test_命令行读不懂返回3(self, tmp_path):
        f = tmp_path / "mcp.json"
        f.write_text("[1, 2]", encoding="utf-8")
        r = subprocess.run([sys.executable, str(SAFE_CLI), "mcp", str(f), "mcpServers",
                            "--api-key", "k", "--api-url", "auto", "--command", "bash", "--arg", "x"],
                           capture_output=True, text=True)
        assert r.returncode == 3 and f.read_text(encoding="utf-8") == "[1, 2]"

    def test_命令行正常写入(self, tmp_path):
        f = tmp_path / "sub" / "mcp.json"
        r = subprocess.run([sys.executable, str(SAFE_CLI), "mcp", str(f), "mcp.servers", "--type", "stdio",
                            "--api-key", "k", "--api-url", "auto", "--command", "bash", "--arg", "/l.sh"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert json.loads(f.read_text(encoding="utf-8"))["mcp"]["servers"]["manon"]["args"] == ["/l.sh"]


# ── 退役 skill：只摘 manon 自己装的那一版 ───────────────────────────────
class TestRetiredSkills:
    def test_指纹对上才删(self, tmp_path, monkeypatch):
        body = b"---\nname: dao\n---\nmanon shipped\n"
        monkeypatch.setattr(_safe_config, "RETIRED_SKILL_SHA256",
                            {hashlib.sha256(body).hexdigest(): "dao"})
        (tmp_path / "dao").mkdir()
        (tmp_path / "dao" / "SKILL.md").write_bytes(body)
        (tmp_path / "audit").mkdir()
        (tmp_path / "audit" / "SKILL.md").write_text("---\nname: audit\n---\n我自己的\n", encoding="utf-8")
        (tmp_path / "idea").mkdir()
        removed, kept = _safe_config.remove_retired_skills(tmp_path)
        assert removed == ["dao"] and not (tmp_path / "dao").exists()
        assert (tmp_path / "audit" / "SKILL.md").is_file() and (tmp_path / "idea").is_dir()
        assert set(kept) == {"audit", "idea"}

    def test_指纹表覆盖六个退役名(self):
        assert set(_safe_config.RETIRED_SKILL_SHA256.values()) == set(_safe_config.RETIRED_SKILLS)


# ── 原子写 ─────────────────────────────────────────────────────────────
class TestAtomicWrite:
    def test_保留权限位且不留临时文件(self, tmp_path):
        f = tmp_path / "a.toml"
        f.write_text("x = 1\n", encoding="utf-8")
        os.chmod(f, 0o640)
        _safe_config.atomic_write_text(f, "x = 2\n")
        assert f.read_text(encoding="utf-8") == "x = 2\n"
        assert stat.S_IMODE(f.stat().st_mode) == 0o640
        assert [p.name for p in tmp_path.iterdir()] == ["a.toml"]

    def test_pre_push钩子只加自己那两行(self, tmp_path):
        hook = tmp_path / "pre-push"
        hook.write_text("#!/bin/sh\nrun_my_checks\nexit 0\n", encoding="utf-8")
        assert _hooks._write_hook_file(hook, "# Manon push hook", "# Manon push hook - x", "manon_run") is True
        assert hook.read_text(encoding="utf-8") == (
            "#!/bin/sh\nrun_my_checks\n# Manon push hook - x\nmanon_run\nexit 0\n")
