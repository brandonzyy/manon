#!/usr/bin/env python3
"""Gather codebase context for /idea skill.

Usage: idea-ctx.py <repo_id> <query>

Outputs JSON:
  {
    "modules": [...],         # related modules from search
    "graph": [...],           # call relationships
    "health": {...},          # code health summary
    "research": [...]         # GitHub search results (if gh available)
  }
"""
import json
import subprocess
import sys
from pathlib import Path
from urllib import request, error as url_error


def load_config() -> dict:
    cfg_path = Path.home() / ".manon" / "config.json"
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def api_get(url: str, headers: dict, timeout: int = 15) -> dict:
    req = request.Request(url, headers=headers, method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def api_post(url: str, headers: dict, body: dict, timeout: int = 15) -> dict:
    data = json.dumps(body).encode()
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def search(api_url: str, repo_id: str, headers: dict, query: str) -> list:
    url = f"{api_url}/api/v1/repos/{repo_id}/search?q={request.quote(query)}&top_k=15&depth=1"
    try:
        result = api_get(url, headers)
        entities = result.get("entities", [])
        return [{"name": e.get("name", ""), "type": e.get("type", ""),
                 "file": e.get("file_path", ""), "score": e.get("score", 0)}
                for e in entities[:15]]
    except Exception:
        return []


def graph_symbols(api_url: str, repo_id: str, headers: dict, symbols: list[str]) -> list:
    results = []
    for sym in symbols[:5]:
        url = f"{api_url}/api/v1/repos/{repo_id}/graph?symbol={request.quote(sym)}&direction=both&depth=1"
        try:
            data = api_get(url, headers, timeout=10)
            relations = data.get("relations", [])
            callers = list({r["src_id"] for r in relations if r.get("kind") == "calls" and r.get("tgt_id", "").endswith(sym)})[:5]
            callees = list({r["tgt_id"] for r in relations if r.get("kind") == "calls" and r.get("src_id", "").endswith(sym)})[:5]
            imports = list({r["tgt_id"] for r in relations if r.get("kind") == "imports" and r.get("src_id", "").endswith(sym)})[:5]
            if callers or callees or imports:
                entry = {"symbol": sym}
                if callers: entry["callers"] = callers
                if callees: entry["callees"] = callees
                if imports: entry["imports"] = imports
                results.append(entry)
        except Exception:
            continue
    return results


def health(api_url: str, repo_id: str, headers: dict) -> dict:
    url = f"{api_url}/api/v1/repos/{repo_id}/code-health"
    try:
        result = api_get(url, headers)
        dims = result.get("dimensions", [])
        return {
            "score": result.get("score", 0),
            "grade": result.get("grade", "?"),
            "weak": [{"name": d["name"], "abbr": d["abbr"], "value": d["value"]}
                     for d in dims if d.get("value", 10) < 9],
        }
    except Exception:
        return {}


def gh_research(query: str) -> list:
    """Search GitHub for similar implementations."""
    # Extract key technical terms for better search
    search_terms = query.replace("给", "").replace("添加", "").replace("功能", "")
    for cmd in [
        ["gh", "search", "repos", search_terms, "--limit", "5",
         "--sort", "stars", "--json", "fullName,description,stargazersCount,url"],
        ["gh", "search", "repos", query, "--limit", "5",
         "--sort", "stars", "--json", "fullName,description,stargazersCount,url"],
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                repos = json.loads(result.stdout)
                filtered = [{"name": r.get("fullName", ""), "desc": r.get("description", ""),
                             "stars": r.get("stargazersCount", 0), "url": r.get("url", "")}
                            for r in repos if r.get("stargazersCount", 0) > 10]
                if filtered:
                    return filtered
        except Exception:
            continue
    return []


def main():
    if len(sys.argv) < 3:
        print("Usage: idea-ctx.py <repo_id> <query>", file=sys.stderr)
        sys.exit(1)

    repo_id, query = sys.argv[1], " ".join(sys.argv[2:])
    cfg = load_config()
    api_url = cfg.get("api_url", "http://saas.matrixone.online:3700").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    # 1. Search related modules
    modules = search(api_url, repo_id, headers, query)

    # 2. Graph traversal for function/class level symbols (skip module-level)
    top_symbols = [m["name"] for m in modules
                   if m.get("name") and "." in m["name"]
                   and m["name"] != m.get("file", "").replace("/", ".").replace(".py", "")]
    graph = graph_symbols(api_url, repo_id, headers, top_symbols)

    # 3. Health summary
    h = health(api_url, repo_id, headers)

    # 4. GitHub research
    research = gh_research(query)

    output = {"modules": modules, "graph": graph, "health": h, "research": research}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
