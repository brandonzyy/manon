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

_CONFIG_PATTERNS = frozenset({
    "config", "settings", "options", "env", "constants", "defaults",
    "schema", "flags", "preferences", "params",
})
_EVENT_PATTERNS = frozenset({
    "event", "emitter", "listener", "subscriber", "publisher",
    "dispatch", "handler", "bus", "signal", "observable", "hook",
})
_PATTERN_KEYWORDS = frozenset({
    "factory", "strategy", "observer", "adapter", "decorator",
    "builder", "singleton", "mediator", "proxy", "facade",
    "visitor", "command", "state", "template",
})


def scan_project(project_path: str) -> dict:
    """Scan project directory: top-level modules, file sizes, dir depths, semantic signals."""
    root = Path(project_path)
    top_modules: list[str] = []
    small_files: list[dict] = []   # source files < 80 lines
    deep_dirs: list[str] = []      # dirs with path depth >= 5
    config_files: list[str] = []   # files matching config patterns (A5)
    event_files: list[str] = []    # files matching event patterns (A6)
    pattern_files: list[str] = []  # files matching design pattern names (A7)
    layer_dirs: dict[str, list[str]] = {}  # dirs organized by tech layer name (C5)

    _TECH_LAYERS = {"models", "views", "controllers", "services", "repositories",
                    "handlers", "middleware", "routes", "resolvers", "schemas",
                    "entities", "dtos", "mappers", "adapters", "validators"}

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
        dir_basename = rel_dir.name.lower() if rel_dir.parts else ""

        # C5: detect tech-layer directory names
        if dir_basename in _TECH_LAYERS:
            layer_dirs.setdefault(dir_basename, []).append(str(rel_dir).replace("\\", "/"))

        for fname in filenames:
            if Path(fname).suffix not in _SOURCE_EXT:
                continue
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root)).replace("\\", "/")
            stem = Path(fname).stem.lower().replace("-", "_")
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").count("\n")
            except OSError:
                continue
            if 0 < lines < 80:
                small_files.append({"path": rel, "lines": lines})

            # A5: config-related files (only in src dirs, skip root tool configs)
            if depth >= 1 and any(p in stem for p in _CONFIG_PATTERNS):
                # Skip test files
                if not any(t in stem for t in ("test", "spec", "mock", "fixture")):
                    config_files.append(rel)
            # A6: event-related files (only in src dirs)
            if depth >= 1 and any(p in stem for p in _EVENT_PATTERNS):
                if not any(t in stem for t in ("test", "spec", "mock", "fixture")):
                    event_files.append(rel)
            # A7: design pattern files (only in src dirs)
            if depth >= 1 and any(p in stem for p in _PATTERN_KEYWORDS):
                if not any(t in stem for t in ("test", "spec", "mock", "fixture")):
                    pattern_files.append(rel)

        if depth >= 5:
            deep_dirs.append(str(rel_dir).replace("\\", "/"))

    # Deduplicate deep_dirs (keep unique paths)
    deep_dirs = sorted(set(deep_dirs))

    return {
        "top_modules": top_modules, "small_files": small_files, "deep_dirs": deep_dirs,
        "config_files": config_files, "event_files": event_files,
        "pattern_files": pattern_files, "layer_dirs": layer_dirs,
    }

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
    """M-layer: entity-anchored deep_query calls using actual file names."""
    root = Path(project_path)
    candidates: list[dict] = []
    raw_results: list[dict] = []

    # Build file-name anchors for the largest modules
    module_files: dict[str, list[str]] = {}
    for mod in top_modules[:4]:
        mod_path = root / mod
        if not mod_path.is_dir():
            continue
        # Support both Python and TS projects
        src_files = sorted(
            f.name for f in mod_path.glob("*.*")
            if f.suffix in _SOURCE_EXT and not f.name.startswith("__") and not f.name.startswith("test")
        )[:8]
        if src_files:
            module_files[mod] = src_files

    if len(module_files) < 2:
        return candidates, raw_results

    mods = list(module_files.keys())

    questions = []
    # Q1/Q2: boundary + duplication for first pair
    mod_a, mod_b = mods[0], mods[1]
    files_a = ", ".join(module_files[mod_a])
    files_b = ", ".join(module_files[mod_b])
    questions.append(
        f"在 {mod_a}/ 和 {mod_b}/ 这两个模块中，职责边界是否清晰？"
        f"{mod_a}/ 下有 {files_a} 这些文件，{mod_b}/ 下有 {files_b}，"
        f"哪些文件的职责有重叠、可以合并？有没有某个文件只做转发调用、自身没有业务逻辑？"
    )
    questions.append(
        f"{mod_a}/ 和 {mod_b}/ 之间是否存在重复的类型定义或接口？"
        f"例如 {mod_a}/ 里是否重新定义了 {mod_b}/ 中已有的 dataclass 或 model？"
        f"这些内部副本能否通过直接导入消除？"
    )
    # Q3: M3 cross-module duplication for second pair (if available)
    if len(mods) >= 4:
        mod_c, mod_d = mods[2], mods[3]
        files_c = ", ".join(module_files[mod_c])
        files_d = ", ".join(module_files[mod_d])
        questions.append(
            f"{mod_c}/ ({files_c}) 和 {mod_d}/ ({files_d}) 之间是否存在功能重复？"
            f"例如相似的工具函数、相同逻辑的不同实现、或可以抽取为共享模块的代码？"
        )

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
            if any(kw in item_lower for kw in ["重复", "duplicate", "同样", "similar", "already", "相似"]):
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


