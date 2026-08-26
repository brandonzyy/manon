#!/usr/bin/env python3
"""coverage_targets.py —— 覆盖率读数 + 补测试目标排序（assurance 的 P5 用具）。

    python3 coverage_targets.py <项目路径> [--repo-id ID] [--top N]
                                [--include-dead] [--allow-stale] [--allow-subset]

出 JSON：{summary, denominator, targets, dead_candidates, dropped, warnings}
退出码：0 判据成立 / 2 判据不成立（下面四条任一）/ 1 用法错

**这份工具的立场是「宁可退 2，也不报一个能被当真的数」**，因为覆盖率读数出错的形态
不是崩溃，是**数字还在、但量的是别的东西**，而棘轮会把它当成事实录进 baseline。
四条硬判据，全部来自实际踩过的坑：

1. **找不到覆盖数据就退 2，不返回空结果。** 上游原版返回 `{"targets": []}` + 一句
   hint，调用方读到的是「没有待补目标」——分析失败被呈现成了「已经覆盖完了」。
2. **源码比覆盖文件新就退 2。** 陈旧读数的方向是报**高**（新写的未覆盖代码不在分母里），
   于是 baseline 被录高，真实的覆盖回退藏在虚高的余量里。
3. **分母要自证。** 覆盖工具通常只报「被测试加载过的文件」，没被任何测试 import 的文件
   连 0% 都不出现。此时百分比是在一个子集上算的，看起来很高。本工具把磁盘源文件数
   一并打出来（外部参照），比值低于 0.9 就退 2。
4. **fan-in 拿不到时是 null，不是 0。** 图谱 API 失败若静默记 0，重要目标会被降权到
   最后一名，而输出看起来完全正常。null 会让排序停下来说话。

另外两条与产品口径有关：

* **零调用方的函数默认不进目标**，单列成 `dead_candidates`。给零消费者的代码补测试
  是给它背书，还会让它从死面棘轮里消失（判例：给死函数写测试可以让它从棘轮里消失）。
  要补就显式 `--include-dead`。
* **截断必须留痕。** 任何 top-N 都在 `dropped` 里报被丢掉多少、按什么丢的。

**本工具不碰 git。** 提交由人来做，且一律 `git commit -F msg -- <显式路径>`：
共用工作树上裸 `git commit` 会把别的会话暂存的东西一起带走（判例：实测判例）。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib import request

SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", ".turbo", ".next", "coverage",
    "__pycache__", ".cache", ".venv", "venv", "vendor", ".worktrees", "web/dist",
})
SRC_EXT = {
    "lcov": {".ts", ".tsx", ".js", ".jsx"},
    "go": {".go"},
}


def die(msg, **extra):
    print(json.dumps({"error": msg, **extra}, ensure_ascii=False, indent=2))
    sys.exit(2)


def walk_sources(root: Path, exts: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix not in exts:
                continue
            if re.search(r"(_test|\.test|\.spec)\.[^.]+$", fn) or fn.startswith("test_"):
                continue
            yield p


# ── 覆盖数据定位与解析 ────────────────────────────────────────────────────────

def find_coverage(root: Path):
    """→ [(路径, 'lcov'|'go')]，按新到旧。两种格式都找，不假定语言。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS or d == "coverage"]
        dirnames[:] = [d for d in dirnames if not d.startswith(".") or d == ".dev-data"]
        for fn in filenames:
            p = Path(dirpath) / fn
            if fn == "lcov.info":
                found.append((p, "lcov"))
            elif fn.endswith((".cover", ".coverprofile")) or fn in ("coverage.out", "cover.out"):
                found.append((p, "go"))
    return sorted(found, key=lambda t: -t[0].stat().st_mtime)


def parse_lcov(path: Path, root: Path):
    files, cur = {}, None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("SF:"):
            raw = line[3:].strip()
            cand = Path(raw) if Path(raw).is_absolute() else (path.parent.parent / raw)
            try:
                cur = str(cand.resolve().relative_to(root.resolve()))
            except ValueError:
                cur = raw
            files.setdefault(cur, {"lh": 0, "lf": 0, "fnh": 0, "fnf": 0})
        elif cur is None:
            continue
        elif line.startswith("LH:"):
            files[cur]["lh"] += int(line[3:] or 0)
        elif line.startswith("LF:"):
            files[cur]["lf"] += int(line[3:] or 0)
        elif line.startswith("FNH:"):
            files[cur]["fnh"] += int(line[4:] or 0)
        elif line.startswith("FNF:"):
            files[cur]["fnf"] += int(line[4:] or 0)
        elif line.startswith("end_of_record"):
            cur = None
    return files


