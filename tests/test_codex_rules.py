"""Manon 调用规则只此一份（manon_mcp/manon_rules.md），三个写入口都经 `_install_codex_agents_md`
写进 Codex 的全局指令文件 ~/.codex/AGENTS.md。

判例（2026-09-24）：install.sh、install.bat、_hooks.py 各内联一份模板，三份口径不同
（中文长版 / 中文短版 / 英文短版），且都写到 ~/AGENTS.md——Codex 在仓里干活时不读那个文件。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from manon_mcp import _hooks

ROOT = Path(__file__).resolve().parent.parent


class TestInstallCodexAgentsMd:
    def test_新建即写入正本(self, tmp_path):
        f = tmp_path / ".codex" / "AGENTS.md"
        f.parent.mkdir()
        _hooks._install_codex_agents_md(f)
        assert f.read_text(encoding="utf-8") == _hooks.MANON_RULES.read_text(encoding="utf-8")

    def test_追加在别人的内容之后(self, tmp_path):
        f = tmp_path / "AGENTS.md"
        f.write_text("# 用户自己的规则\n", encoding="utf-8")
        _hooks._install_codex_agents_md(f)
        text = f.read_text(encoding="utf-8")
        assert text.startswith("# 用户自己的规则\n\n")
        assert text.endswith(_hooks.MANON_RULES.read_text(encoding="utf-8"))

    def test_已有规则不动_重复安装幂等(self, tmp_path):
        f = tmp_path / "AGENTS.md"
        f.write_text("自己写的 manon_search 用法\n", encoding="utf-8")
        _hooks._install_codex_agents_md(f)
        assert f.read_text(encoding="utf-8") == "自己写的 manon_search 用法\n"
        g = tmp_path / "new.md"
        _hooks._install_codex_agents_md(g)
        once = g.read_text(encoding="utf-8")
        _hooks._install_codex_agents_md(g)
        assert g.read_text(encoding="utf-8") == once

    def test_正本自己带判重标记(self):
        # 判重看 manon_search；正本里没有它，每次安装都会再追加一份
        assert "manon_search" in _hooks.MANON_RULES.read_text(encoding="utf-8")


class TestSingleSource:
    def test_MCP_init_写到_codex_目录(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(_hooks, "_config", SimpleNamespace(API_KEY="k"))
        (tmp_path / ".codex").mkdir()
        assert _hooks._install_codex_config() == "Codex CLI configured"
        assert (tmp_path / ".codex" / "AGENTS.md").is_file()
        assert not (tmp_path / "AGENTS.md").exists()

    def test_安装器不内联规则_只调同一个函数(self):
        rules_lines = [ln for ln in _hooks.MANON_RULES.read_text(encoding="utf-8").splitlines()
                       if ln.startswith("## ")]
        for name in ("install.sh", "install.bat"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "_install_codex_agents_md" in text, name
            assert ".codex" in text and "AGENTS.md" in text, name
            assert "知识图谱规则" not in text, name
            assert not any(h in text for h in rules_lines), name
            assert '$HOME/AGENTS.md"' not in text and '$HOME_DIR\\AGENTS.md"' not in text, name