# ── Semantic gap analysis (A5/A6/A7/M3/C5) ───────────────────────────────────

def analyze_semantic_gaps(api_base: str, repo_id: str, headers: dict,
                          structural: dict) -> tuple[list[dict], list[dict]]:
    """Graph-guided deep_query for principles that health metrics cannot quantify.

    Uses structural signals (file names, dir names) as anchors for targeted queries.
    Only asks questions when structural signals suggest a potential issue.
    """
    candidates: list[dict] = []
    raw_results: list[dict] = []

    def _ask(question: str) -> dict | None:
        try:
            url = f"{api_base}/api/v1/repos/{repo_id}/deep-query"
            return api_post(url, {"question": question, "max_rounds": 2}, headers)
        except Exception as e:
            raw_results.append({"question": question, "error": str(e)})
            return None

    # ── A5: Config complexity ─────────────────────────────────────────────
    config_files = structural.get("config_files", [])
    if len(config_files) >= 5:
        sample = ", ".join(Path(f).name for f in config_files[:8])
        resp = _ask(
            f"项目中有 {len(config_files)} 个配置相关文件（{sample}），"
            f"这些配置是否存在过度分散或重复定义的问题？"
            f"是否有配置文件可以合并？是否有运行时配置和编译时配置混在一起的情况？"
        )
        if resp:
            raw_results.append({"question": "A5:config", "sub_questions": resp.get("sub_questions", [])})
            candidates.append({
                "layer": "A", "code": "A5",
                "description": f"发现 {len(config_files)} 个配置相关文件（{sample}），可能存在配置复杂度问题",
                "source": "structural+deep_query", "confidence": "medium",
            })

    # ── A6: Event system overkill ─────────────────────────────────────────
    event_files = structural.get("event_files", [])
    if len(event_files) >= 4:
        sample = ", ".join(Path(f).name for f in event_files[:8])
        resp = _ask(
            f"项目中有 {len(event_files)} 个事件/消息相关文件（{sample}），"
            f"这些事件系统是否过度复杂？是否存在简单的函数调用被包装成事件发布/订阅的情况？"
            f"哪些事件可以用直接调用替代？"
        )
        if resp:
            raw_results.append({"question": "A6:events", "sub_questions": resp.get("sub_questions", [])})
            for item in resp.get("sub_questions", [])[:3]:
                if isinstance(item, str) and any(kw in item.lower() for kw in
                    ["直接调用", "direct call", "不需要", "unnecessary", "简化", "simplif", "过度"]):
                    candidates.append({
                        "layer": "A", "code": "A6",
                        "description": f"deep_query 发现事件系统可能过度：{item}",
                        "source": "deep_query", "confidence": "medium",
                    })
        if not any(c["code"] == "A6" for c in candidates) and len(event_files) >= 4:
            candidates.append({
                "layer": "A", "code": "A6",
                "description": f"发现 {len(event_files)} 个事件系统文件（{sample}），需确认是否有过度事件化",
                "source": "structural", "confidence": "medium",
            })

    # ── A7: Over-patterning ───────────────────────────────────────────────
    pattern_files = structural.get("pattern_files", [])
    if len(pattern_files) >= 3:
        sample = ", ".join(Path(f).name for f in pattern_files[:8])
        resp = _ask(
            f"项目中有 {len(pattern_files)} 个文件名包含设计模式关键词（{sample}），"
            f"这些设计模式的使用是否必要？是否存在只有一个具体实现的 Factory 或 Strategy？"
            f"哪些模式可以用更简单的实现替代？"
        )
        if resp:
            raw_results.append({"question": "A7:patterns", "sub_questions": resp.get("sub_questions", [])})
            for item in resp.get("sub_questions", [])[:3]:
                if isinstance(item, str) and any(kw in item.lower() for kw in
                    ["一个实现", "single", "only one", "不需要", "unnecessary", "简单", "simple"]):
                    candidates.append({
                        "layer": "A", "code": "A7",
                        "description": f"deep_query 发现可能过度的设计模式：{item}",
                        "source": "deep_query", "confidence": "medium",
                    })
        if not any(c["code"] == "A7" for c in candidates) and len(pattern_files) >= 3:
            candidates.append({
                "layer": "A", "code": "A7",
                "description": f"发现 {len(pattern_files)} 个设计模式文件（{sample}），需确认模式使用是否必要",
                "source": "structural", "confidence": "medium",
            })

    # ── C5: Split by tech layer ───────────────────────────────────────────
    layer_dirs = structural.get("layer_dirs", {})
    if len(layer_dirs) >= 3:
        layers_str = ", ".join(f"{k}/ ({len(v)}处)" for k, v in sorted(layer_dirs.items())[:6])
        candidates.append({
            "layer": "C", "code": "C5",
            "description": f"目录按技术层组织而非按功能组织：{layers_str}。相关功能的代码分散在不同技术层目录中，修改一个功能需要跨多个目录",
            "source": "structural", "confidence": "medium",
        })

    return candidates, raw_results

