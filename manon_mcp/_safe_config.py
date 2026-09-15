#!/usr/bin/env python3
"""安装器写别家配置文件的三条规矩：只动 manon 自己名下的东西、读不懂就不写、写要原子。

~/.claude.json、~/.claude/settings.json、ZCode / Kimi / Codex 的配置都不是 manon 的文件，
里面是用户与别的工具的状态。判例（2026-09-15）：settings.json 读失败时被当成空表整份写回；
与 manon 同组的别人的钩子被连组删掉；~/.claude.json 被顺手塞进一条 playwright；
退役 skill 按名字 `rm -rf`，同名的用户 skill 一起没了。

只依赖标准库：install.sh / install.bat 按路径直接跑它（见 `main`）。
判据：tests/test_install_safety.py。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


class UnreadableConfig(ValueError):
    """原文件在，但读不成一个 JSON 对象——这时一个字都不写。"""


def atomic_write_text(path, text: str) -> None:
    """同目录临时文件写完、fsync、再 rename 过去：中途失败原文件不动，读者看不到半份。

    已有文件保留原权限位（settings.json 常是 600）；新文件按 umask 给 0666 & ~umask。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    else:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json_object(path) -> dict:
    """不存在 → 空对象；存在但读不成 JSON 对象 → UnreadableConfig（调用方据此不写）。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnreadableConfig(f"{path} 读不懂：{exc}") from exc
    if not isinstance(data, dict):
        raise UnreadableConfig(f"{path} 顶层不是对象")
    return data


def upsert_json_entry(path, container, name: str, entry: dict) -> bool:
    """只把 `<container…>.<name>` 设成 entry，别的键原样。内容没变返回 False 且不写。"""
    path = Path(path)
    cfg = load_json_object(path)
    node = cfg
    for key in container:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            raise UnreadableConfig(f"{path} 的 {key} 不是对象")
        node = child
    if node.get(name) == entry:
        return False
    node[name] = entry
    atomic_write_text(path, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return True


def upsert_mcp_server(path, container, base: dict, api_key: str, api_url: str) -> bool:
    """登记 manon 这一条 MCP 服务器。**只登记 manon**——别的服务器（playwright 之类）不归它管。"""
    env = {"MANON_API_KEY": api_key}
    if api_url != "auto":
        env["MANON_API_URL"] = api_url
    return upsert_json_entry(path, tuple(container), "manon", {**base, "env": env})


RETIRED_SKILLS = ("tc", "dao", "audit", "retire-checks", "experience", "idea")

#: manon 发布过的这六个 skill 的每一版 SKILL.md（sha256 → 名字），取自 git 全历史，共 22 版。
#: 它们已退役、不会再有新版，这张表是冻结的。对不上的同名目录不是 manon 装的那一版。
RETIRED_SKILL_SHA256 = {
    "33290393bdc695b97486180986c6a0e77714a0d64807d580eda2eedcf7ea5378": "audit",
    "c15da03c34845de3d40b1b67c18e98561e3d29f5ff8204c64db88174a56f1ab1": "audit",
    "ecc711745a40ed8dd8c46f25cdc094714ff292cbd0923ab6bde614061cc3c47c": "audit",
    "0ec6e64129cf38dc2a24b87cd893e066613e38163064e4094572cc2e77ebbf5f": "dao",
    "1cd0ae8a7e6256d2f0fbfb70b6c7019f48cd6eebfe49c37f29b4f5108d8414ce": "dao",
    "2edcea47f8c1e73faee707da72c90776c785704686dca00da90b090813274e68": "dao",
    "57b13d96484a470bd8b6ff7f2104b47bd8ded78e662fa332abb7c733ecbbe6e3": "dao",
    "5c1b0cea0b59a79df31f48d6e365c983d62273a9b89a13073690315dc558ffdb": "dao",
    "7c2328787436a7c4f000f27b101fb10cd2ccce2cb46aae67786cf1c2f467c24f": "dao",
    "9788679b85ef23add9e76e6a27194624d3fb9d57aa440d6d1183d2376fe586dc": "dao",
    "980cfeca2f50825519d3554fc4e14ce6c9eed22c4794f540b93843de0e28cdf5": "dao",
    "9bba59b3ae9b928259ee603ca6afc1e1d6128052c58c2cce0f9b2daedec46420": "dao",
    "9fde5aa515d09b196bb7daadaf1568c4ea0e116a017d32ba9bbaeac27de023ca": "dao",
    "bff868dd58ee7f0909dd936058187d423904f6b480e825d2d88ccd64ef11a974": "dao",
    "c6e8ba9c397e77105d94967ee2f9f9695f8fc5f447c4f36e7254d82211910731": "dao",
    "e25913bf1e2bb038b177e86d32a07541d38870914005295a05e755c055ab8e77": "dao",
    "f6a3b25dc0739b864cab64fdf8f45933bea481292d16e4195e54e0848a751cfe": "dao",
    "c1bc7e2ded317d0e08c9a1e90248b4b540950e5567d69d509231e22daca7d840": "experience",
    "0c299ce8728475d3be3ea807d2dfdda8f775fad8593f7074efd9632277259aec": "idea",
    "d8d5d7a76b1573b4cce5148bde68c258476946dd493b0d484aa96cea73819db0": "idea",
    "8bd62c9a2083ef2a52a8121e6fee02080edda1e329aff6ead6e07d283da2624d": "retire-checks",
    "1dcda2b830ef142cb35c0120c0167f71494ca516f7196f683a2d4cfa172d4505": "tc",
}


def remove_retired_skills(base) -> tuple[list, list]:
    """摘掉 base 下 manon 装过的退役 skill。**只摘 SKILL.md 指纹对得上的**——同名目录可能是
    用户自己的 skill、改过的副本或软链，那种保留。返回（摘掉的, 保留的）。"""
    base = Path(base)
    removed: list = []
    kept: list = []
    for name in RETIRED_SKILLS:
        d = base / name
        if not (d.is_dir() or d.is_symlink()):
            continue
        try:
            digest = hashlib.sha256((d / "SKILL.md").read_bytes()).hexdigest()
        except OSError:
            digest = ""
        if not d.is_symlink() and RETIRED_SKILL_SHA256.get(digest) == name:
            shutil.rmtree(d)
            removed.append(name)
        else:
            kept.append(name)
    return removed, kept


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="manon 安装器写别家配置文件的唯一出口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mcp", help="登记 manon 这一条 MCP 服务器")
    m.add_argument("target")
    m.add_argument("container", help="点分的容器路径，如 mcpServers、mcp.servers")
    m.add_argument("--api-key", default="")
    m.add_argument("--api-url", default="auto")
    m.add_argument("--type", default="")
    m.add_argument("--command", required=True)
    m.add_argument("--arg", action="append", default=[])
    r = sub.add_parser("retire-skills", help="摘掉 manon 装过的退役 skill")
    r.add_argument("base")
    a = ap.parse_args(argv)

    if a.cmd == "mcp":
        base = {**({"type": a.type} if a.type else {}), "command": a.command, "args": a.arg}
        try:
            changed = upsert_mcp_server(a.target, a.container.split("."), base, a.api_key, a.api_url)
        except UnreadableConfig as exc:
            print(f"[!] 未改动 {a.target}：{exc}", file=sys.stderr)
            return 3
        print(f"[+] {a.target}：manon 条目{'已写入' if changed else '无变化'}")
        return 0

    removed, kept = remove_retired_skills(a.base)
    for name in removed:
        print(f"[+] 已摘掉退役 skill {a.base}/{name}")
    for name in kept:
        print(f"[!] 保留 {a.base}/{name}：与 manon 发布过的任何一版都对不上"
              "（可能是你自己的同名 skill 或改过的副本）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
