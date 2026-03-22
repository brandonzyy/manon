<div align="center">

# Manon

### AI Architect for Your Codebase

**Knowledge graph engine + development skills — from requirements to production, grounded in code facts.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

[Quick Start](#-quick-start) · [Skill System](#-skill-system) · [Knowledge Graph](#-knowledge-graph) · [Query Tools](#-query-tools) · [MCP Tools](#-mcp-tools)

</div>

---

## ❓ The Problem

AI coding has two structural flaws:

| Flaw | Symptom | Consequence |
|------|---------|-------------|
| **Insufficient context** | Model can't see call graphs, dependency chains, module boundaries | **Hallucination** — guesses relationships, misses side effects |
| **Unstructured workflow** | Model dives straight into code without requirements, testing, or validation | **Drift** — scope creep, untested code, silent regressions |

The stronger the model, the worse both problems get — powerful model + bad context + no process = confident garbage, faster.

## 💡 The Solution

Manon provides two layers:

**Layer 1 — Knowledge Graph** (the foundation)
Indexes every function, class, call relationship, import chain, and module boundary. Vector + graph hybrid search. When the model needs context, it gets precisely the relevant code — not too much, not too little.

**Layer 2 — Development Skills** (the workflow)
Five skills that cover the full development lifecycle — requirements, code quality, testing, and validation. Each skill is backed by the graph, ensuring decisions are grounded in code facts, not LLM imagination.

```
  /idea        write code       /dao          /tc           /exp
  ┌─────┐      ┌─────┐       ┌─────┐       ┌─────┐       ┌─────┐
  │Refine│ ──▶ │Build│  ──▶  │Clean│  ──▶  │Test │  ──▶  │Verify│
  │ Req  │     │     │       │     │       │     │       │ E2E  │
  └──┬──┘      └──┬──┘       └──┬──┘       └──┬──┘       └──┬──┘
     │            │              │              │              │
     └──────────  all grounded in knowledge graph  ───────────┘
```

---

## ⚡ Quick Start

### Installation (Claude Code / Cursor / Windsurf)

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

Add to `~/.claude/settings.json` (Claude Code) or `~/.cursor/mcp.json` (Cursor):

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

Skills exist only when they provide capabilities that pure LLM conversation cannot — external tool integration (graph API, coverage data, Playwright), deterministic workflows, or structured output. If Claude can do it well in a normal chat, it doesn't need a skill.

| Phase | Skill | What it does | Why a skill, not just chat? |
|-------|-------|-------------|----------------------------|
| **Requirements** | `/idea` | Graph + GitHub research → Socratic questioning → dev document | Questions based on code facts (fan-in, dependencies), not generic |
| **Development** | Claude + graph | Write code with `manon_search` / `manon_graph` | Hooks enforce graph-first; `manon_impact` after every commit |
| **Maintenance** | `/dao` | Health scan → 3-layer classification → auto-simplify | Batch Architecture/Module/Code analysis with graph validation |
| **Testing** | `/tc` | Coverage scan → graph-prioritize → write tests → verify | Ranks by structural importance, not random |
| **Validation** | `/exp` | AI agent operates the product like a real user | Playwright/Bash to click, type, read logs — not imagination |

### `/idea` — Requirement Refinement

Queries the knowledge graph and GitHub, then asks Socratic questions grounded in what it found — "Module X has high fan-in, should the new feature go there or in a new module?" After 3-7 rounds, proposes 2-3 approaches with impact analysis, outputs a reviewable dev document.

```
/idea   — context → questioning → propose → document → review
```

### `/dao` — Code Simplification

Scans code health, classifies complexity into three layers (Architecture / Module / Code), shows A/M issues for your pick, auto-fixes all C issues with graph validation (e.g., dead code deletion only after zero-caller confirmation).

```
/dao    — health scan → classify → A/M panel + auto-fix C → commit
```

### `/tc` — Test Coverage

Scans coverage data, queries the graph for entity importance (fan-in, complexity, centrality), generates a prioritized list of untested code, writes tests, runs them, commits.

```
/tc     — coverage scan → graph-prioritize → write tests → verify → commit
```

### `/exp` — Experience Validation

AI agent operates the product like a real user. Supports 4 product types:

| Type | Tools | Use Case |
|------|-------|----------|
| `web` | Playwright MCP | Frontend pages |
| `cli` | Bash | Scripts, CLI tools |
| `service` | curl + logs | Backend APIs |
| `hybrid` | Playwright + Bash | Full-stack |

```
/exp    — define scenarios → agent operates → report → fix → re-test (max 3 rounds)
```

---

## 🔬 Knowledge Graph

### Architecture (Edge-Cloud)

```
Local                                     Cloud
─────                                     ─────
① Scan project files
② Parse AST locally (tree-sitter)
   functions, classes, calls, imports
③ Hash files, send only changes ────────→ ④ Build knowledge graph
                                          ⑤ Generate vector index
                                          ⑥ Store entities & relations
                                              ↓
⑧ AI gets precise context ←──────────── ⑦ Respond to queries
```

- **Local parsing, cloud storage** — code never needs to be pushed to Git
- **Incremental sync** — only changed files are uploaded
- **Hybrid search** — graph traversal for precise relationships + vector search for semantic queries

### Edge Types

| Edge | Source | Example |
|------|--------|---------|
| `calls` | AST call expressions | `search_handler → SearchEngine.execute` |
| `imports` | AST import statements | `router.py → SearchEngine` |
| `inherits` | AST class definitions | `AdminUser → BaseUser` |
| `handles` | AST route decorators | `GET /api/search → search_handler` |

All edges are AST-deterministic. No string inference, no statistical correlation.

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

Score changes output automatically after every `git push`.

### Language Support

**Specialized parsers** (deep extraction: symbols, calls, imports, inheritances, routes):
Python, TypeScript, JavaScript, Java, PHP (6 languages)

**Generic parser** (symbols + imports via tree-sitter, auto-downloaded):
Go, Rust, C, C++, C#, Ruby, Swift, Kotlin, Scala, Lua, R, Elixir, Dart, Haskell, OCaml, Bash, Zig (17 languages)

---

## 📊 Measured Effectiveness

### Real-World Benchmark

Analyzed OpenClaw project (2,100 files). Full report: [`docs/MANON_VS_NATIVE_COMPARISON_EN.md`](docs/MANON_VS_NATIVE_COMPARISON_EN.md)

| Dimension | Manon | Native Tools | Difference |
|-----------|-------|-------------|------------|
| **Time** | ~30 min | ~8-12 hours | **16-24x faster** |
| **Accuracy** | 95%+ | 60-70% | **+30%** |

### Query Tools Benchmark

20 real-world queries. Full report: [`docs/manon-query-tools-evaluation-en.md`](docs/manon-query-tools-evaluation-en.md)

| Metric | Manon | Native Tools | Improvement |
|--------|-------|-------------|-------------|
| Tool calls per task | 1 | 13.7 | **91% fewer** |
| Total tokens | ~19.5K | ~350K | **94% savings** |
| Quality score | 4.3/5 | 3.2/5 | **+34%** |

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

## 🗺️ Roadmap

### Structured Pipeline (Planned)

A deterministic workflow enforcing `clarify → spec → design → decompose → execute → review`. Each step bounded, with clear inputs and outputs, visible and interruptible. Combined with the knowledge graph, this will eliminate the black-box problem in AI coding.

---

## 📋 Requirements

- Python 3.10+ (auto-installed on Windows via `winget` if missing)
- MCP: Claude Code, Cursor, Windsurf, Zed, Continue, or CodeBuddy
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