# ── Unified deep_query confirmation ───────────────────────────────────────────

# Candidates already confirmed via deep_query in their analysis phase
_ALREADY_CONFIRMED_SOURCES = frozenset({"deep_query", "structural+deep_query"})

# Principle-specific confirmation questions — anchored to the candidate description
_CONFIRM_TEMPLATES = {
    "A1": "以下模块被怀疑是不必要的间接层：{desc}。请确认它是否真的只做转发？是否有无法通过直接导入替代的理由（如循环依赖避免、延迟加载）？",
    "A2": "{desc}。这种模块划分是否有合理的边界依据？还是可以合并相关的微型模块？",
    "A3": "{desc}。这些抽象是否确实只有一个实现？是否有扩展的规划？",
    "A4": "{desc}。这些跨模块调用是否由业务需求驱动？是否有不必要的中间接口层？",
    "M1": "{desc}。这个模块的职责是否可以拆分？哪些功能可以独立到子模块？",
    "M3": "{desc}。这些相似的实现是否可以抽取为共享函数？差异点在哪里？",
    "C1": "{desc}。这些 barrel/re-export 文件是否可以让消费方直接导入源模块？",
    "C2": "{desc}。这些小文件之间是否有内聚关系？合并后的文件是否仍然职责清晰？",
    "C4": "{desc}。这些零调用者实体是否为框架入口点、CLI 命令、或测试辅助？还是确实可以安全删除？",
    "C6": "{desc}。移除这些抽象后，调用方代码是否会变得更简单？",
    "C7": "{desc}。这个循环依赖的双方是否应该合并为一个模块？还是需要引入接口层打破循环？",
    "C8": "{desc}。这些大函数可以按什么维度拆分？拆分后各子函数的职责是什么？",
}


