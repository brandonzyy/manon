#!/usr/bin/env python3
"""dao-analyze.py — programmatic three-layer analysis using Manon APIs.

Replaces the complex prompt-based inquiry strategy in SKILL.md.
Each layer uses the right tool instead of routing everything through deep_query.

Usage:
    python dao-analyze.py <project_path> <repo_id>

Output JSON:
    {
      "candidates": [
        {"layer": "A", "code": "A2", "description": "...", "source": "graph|health|structural|deep_query", "confidence": "high|medium"}
      ],
      "deep_query_raw": [{"question": "...", "sub_questions": [...], "covered": [...]}],
      "structural": {"top_modules": [...], "small_files": [...], "deep_dirs": [...]}
    }
"""
import json
import os
import sys
from pathlib import Path
from urllib import error as url_error
from urllib import request

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg_path = Path.home() / ".manon" / "config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

def api_headers(cfg: dict) -> dict:
    h = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        h["Authorization"] = f"Bearer {cfg['api_key']}"
    return h

def api_get(url: str, headers: dict) -> dict:
    req = request.Request(url, headers=headers, method="GET")
    with request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def api_post(url: str, body: dict, headers: dict) -> dict:
    data = json.dumps(body).encode()
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

# ── Project structure ─────────────────────────────────────────────────────────

_SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rs", ".rb", ".php"}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
              ".dao", ".codeindex", "htmlcov", ".tox", ".mypy_cache",
              "tests", "test", "__tests__", "fixtures", "examples", "docs"}
_SKIP_TOP = {"tests", "test", "scripts", "tools", "docs", "examples", "fixtures", "bin"}

def scan_project(project_path: str) -> dict:
    """Scan project directory: top-level modules, file sizes, dir depths."""
    root = Path(project_path)
    top_modules: list[str] = []
    small_files: list[dict] = []   # source files < 80 lines
    deep_dirs: list[str] = []      # dirs with path depth >= 5

    # Top-level modules: dirs with source files or __init__.py / package.json
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") or item.name in _SKIP_DIRS or item.name in _SKIP_TOP:
            continue
        if item.is_dir():
            has_init = (item / "__init__.py").exists() or (item / "package.json").exists()
            has_src = any(item.rglob("*.py")) or any(item.rglob("*.ts"))
            if has_init or has_src:
                top_modules.append(item.name)

    # Walk all source files
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts)

        for fname in filenames:
            if Path(fname).suffix not in _SOURCE_EXT:
                continue
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root)).replace("\\", "/")
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").count("\n")
            except OSError:
                continue
            if 0 < lines < 80:
                small_files.append({"path": rel, "lines": lines})
        if depth >= 5:
            deep_dirs.append(str(rel_dir).replace("\\", "/"))

    # Deduplicate deep_dirs (keep unique paths)
    deep_dirs = sorted(set(deep_dirs))

    return {"top_modules": top_modules, "small_files": small_files, "deep_dirs": deep_dirs}

# ── Architecture layer — graph traversal ─────────────────────────────────────

