<div align="center">

# Manon

### AI Architect for Your Codebase

**Knowledge graph engine + development skills — from requirements to production, grounded in code facts.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1)](https://modelcontextprotocol.io)
[![License: BSL-1.1](https://img.shields.io/badge/license-BSL--1.1-orange)](LICENSE)

[Quick Start](#-quick-start) · [Skill System](#-skill-system) · [Knowledge Graph](#-knowledge-graph) · [Query Tools](#-query-tools) · [MCP Tools](#-mcp-tools)

[中文文档](README_CN.md)

</div>

---

## ❓ The Problem

AI coding has two structural flaws:

| Flaw | Symptom | Consequence |
|------|---------|-------------|
| **Insufficient context** | Model can't see call graphs, dependency chains, module boundaries | **Hallucination** — guesses relationships, misses side effects |
| **Unstructured workflow** | Model dives straight into code without requirements, testing, or validation | **Drift** — scope creep, untested code, silent regressions |

These flaws compound into 19 specific failure modes across three layers:

| Layer | What goes wrong | Examples |
|-------|----------------|---------|
| **Architecture** | Structural decisions made without seeing the full system | Unnecessary abstraction layers, over-modularization, premature generalization, config/event system overkill |
| **Module** | Boundaries drawn without understanding dependencies | Feature bloat in single modules, unclear ownership, cross-module duplication, excessive coupling |
| **Code** | Line-level changes without knowing who calls what | Dead code left behind, circular dependencies introduced, functions split too small, low cohesion |

For AI coding specifically, these problems are worse than for human developers:
- **AI can't "feel" architectural intent** — it optimizes locally, creating technically correct code that violates system-level design decisions
- **AI generates faster than it validates** — without graph-backed verification, bad patterns propagate at machine speed
- **Each AI session starts blind** — no memory of past decisions, so the same structural mistakes get re-introduced across conversations

The stronger the model, the worse both problems get — powerful model + bad context + no process = confident garbage, faster.

## 💡 The Solution

Manon provides two layers:

**Layer 1 — Knowledge Graph** (the foundation)
Indexes every function, class, call relationship, import chain, and module boundary. Vector + graph hybrid search. When the model needs context, it gets precisely the relevant code — not too much, not too little.

**Layer 2 — Development Skills** (the workflow)
Two skills. `/manon` activates the knowledge graph for your project. `/assurance` is the single entry to the engineering assurance system: it scores the project by measurement first, then routes to one of its loops — gap-filling the tool stack, coverage, behaviour-layer audit, structural simplification, retiring stale gates. Every loop is backed by the graph or deterministic scripts, so decisions are grounded in code facts, not LLM imagination.

```
  /manon (activate)                  /assurance (assure)
  ┌─────┐                          ┌──────────┐
  │Index│──▶ graph-backed coding ─▶│ triage by│──▶ gap-fill · coverage · behaviour
  └─────┘                          │ reading  │    audit · simplify · retire
                                   └──────────┘
```

---

## ⚡ Quick Start

### Installation (Claude Code / Codex / ZCode / Kimi Code)

**macOS / Linux**
```bash
git clone https://github.com/brandonzyy/manon.git
cd manon
bash install.sh
```

**Windows**
```cmd
git clone https://github.com/brandonzyy/manon.git
cd manon
install.bat
```

The installer auto-detects your editor, installs dependencies, registers a free account, configures MCP + Playwright, and installs all skills. Restart your editor and you're ready.

> **First use:** Type `/manon` in Claude Code to activate. Manon will index your project and enter knowledge-graph mode.

**Official SaaS** — Free, zero-config, geo-routed. No server setup needed.

<details>
<summary>Environment variables (optional)</summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `MANON_API_URL` | auto (geo-routed) | Override API endpoint. `http://localhost:3700` for self-hosted |
| `MANON_API_KEY` | auto-generated | API key (auto-created on first use) |

</details>

<details>
<summary>Manual MCP config</summary>

Add to `~/.claude.json` (Claude Code) or `~/.kimi-code/mcp.json` (Kimi Code) — same `mcpServers` shape as below. ZCode: `~/.zcode/cli/config.json`, same entry nested under `mcp.servers`. Codex: `[mcp_servers.manon]` in `~/.codex/config.toml`.

```json
{
  "mcpServers": {
    "manon": {
      "command": "python",
      "args": ["/path/to/manon/run_mcp.py"],
      "env": {}
    }
  }
}
```

</details>

---

## 🎯 Skill System

Skills exist only when they provide capabilities that pure LLM conversation cannot — external tool integration (graph API, coverage data), deterministic workflows, or structured output. If the model can do it well in a normal chat, it doesn't need a skill.

| Skill | Role | What it does |
|-------|------|-------------|
| `/manon` | Activation | Index the project, enter graph mode, install hooks |
| `/assurance` | Engineering assurance | One entry, triage by measurement: gap-fill the tool stack, coverage loop, behaviour-layer audit, structural simplification, retiring stale gates |

### `/manon` — Activation

Type `/manon` once per project. It indexes your code (with directory-role analysis for what to index vs skip), shows index status and the 8-dimension code health table, and installs hooks that enforce graph-first search and post-commit impact analysis.

### `/assurance` — Engineering Assurance

The single entry for everything "is this project actually protected". It scores the project's tool stack first — three states: `OK` / `CONFIGURED_NOT_RUN` (configured but never executed — the dangerous one, it *looks* installed) / `MISSING` — then routes by that reading:

```
/assurance — checkup → triage → one of:
             gap-fill  P1-P6: free wins → dead surface → CI → types → coverage → mutation
             coverage loop    rank untested code by fan-in × coverage, add mutation-killing assertions
             behaviour audit  4 contract tables scope it → 5-defect-taxonomy semantic audit
             simplification   graph-driven: 19 principles, A/M panel + auto-fix C layer
             retirement       inventory executors/checkers, prove before deleting, install ratchets
```

The four deterministic contract tables behind the behaviour audit (no model, sub-second, also standalone for CI and git hooks):

| Table | What it reconciles |
|---|---|
| endpoints | routes the backend declares ↔ URLs anything calls (the cross-language edge carried by strings) |
| configs | knobs declared ↔ knobs actually read (decoy knobs, keys only forwarded downstream) |
| states | state values a schema allows ↔ values code writes and reads (dead states, phantom states) |
| envelope | routed entry points → sensitive sinks, with no gate in between |

```bash
python scripts/manon-contract-audit.py <project_path> --fail-on new --baseline <repo_id>
```

`--fail-on new` fails only on surfaces that were not already there, so turning it on does not block every push on day one.

---

## 🔬 Knowledge Graph

### Architecture

```
┌─ Local (manon_mcp) ──────────────────┐     ┌─ Cloud (saas) ──────────────────────┐
│                                      │     │                                      │
│  IDE (Claude Code / ZCode / ...)     │     │  FastAPI application (saas/main.py)  │
│       ↕ MCP protocol                 │     │       ↕                              │
│  manon_mcp/server.py                 │     │  Routers                             │
│    ├─ tools/   (MCP tool handlers)   │     │    query / indexing / repos / ...    │
│    ├─ _client  (HTTP → SaaS API)     │     │       ↕                              │
│    ├─ _sync    (scan + batch upload) │     │  MatrixoneGraph (facade)             │
│    └─ _hooks   (git + editor hooks)  │     │    ├─ CodeGraph  (NetworkX DiGraph)  │
│       ↕                              │     │    ├─ VectorIndex (numpy cosine)     │
│  core/ast (tree-sitter AST parsing)  │     │    ├─ pipeline   (AST → graph)       │
│  codeindex/ (parsers per language)   │     │    └─ impact     (commit analysis)   │
│                                      │     │       ↕                              │
│  ① Scan files                        │     │  services/                           │
│  ② Parse AST locally                 │     │    llm.py (deep_query)               │
│  ③ Upload changed files ─────────────┼────▶│    embedding (vector generation)     │
│                                      │     │                                      │
│  ⑤ Query results ◀───────────────────┼─────┤  ④ Build graph + vectors             │
└──────────────────────────────────────┘     └──────────────────────────────────────┘
```

- **Code stays local** — only AST data is uploaded, never raw source to Git
- **Incremental sync** — file hashes detect changes, upload only diffs
- **Hybrid retrieval** — graph traversal (structural) + vector search (semantic)

### Code Health (8 Dimensions)

| Abbr | Dimension | What it measures |
|------|-----------|-----------------|
| MC | Module Coupling | Cross-module dependency ratio |
| CD | Circular Dependencies | Cycle count |
| FI | Fan-in Concentration | High-fan-in entity ratio |
| DC | Dead Code | Zero-caller entity ratio |
| FS | Function Complexity | Oversized function ratio |
| TD | Technical Debt | TODO/FIXME density |
| MF | Module Fragmentation | Tiny module + deep path ratio |
| RE | Indirection Density | Barrel re-export ratio |

### Language Support

**Specialized parsers** (deep extraction — symbols, calls, imports, inheritances, routes):
Python, TypeScript, JavaScript, Java, PHP

**Generic parser** (symbols + imports via tree-sitter, auto-downloaded on first use):
Go, Rust, C, C++, C#, Ruby, Swift, Kotlin, Scala, Lua, R, Elixir, Dart, Haskell, OCaml, Bash, Zig

---

## 📊 Measured Effectiveness

### 1. Query Intelligence

How much better is graph-powered querying vs. native tools (Grep/Glob/Read)?

**Real-world benchmark** — OpenClaw project, 2,100 files. Full report: [`docs/MANON_VS_NATIVE_COMPARISON_EN.md`](docs/MANON_VS_NATIVE_COMPARISON_EN.md)

| Dimension | Manon | Native Tools | Difference |
|-----------|-------|-------------|------------|
| **Time** | ~30 min | ~8-12 hours | **16-24x faster** |
| **Accuracy** | 95%+ | 60-70% | **+30%** |

**Query tools benchmark** — 20 real-world queries. Full report: [`docs/manon-query-tools-evaluation-en.md`](docs/manon-query-tools-evaluation-en.md)

| Metric | Manon | Native Tools | Improvement |
|--------|-------|-------------|-------------|
| Tool calls per task | 1 | 13.7 | **91% fewer** |
| Total tokens | ~19.5K | ~350K | **94% savings** |
| Quality score | 4.3/5 | 3.2/5 | **+34%** |

### 2. Development Lifecycle (Dogfooding)

Manon uses its own skills to develop itself. These are real outcomes, not synthetic benchmarks.

**Simplification loop (now one loop of `/assurance`)**

Applied to Manon's own codebase (93 files, 800+ entities):

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Code health score | 88/100 | 97/100 | **+9** |
| Dead code entities | 47 | 29 | **-38%** |
| Test coverage | 32% | 61% | **+29pp** |
| Cross-module relations | 0 | 48 | Fixed from zero (was a graph bug) |

The loop identified and auto-fixed: dead functions, over-fragmented modules, barrel re-exports, circular dependencies, and merged 4 redundant files — all validated against the graph before committing.

### 3. Self-Improvement Loop

Code is written with graph context (hooks enforce graph-first) → `/assurance` audits behaviour on suspect surfaces, fills coverage gaps where fan-in is highest, simplifies structure, and retires gates that no longer prove anything → findings feed back into the next cycle. Manon v1.0→v1.2.4 was developed entirely through this loop; v1.5+ consolidates all of it behind a single entry.

---

## 🔍 Query Tools

### `manon_search` — Semantic Code Search

Converts natural language to vector embeddings, retrieves closest entities, expands along graph edges. Solves the "don't know the keyword" problem.

### `manon_graph` — Call Graph Traversal

Directional traversal (callers/callees/both) with configurable depth. Solves the "will changing this break something" problem.

### `manon_deep_query` — Multi-Round Deep Query

Server-side LLM iterative querying. Auto-identifies gaps, generates follow-up queries. One call covers cross-module architecture questions.

### `manon_impact` — Commit Impact Analysis

Parses diffs → extracts changed symbols → traces callers 2 hops → computes risk score (0-100). Instant CI/CD gating.

<details>
<summary>Tool selection guide</summary>

```
What do you need?
├── Find code (don't know keyword)  → manon_search
├── Find code (know the keyword)    → Grep
├── Trace call relationships        → manon_graph
├── Cross-module architecture       → manon_deep_query
├── Assess commit risk              → manon_impact
├── Before modifying code           → manon_search + manon_graph
└── Simple file lookup              → Glob
```

</details>

---

## 🛠️ MCP Tools

| Category | Tool | Description |
|----------|------|-------------|
| **Repo** | `manon_init` | Auto-detect and register project |
| | `manon_repos_list` | List repos and index status |
| | `manon_repos_create/get/delete` | CRUD operations |
| **Index** | `manon_index_status` | Check indexing progress |
| | `manon_push_update` | Incremental sync |
| **Query** | `manon_search` | Semantic code search |
| | `manon_graph` | Call graph traversal |
| | `manon_impact` | Commit impact analysis |
| | `manon_deep_query` | Multi-round deep query |
| | `manon_code_health` | 8-dimension health scoring |
| **Auto** | `manon_setup_hooks` | Install git pre-push hook |
| **Util** | `manon_config/account/usage` | Configuration and account info |

### Automation (Hooks)

| Hook | When | What |
|------|------|------|
| **git pre-push** | After `git push` | Auto-update graph + output health score delta |
| **PreToolUse** | Before Grep/Glob/Agent | Remind to check graph first |
| **PostToolUse** | After `git commit` | Trigger `manon_impact` analysis |

---

## 📡 API Reference

Base URL: `http://your-server:3700/api/v1` — All endpoints require `X-API-Key` header.

<details>
<summary>Repos</summary>

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/repos` | Create repo |
| `GET` | `/repos` | List repos |
| `GET` | `/repos/{id}` | Get repo |
| `DELETE` | `/repos/{id}` | Delete repo |

</details>

<details>
<summary>Indexing</summary>

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/repos/{id}/index-status` | Check status |
| `POST` | `/repos/{id}/push-update` | Incremental update |
| `POST` | `/repos/{id}/sync-ast` | Upload local AST data |

</details>

<details>
<summary>Query</summary>

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/repos/{id}/search` | Semantic search |
| `GET` | `/repos/{id}/graph` | Graph traversal |
| `GET` | `/repos/{id}/impact` | Impact analysis |
| `POST` | `/repos/{id}/deep-query` | Multi-round deep query |

</details>

<details>
<summary>Account</summary>

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/register` | Self-service registration |
| `GET` | `/account` | Account info |
| `GET` | `/usage` | Usage statistics |

</details>

---

## ⚙️ Configuration

All configuration in `~/.manon/config.json`, created automatically.

| Setting | Default | Description |
|---------|---------|-------------|
| `api_key` | auto-generated | Free-tier key |
| `api_url` | geo-routed | Server endpoint |
| `projects` | `{}` | Local project registry |

Override via `MANON_API_KEY`, `MANON_API_URL`.

> **Self-hosted:** Set `MANON_API_URL=http://localhost:3700`. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## 📋 Version History

| Version | Date | Summary |
|---------|------|---------|
| **v1.6.2** | 2026-08-27 | Installer: ZCode + Kimi Code support added; platforms narrowed to Claude Code / Codex / ZCode / Kimi Code (Cursor / Windsurf / Zed / Continue / CodeBuddy / OpenCode removed); fix installer crash when `~/.codex` is missing |
| **v1.6.1** | 2026-08-27 | `/assurance` gap-fill item #6: dependency audit + secret scanning; checker parity fixes |
| **v1.6.0** | 2026-08-27 | Skill consolidation: `/dao` `/audit` `/retire-checks` merged into `/assurance`; `/experience` `/idea` retired; two skills remain (`/manon` + `/assurance`) |
| **v1.5.1** | 2026-08-26 | `/retire-checks` added (missed in 1.5.0); `check_skills.py` gate (install coverage + cross-reference invariants) |
| **v1.5.0** | 2026-08-26 | `/assurance` skill (3-state checkup, triage entry); `/tc` retired into the P5 coverage loop; dao/audit SKILL.md compressed ≤100 lines with `references/` |
| **v1.4.3** | 2026-08-24 | Contract-audit schema lifecycle + write-out-of-bounds criteria; tests 38→55; CaseOS dead surfaces 21→15 |
| **v1.4.2** | 2026-08-24 | Contract-audit policy file no longer counts itself as evidence (exemption list zeroed the table); tests 31→37 |
| **v1.4.1** | 2026-08-24 | Four false-positive classes fixed in contract audit (same-file callers, same-module constants, MIME-as-state, column DEFAULTs) |
| **v1.4.0** | 2026-08-24 | Contract audit: 4 reconciliation tables, MCP tool, zero-model CLI, `/audit` skill, `.manon-contract.yaml`; venv-suffix dirs skipped (18.5s→1.2s) |
| **v1.2.4** | 2026-03-22 | `/idea` + `/exp` skills; HANDLES edge type; Playwright MCP auto-config; complete skill ecosystem |
| **v1.2.3** | 2026-03-22 | `/tc` skill; health dimensions MF/RE; `_resolve()` repo_id tolerance; dao code simplification; release tooling |
| **v1.2.2** | 2026-03-21 | Bugfixes: install.sh crash, Windows MANON_DIR, phantom nodes; TS/JS coverage; scan mtime fast path |
| **v1.2.1** | 2026-03-20 | Knowledge graph quality overhaul: phantom node fix, cross-module edge recovery, type inference; relations +74%; health 97/100 |
| **v1.2.0** | 2026-03-19 | Script classifier; LLM classify endpoint; +115 tests; health 94/100 |
| **v1.1.2** | 2026-03-19 | Major cleanup via `/dao`: dead code removed, test coverage 32%→61% |
| **v1.1.0** | 2026-03-18 | `/dao` skill bundled; MCP tools consolidated |
| **v1.0.0** | 2026-03-16 | Architecture simplification; full test suite |
| **v0.2.0** | 2026-02-23 | Initial open-source release |

<details>
<summary>Detailed changelog</summary>

### v1.2.4 — 2026-03-22

**Complete skill ecosystem: `/idea` + `/exp` + HANDLES edge type.**

- **Added** — `/idea` skill: graph-aware requirement refinement — queries graph + GitHub, Socratic questioning, generates reviewable dev document
- **Added** — `/exp` skill: experience-driven validation — AI agent operates the product (web/cli/service/hybrid) like a real user, 3-round fix loop
- **Added** — HANDLES edge type: AST-extracted HTTP route registrations (Flask/FastAPI, Express, NestJS). Existing repos need re-index
- **Added** — Playwright MCP auto-configured by installer
- **Fixed** — `/idea` scripts: numbered heading tolerance, graph API parsing, Windows encoding

### v1.2.3 — 2026-03-22

**New `/tc` skill, health dimension rework.**

- **Added** — `/tc` skill: graph-prioritized test coverage loop
- **Refactored** — Health dimensions: TC/ID → MF/RE (graph-native)
- **Added** — `_resolve()` repo_id fuzzy matching; `release.py`
- **Improved** — `/dao` semantic signal detection; script classifier
- **Fixed** — Chunk truncation, VectorIndex resilience, dao stop hook scoping
- **Refactored** — Skill sync to standalone; `rate_limit.py` + `adaptive_config.py` merged

### v1.2.2 — 2026-03-21

**Bugfixes + consolidation.**

- **Fixed** — install.sh unbound variable crash, Windows MANON_DIR syntax, phantom graph nodes, dao stop hook
- **Added** — TypeScript/JS coverage support
- **Improved** — Scan mtime fast path
- **Infra** — Consolidated git to GitHub, removed Gitee mirror

### v1.2.1 — 2026-03-20

**Knowledge graph quality overhaul.** Relations +74% (600→1053), health 94→97.

- **Fixed** — Phantom file attribution via `responsible_files`; Python relative import resolution; project-internal absolute import classification
- **Added** — Instance method type inference (`var = ClassName()` tracking)

### v1.2.0 — 2026-03-19

- **Added** — Script classifier (4-signal rule chain + LLM tiebreaker); `POST /classify-scripts`
- **Added** — `/dao` hook enforcement (EnterPlanMode marker + Stop blocker)
- **Refactored** — `git_parser.py` + `symbol_extractor.py` → `parsing.py`
- **Tests** — +115 unit tests

</details>

---

## 📋 Requirements

- Python 3.10+ (auto-installed on Windows via `winget` if missing)
- MCP: Claude Code, Codex, ZCode, or Kimi Code
- Network connection

## 🏗️ Self-Hosting

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for local deployment with Ollama, OpenAI-compatible LLMs, multi-user setup.

## 🤝 Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup and contribution guidelines.

## 💬 Community & Support

- **Issues**: [Report bugs or request features](https://github.com/brandonzyy/manon/issues)
- **Discussions**: [Ask questions or share ideas](https://github.com/brandonzyy/manon/discussions)

## 📄 License

MIT License — see [LICENSE](LICENSE).

Copyright (c) 2026 MatrixOne (Hangzhou) Information Technology Co., Ltd.