def confirm_candidates(
    candidates: list[dict],
    api_base: str, repo_id: str, headers: dict,
) -> tuple[list[dict], list[dict]]:
    """Confirm unconfirmed candidates via deep_query. Return (confirmed, raw_results).

    Strategy:
    - Already-confirmed (source=deep_query): pass through directly
    - High-confidence (health score triggered): pass through, but batch-confirm up to 3
    - Medium-confidence: must pass deep_query confirmation to survive
    - Batch by principle code to minimize API calls (max 4 queries total)
    """
    confirmed: list[dict] = []
    raw_results: list[dict] = []

    # Split into pass-through vs needs-confirmation
    needs_confirm: list[dict] = []
    for c in candidates:
        if c.get("source") in _ALREADY_CONFIRMED_SOURCES:
            confirmed.append(c)
        elif c.get("confidence") == "high":
            # High-confidence from health metrics: pass through but still try to confirm
            confirmed.append(c)
        else:
            needs_confirm.append(c)

    if not needs_confirm:
        return confirmed, raw_results

    # Group by principle code, take first per group (max 4 groups to limit API calls)
    groups: dict[str, list[dict]] = {}
    for c in needs_confirm:
        groups.setdefault(c["code"], []).append(c)

    query_count = 0
    for code, group in list(groups.items()):
        if query_count >= 4:
            # Budget exhausted: pass remaining as unconfirmed
            for c in group:
                c["confidence"] = "unconfirmed"
                confirmed.append(c)
            continue

        template = _CONFIRM_TEMPLATES.get(code)
        if not template:
            # No template: pass through as-is
            confirmed.extend(group)
            continue

        # Use first candidate's description as anchor
        desc = group[0]["description"]
        question = template.format(desc=desc)

        try:
            url = f"{api_base}/api/v1/repos/{repo_id}/deep-query"
            resp = api_post(url, {"question": question, "max_rounds": 2}, headers)
            query_count += 1
        except Exception as e:
            raw_results.append({"question": question, "error": str(e)})
            # On failure: keep candidates but mark unconfirmed
            for c in group:
                c["confidence"] = "unconfirmed"
                confirmed.append(c)
            continue

        sub_qs = resp.get("sub_questions", [])
        raw_results.append({
            "question": question, "code": code,
            "sub_questions": sub_qs,
            "rounds": len(resp.get("rounds", [])),
        })

        # Check if deep_query found the issue to be real
        answer_text = " ".join(str(s) for s in sub_qs).lower()
        reject_signals = ["合理", "必要", "intentional", "by design", "不建议",
                          "没有问题", "no issue", "正确", "appropriate"]
        confirm_signals = ["可以", "建议", "应该", "确实", "indeed", "redundant",
                           "不必要", "unnecessary", "合并", "移除", "remove", "simplif"]

        reject_score = sum(1 for kw in reject_signals if kw in answer_text)
        confirm_score = sum(1 for kw in confirm_signals if kw in answer_text)

        if confirm_score > reject_score:
            # Confirmed: upgrade confidence
            for c in group:
                c["confidence"] = "confirmed"
                # Enrich description with deep_query insight if available
                if sub_qs and isinstance(sub_qs[0], str):
                    c["description"] += f"（deep_query 确认：{sub_qs[0][:80]}）"
                confirmed.append(c)
        elif reject_score > confirm_score:
            # Rejected: drop these candidates
            pass
        else:
            # Ambiguous: keep but mark
            for c in group:
                c["confidence"] = "unconfirmed"
                confirmed.append(c)

    return confirmed, raw_results