def analyze_architecture(api_base: str, repo_id: str, headers: dict,
                          top_modules: list[str], health_dims: dict,
                          project_path: str = "") -> list[dict]:
    """A-layer: use graph API to detect thin layers and hub modules."""
    candidates = []

    # A: FI/MC driven — if a module is called by many others, check if it's a hub
    mc = health_dims.get("MC", {})
    fi = health_dims.get("FI", {})
    if fi.get("value", 10) < 9:
        candidates.append({
            "layer": "A", "code": "A1",
            "description": f"FI={fi['value']:.1f} < 9: 存在高扇入模块，某个模块被过多其他模块依赖，可能承担了过多职责或是隐式的中心节点",
            "source": "health", "confidence": "high",
        })
    if mc.get("value", 10) < 9:
        candidates.append({
            "layer": "A", "code": "A4",
            "description": f"MC={mc['value']:.1f} < 9: 跨模块调用比例偏高，模块间耦合超出理想水平，需检查是否存在不必要的跨层依赖",
            "source": "health", "confidence": "medium",
        })

    # Graph traversal: check each top-level module's cross-module edges
    for mod in top_modules[:6]:  # limit to avoid too many API calls
        try:
            url = f"{api_base}/api/v1/repos/{repo_id}/graph?symbol={mod}&depth=1&direction=both"
            result = api_get(url, headers)
        except Exception:
            continue

        relations = result.get("relations", [])
        import_out = [r for r in relations if r.get("kind") == "imports" and r.get("src_id", "").startswith(mod + ".")]
        import_in  = [r for r in relations if r.get("kind") == "imports" and r.get("tgt_id", "").startswith(mod + ".")]

        # Thin intermediary: module only re-exports — but skip top-level facade __init__.py
        # (a module with sub-packages is expected to re-export; flag only leaf modules)
        calls = [r for r in relations if r.get("kind") == "calls"]
        root_check = Path(project_path) / mod
        has_subpackages = any(
            (root_check / d).is_dir() and (root_check / d / "__init__.py").exists()
            for d in os.listdir(root_check) if (root_check / d).is_dir()
        ) if root_check.is_dir() else False
        if len(import_out) >= 5 and len(calls) == 0 and not has_subpackages:
            candidates.append({
                "layer": "A", "code": "A1",
                "description": f"{mod}/ 模块只做导入转发（{len(import_out)} 个 import，0 个 call），可能是不必要的间接层",
                "source": "graph", "confidence": "medium",
            })

        # Hub: imported by many different modules → M1/A1
        external_importers = {r["src_id"].split(".")[0] for r in import_in
                               if not r.get("src_id", "").startswith(mod)}
        if len(external_importers) >= 4:
            candidates.append({
                "layer": "M", "code": "M1",
                "description": f"{mod}/ 被 {len(external_importers)} 个其他模块依赖（{', '.join(sorted(external_importers)[:4])}...），需确认职责边界是否清晰",
                "source": "graph", "confidence": "medium",
            })

    return candidates

# ── Module layer — entity-anchored deep_query ─────────────────────────────────

def analyze_modules(api_base: str, repo_id: str, headers: dict,
                    project_path: str, top_modules: list[str]) -> tuple[list[dict], list[dict]]:
    """M-layer: 2 entity-anchored deep_query calls using actual file names."""
    root = Path(project_path)
    candidates: list[dict] = []
    raw_results: list[dict] = []

    # Build file-name anchors for the 2 largest modules
    module_files: dict[str, list[str]] = {}
    for mod in top_modules[:4]:
        mod_path = root / mod
        if not mod_path.is_dir():
            continue
        files = sorted(f.name for f in mod_path.glob("*.py") if not f.name.startswith("__"))[:8]
        if files:
            module_files[mod] = files

    if len(module_files) < 2:
        return candidates, raw_results

    mods = list(module_files.keys())
    mod_a, mod_b = mods[0], mods[1]
    files_a = ", ".join(module_files[mod_a])
    files_b = ", ".join(module_files[mod_b])

    questions = [
        # Q1: module boundary + consolidation
        (
            f"在 {mod_a}/ 和 {mod_b}/ 这两个模块中，职责边界是否清晰？"
            f"{mod_a}/ 下有 {files_a} 这些文件，{mod_b}/ 下有 {files_b}，"
            f"哪些文件的职责有重叠、可以合并？有没有某个文件只做转发调用、自身没有业务逻辑？"
        ),
        # Q2: cross-boundary type duplication
        (
            f"{mod_a}/ 和 {mod_b}/ 之间是否存在重复的类型定义或接口？"
            f"例如 {mod_a}/ 里是否重新定义了 {mod_b}/ 中已有的 dataclass 或 model？"
            f"这些内部副本能否通过直接导入消除？"
        ),
    ]

    for q in questions:
        try:
            url = f"{api_base}/api/v1/repos/{repo_id}/deep-query"
            resp = api_post(url, {"question": q, "max_rounds": 3}, headers)
        except Exception as e:
            raw_results.append({"question": q, "error": str(e)})
            continue

        sub_qs = resp.get("sub_questions", [])
        covered = resp.get("covered", [])
        raw_results.append({"question": q, "sub_questions": sub_qs, "covered": covered,
                             "rounds": len(resp.get("rounds", []))})

        # Convert deep_query findings into candidates
        for item in sub_qs[:4]:
            if not isinstance(item, str):
                continue
            item_lower = item.lower()
            if any(kw in item_lower for kw in ["重复", "duplicate", "同样", "similar", "already"]):
                candidates.append({
                    "layer": "M", "code": "M3",
                    "description": f"deep_query 发现潜在重复：{item}",
                    "source": "deep_query", "confidence": "medium",
                })
            elif any(kw in item_lower for kw in ["转发", "forward", "re-export", "只有", "thin", "barrel"]):
                candidates.append({
                    "layer": "C", "code": "C1",
                    "description": f"deep_query 发现潜在转发层：{item}",
                    "source": "deep_query", "confidence": "medium",
                })
            elif any(kw in item_lower for kw in ["合并", "merge", "consolidat", "too small", "碎片"]):
                candidates.append({
                    "layer": "C", "code": "C2",
                    "description": f"deep_query 发现可合并文件：{item}",
                    "source": "deep_query", "confidence": "medium",
                })

    return candidates, raw_results