_GO_RE = re.compile(r"^(.*?):(\d+)\.\d+,(\d+)\.\d+ (\d+) (\d+)$")


def parse_go(path: Path, root: Path):
    """Go coverprofile：`file:l.c,l.c 语句数 命中数`。以**语句**为单位，不是行。"""
    files = {}
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not lines or not lines[0].startswith("mode:"):
        die(f"不是 Go coverprofile（首行不是 mode:）：{path}")
    modpath = _go_module(root)
    for line in lines[1:]:
        m = _GO_RE.match(line)
        if not m:
            continue
        f, _, _, nstmt, count = m.groups()
        if modpath and f.startswith(modpath + "/"):
            f = f[len(modpath) + 1:]
        e = files.setdefault(f, {"lh": 0, "lf": 0, "fnh": 0, "fnf": 0})
        e["lf"] += int(nstmt)
        if int(count) > 0:
            e["lh"] += int(nstmt)
    return files


def _go_module(root: Path):
    for gomod in list(root.glob("go.mod")) + list(root.glob("*/go.mod")):
        for line in gomod.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    return None


# ── 图谱 fan-in：拿不到就是 None ──────────────────────────────────────────────

def fan_in_lookup(names, repo_id):
    """→ (dict[name]=int|None, 说明)。整批失败返回全 None 并说明原因，绝不记 0。"""
    if not repo_id:
        return {n: None for n in names}, "未给 --repo-id，fan-in 未知（不是 0）"
    cfg_path = Path.home() / ".manon" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    base = cfg.get("api_url", "http://saas.matrixone.online:3700").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    out, failures = {}, 0
    for n in names:
        url = f"{base}/api/v1/repos/{repo_id}/graph?symbol={n}&depth=1&direction=callers"
        try:
            with request.urlopen(request.Request(url, headers=headers), timeout=15) as r:
                out[n] = len(json.loads(r.read().decode()).get("relations", []))
        except Exception:
            out[n] = None
            failures += 1
    if failures == len(names):
        return out, f"图谱 API 一条都没通（{failures}/{len(names)}），fan-in 全部未知"
    if failures:
        return out, f"图谱 API 有 {failures}/{len(names)} 条没通，那些 fan-in 是未知不是 0"
    return out, None


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("project")
    ap.add_argument("--repo-id", default=None)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--include-dead", action="store_true", help="把零调用方的函数也当目标（默认排除）")
    ap.add_argument("--allow-stale", action="store_true", help="源码比覆盖文件新时仍然出数")
    ap.add_argument("--allow-subset", action="store_true", help="分母只覆盖部分源文件时仍然出数")
    a = ap.parse_args()

    root = Path(a.project).resolve()
    if not root.is_dir():
        die(f"项目路径不存在：{root}")

    cands = find_coverage(root)
    if not cands:
        die("找不到覆盖数据（lcov.info / *.coverprofile / coverage.out）——"
            "这是判据不成立，不是「零个待补目标」",
            hint="先跑一次覆盖率：Go 用 `go test -count=1 -coverprofile=coverage.out ./...`"
                 "（-count=1 是必需的，测试缓存会把别的包旧版本的位置算进未覆盖）；"
                 "前端用 `vitest run --coverage`")
    cov_path, kind = cands[0]
    warnings = []
    if len(cands) > 1:
        warnings.append(f"找到 {len(cands)} 份覆盖数据，取最新的 {cov_path}；"
                        f"其余：{[str(p) for p, _ in cands[1:4]]}")

    # 判据 2：读数不许比源码旧
    exts = SRC_EXT[kind]
    srcs = list(walk_sources(root, exts))
    if not srcs:
        die(f"磁盘上一个 {sorted(exts)} 源文件都没有，无法给 {kind} 读数做外部参照")
    cov_mtime = cov_path.stat().st_mtime
    newer = [p for p in srcs if p.stat().st_mtime > cov_mtime]
    if newer and not a.allow_stale:
        die(f"覆盖读数比源码旧：{len(newer)} 个源文件在 {cov_path.name} 之后改过。"
            "陈旧读数的方向是报**高**，录进 baseline 会把真实回退藏起来",
            newest=[str(p.relative_to(root)) for p in sorted(newer, key=lambda p: -p.stat().st_mtime)[:5]],
            fix="重跑覆盖率，或确认后加 --allow-stale")

    files = parse_lcov(cov_path, root) if kind == "lcov" else parse_go(cov_path, root)
    if not files:
        die(f"{cov_path} 解析出 0 个文件——产出器分析失败必须报错，不能报成零发现")

    # 判据 3：分母自证（外部参照＝磁盘源文件数）
    ratio = len(files) / len(srcs)
    denominator = {"覆盖数据里的文件数": len(files), "磁盘上的源文件数": len(srcs),
                   "比值": round(ratio, 3), "外部参照": f"os.walk {sorted(exts)}"}
    if ratio < 0.9 and not a.allow_subset:
        die(f"分母只有磁盘源文件的 {ratio:.0%}——百分比是在子集上算的，会显著偏高。"
            "没被任何测试 import 的文件连 0% 都不出现",
            denominator=denominator,
            fix="让覆盖工具报全部源文件（Go 用 ./...；vitest 配 coverage.all=true），"
                "或确认后加 --allow-subset")
    if ratio < 0.9:
        warnings.append(f"🔴 分母只有磁盘源文件的 {ratio:.0%}，下面每个百分比都是子集上的读数")

    lh = sum(f["lh"] for f in files.values()); lf = sum(f["lf"] for f in files.values())
    fnh = sum(f["fnh"] for f in files.values()); fnf = sum(f["fnf"] for f in files.values())
    unit = "语句" if kind == "go" else "行"
    summary = {"格式": kind, "覆盖数据": str(cov_path.relative_to(root)), "单位": unit,
               f"{unit}_命中": lh, f"{unit}_合计": lf,
               f"{unit}_覆盖率": round(lh / max(lf, 1) * 100, 1)}
    if fnf:
        summary |= {"函数_命中": fnh, "函数_合计": fnf,
                    "函数_覆盖率": round(fnh / max(fnf, 1) * 100, 1)}

    # 目标：覆盖率低的文件，按 fan-in 排
    weak = [(rel, c["lh"] / max(c["lf"], 1) * 100) for rel, c in files.items()
            if c["lf"] and c["lh"] / c["lf"] * 100 <= 80]
    weak.sort(key=lambda t: t[1])
    considered = weak[:60]
    fan, note = fan_in_lookup([Path(r).stem for r, _ in considered], a.repo_id)
    if note:
        warnings.append(note)

    targets, dead = [], []
    for rel, pct in considered:
        fi = fan.get(Path(rel).stem)
        cov_w = 3 if pct == 0 else 2 if pct <= 50 else 1
        row = {"文件": rel, f"{unit}覆盖率": round(pct, 1), "fan_in": fi,
               "未覆盖" + unit: files[rel]["lf"] - files[rel]["lh"]}
        if fi == 0:
            row["判定"] = "零调用方 —— 先问该不该退役，不要先补测试"
            dead.append(row); continue
        row["权重"] = None if fi is None else round((3 if fi >= 5 else 2 if fi >= 2 else 1) * cov_w, 1)
        targets.append(row)
    # fan-in 未知的排在已知之后，不冒充高分也不冒充低分
    targets.sort(key=lambda r: (r["权重"] is None, -(r["权重"] or 0), -r["未覆盖" + unit]))

    dropped = {"低覆盖文件总数": len(weak), "进入排序的": len(considered),
               "因 top-N 未列出的": max(0, len(targets) - a.top),
               "排序依据": "先 fan-in×覆盖权重，再未覆盖量；fan-in 未知的整体后置"}
    if not a.include_dead and dead:
        dropped["移出目标的零调用方文件"] = len(dead)

    print(json.dumps({"summary": summary, "denominator": denominator,
                      "targets": (targets + dead if a.include_dead else targets)[:a.top],
                      "dead_candidates": dead, "dropped": dropped,
                      "warnings": warnings}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