# ── Code layer — structural analysis ─────────────────────────────────────────

def analyze_code_structural(project_path: str, structural: dict,
                              health_dims: dict) -> list[dict]:
    """C-layer: purely structural checks — no API calls needed."""
    candidates: list[dict] = []

    # C4: dead code — DC metric
    dc = health_dims.get("DC", {})
    if dc.get("value", 10) < 10:
        dc_detail = dc.get("detail", dc)
        dead = int(dc_detail.get("dead_count", 0))
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
        fs_detail = fs.get("detail", fs)
        oversized = int(fs_detail.get("oversized", 0))
        total_fn = int(fs_detail.get("total", 0))
        candidates.append({
            "layer": "C", "code": "C8",
            "description": f"FS={fs['value']:.1f}: {oversized}/{total_fn} 个函数超过 50 行，可能存在低内聚问题",
            "source": "health", "confidence": "medium",
        })

    # MF: module fragmentation — tiny modules + deep paths
    mf = health_dims.get("MF", {})
    if mf.get("value", 10) < 9:
        detail = mf.get("detail", mf)
        tiny = int(detail.get("tiny_modules", 0))
        total = int(detail.get("total_modules", 0))
        deep = int(detail.get("deep_files", 0))
        if tiny > 0:
            candidates.append({
                "layer": "C", "code": "C2",
                "description": f"MF={mf['value']:.1f}: {tiny}/{total} 个模块实体数 <3，碎片化严重，考虑合并相关小文件",
                "source": "health", "confidence": "high",
            })
        if deep > 0:
            candidates.append({
                "layer": "C", "code": "C3",
                "description": f"MF: {deep} 个文件路径深度 ≥5，目录嵌套过深",
                "source": "health", "confidence": "medium",
            })
        if tiny > total * 0.3:
            candidates.append({
                "layer": "A", "code": "A2",
                "description": f"MF={mf['value']:.1f}: 超过 30% 的模块为微型模块（{tiny}/{total}），项目可能过度模块化",
                "source": "health", "confidence": "medium",
            })

    # RE: re-export / indirection — barrel modules + single-impl interfaces
    re_dim = health_dims.get("RE", {})
    if re_dim.get("value", 10) < 9:
        detail = re_dim.get("detail", re_dim)
        barrels = int(detail.get("barrel_modules", 0))
        single = int(detail.get("single_impl_interfaces", 0))
        if barrels > 0:
            candidates.append({
                "layer": "C", "code": "C1",
                "description": f"RE={re_dim['value']:.1f}: {barrels} 个模块只做 re-export 无自身逻辑，考虑移除间接层",
                "source": "health", "confidence": "high",
            })
        if single > 0:
            total_iface = int(detail.get("total_interfaces", 0))
            candidates.append({
                "layer": "C", "code": "C6",
                "description": f"RE: {single}/{total_iface} 个接口/类型别名只有 ≤1 个实现，可能是不必要的抽象",
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

    # 6. Semantic gap analysis (A5/A6/A7/C5 — graph-guided deep_query)
    gap_candidates, gap_raw = analyze_semantic_gaps(
        api_base, repo_id, headers, structural
    )
    c_candidates.extend(gap_candidates)
    deep_query_raw.extend(gap_raw)

    # 7. Unified deep_query confirmation for all unconfirmed candidates
    all_raw = a_candidates + m_candidates + c_candidates
    confirmed, confirm_raw = confirm_candidates(all_raw, api_base, repo_id, headers)
    deep_query_raw.extend(confirm_raw)

    # Deduplicate candidates by (layer, code, source)
    seen: set[tuple] = set()
    candidates: list[dict] = []
    for c in confirmed:
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