# ── Code layer — structural analysis ─────────────────────────────────────────

def analyze_code_structural(project_path: str, structural: dict,
                              health_dims: dict) -> list[dict]:
    """C-layer: purely structural checks — no API calls needed."""
    candidates: list[dict] = []

    # C4: dead code — DC metric
    dc = health_dims.get("DC", {})
    if dc.get("value", 10) < 10:
        dead = int(dc.get("dead_count", 0))
        candidates.append({
            "layer": "C", "code": "C4",
            "description": f"DC={dc['value']:.1f}: 图中有 {dead} 个零调用者实体，需用 manon_graph(kind=callers) 逐一确认是否可删除",
            "source": "health", "confidence": "high",
        })

    # C2: over-fragmentation — directories with many small files
    root = Path(project_path)
    dir_files: dict[str, list[str]] = {}
    for item in structural["small_files"]:
        d = str(Path(item["path"]).parent)
        dir_files.setdefault(d, []).append(item["path"])
    for d, files in dir_files.items():
        if len(files) >= 4:
            candidates.append({
                "layer": "C", "code": "C2",
                "description": f"{d}/ 下有 {len(files)} 个小文件（<80行），考虑合并：{', '.join(Path(f).name for f in files[:5])}",
                "source": "structural", "confidence": "high",
            })

    # C3: deep directory nesting
    if structural["deep_dirs"]:
        candidates.append({
            "layer": "C", "code": "C3",
            "description": f"目录深度 ≥5 的路径 {len(structural['deep_dirs'])} 处：{', '.join(structural['deep_dirs'][:4])}",
            "source": "structural", "confidence": "medium",
        })

    # C7: circular deps — CD metric
    cd = health_dims.get("CD", {})
    if cd.get("value", 10) < 10:
        candidates.append({
            "layer": "C", "code": "C7",
            "description": f"CD={cd['value']:.1f}: 存在循环依赖（cycles={cd.get('cycles', '?')}），需用 manon_code_health 定位具体环路",
            "source": "health", "confidence": "high",
        })

    # FS: oversized functions
    fs = health_dims.get("FS", {})
    if fs.get("value", 10) < 9:
        oversized = int(fs.get("oversized", 0))
        candidates.append({
            "layer": "C", "code": "C8",
            "description": f"FS={fs['value']:.1f}: {oversized} 个函数超过规模阈值，可能存在低内聚问题",
            "source": "health", "confidence": "medium",
        })

    return candidates

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    project_path, repo_id = sys.argv[1], sys.argv[2]
    cfg = load_config()
    api_base = cfg.get("api_url", "http://saas.matrixone.online:3700").rstrip("/")
    headers = api_headers(cfg)

    # 1. Project structure (no API)
    structural = scan_project(project_path)

    # 2. Health data
    try:
        health_raw = api_get(f"{api_base}/api/v1/repos/{repo_id}/code-health", headers)
        dims_list = health_raw.get("dimensions", [])
        health_dims = {d["abbr"]: d for d in dims_list}
    except Exception as e:
        print(json.dumps({"error": f"health API failed: {e}"}))
        sys.exit(1)

    # 3. Architecture analysis (health + graph)
    a_candidates = analyze_architecture(api_base, repo_id, headers,
                                         structural["top_modules"], health_dims,
                                         project_path)

    # 4. Module analysis (deep_query with anchors)
    m_candidates, deep_query_raw = analyze_modules(
        api_base, repo_id, headers, project_path, structural["top_modules"]
    )

    # 5. Code structural analysis (no API)
    c_candidates = analyze_code_structural(project_path, structural, health_dims)

    # Deduplicate candidates by (layer, code, source)
    seen: set[tuple] = set()
    candidates: list[dict] = []
    for c in a_candidates + m_candidates + c_candidates:
        key = (c["layer"], c["code"], c["source"])
        if key not in seen:
            seen.add(key)
            candidates.append(c)

    print(json.dumps({
        "candidates": candidates,
        "deep_query_raw": deep_query_raw,
        "structural": structural,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
