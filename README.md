<div align="center">

# Manon

### Context Management for AI Coding

**MCP service powered by the MatrixOneGraph knowledge graph engine — precise, controllable AI programming.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

[Quick Start](#-quick-start) · [How It Works](#-how-it-works) · [Query Tools](#-query-tools-in-depth) · [MCP Tools](#-mcp-tools) · [API Reference](#-api-reference)

</div>

---

## ❓ The Problem

The core flaw of AI coding: **insufficient context**.

| Flaw | Symptom | Consequence |
|------|---------|-------------|
| **Insufficient context** | Model can't see call graphs, dependency chains, module boundaries | **Hallucination** — guesses relationships, misses side effects, breaks things elsewhere |

The stronger the model, the worse the context problem — powerful model + bad context = confident garbage, faster.

## 💡 The Solution

Manon is an MCP service powered by the **MatrixOneGraph knowledge graph engine**, providing precise context for AI coding:

**MatrixOneGraph Knowledge Graph** — Indexes every function, class, call relationship, import chain, and module boundary in your codebase. When the model needs context, it gets precisely the relevant entities and code — not too much, not too little.

- **Entities, calls, imports** — full structural indexing
- **Vector + graph hybrid search** — precise relationships + semantic queries
- **Precise, minimally sufficient context** — eliminates hallucination

## 📊 Measured Effectiveness

### Real-World Analysis Benchmark

Analyzed OpenClaw project (2,100 files) to develop a streamlining plan. Full report: [`docs/MANON_VS_NATIVE_COMPARISON_EN.md`](docs/MANON_VS_NATIVE_COMPARISON_EN.md)

| Dimension | Using Manon | Using Native Tools | Difference |
|-----------|-------------|-------------------|------------|
| **Time Required** | ~30 minutes | ~8-12 hours | **16-24x faster** |
| **Analysis Depth** | Deep semantic understanding | Surface text matching | Manon deeper |
| **Accuracy** | 95%+ | 60-70% | **+30%** |
| **Reliability** | Graph-based relationships | Speculation-based | Manon more reliable |

**Key Advantages**:
- **Semantic understanding** — Understands code meaning and relationships, not just text matching
- **Relationship graph** — 52,701 entities, 73,865 relationships, instant multi-layer dependency tracing
- **Natural language queries** — Describe intent without knowing exact keywords

### Query Tools Evaluation

Evaluated with 20 real-world queries (5 per tool), benchmarked against native tools (Grep/Glob/Read/git) on identical tasks. Full report: [`docs/manon-query-tools-evaluation-en.md`](docs/manon-query-tools-evaluation-en.md)

| Metric | Manon | Native Tools | Improvement |
|--------|-------|-------------|-------------|
| Avg tool calls per task | 1 | 13.7 | **91% fewer** |
| Total tokens (20 queries) | ~19.5K | ~350K | **94% savings** |
| Avg quality score | 4.3/5 | 3.2/5 | **+34%** |

| Tool | Use Case | Calls Saved | Quality (Manon → Native) |
|------|----------|-------------|--------------------------|
| `manon_search` | Semantic code search | 86% | 4.2 vs 2.6 |
| `manon_graph` | Call graph traversal | 90% | 4.6 vs 2.6 |
| `manon_deep_query` | Multi-round architecture analysis | 94% | 4.6 vs 2.6 |
| `manon_impact` | Commit impact analysis | 95% | 3.8 vs 4.8 ¹ |

> ¹ `impact` trades depth for speed — 80% of the insight in 1/66 of the tokens. For high-risk commits, pair with manual review.

### Unique Value (Hard to Achieve with Native Tools)

1. **Semantic search** — Describe intent in natural language without knowing exact naming. Search "error handling" to find all exception-related code, not just `Exception` keyword matches
2. **Directional graph traversal** — Distinguish callers (who calls it) vs callees (what it calls). Native Grep only finds reference lines with no direction
3. **Automatic coverage analysis** — LLM identifies information gaps and generates follow-up queries. Complex cross-module questions resolved in one call
4. **Structured entities + relations** — Returns typed, scored, relationship-aware data, not raw text lines
5. **Instant impact screening** — One call returns changed symbols, callers, propagation chains, and risk scores. Directly usable for CI/CD gating

### Tool Selection Decision Tree

```
What do you need?
├── Find code/features (don't know the keyword)
│   └── manon_search → supplement with Grep if needed
├── Find code (know the exact keyword)
│   └── Grep (faster, more precise)
├── Trace call relationships/dependencies
│   └── manon_graph → increase depth if needed
├── Understand cross-module architecture
│   └── manon_deep_query (automatic multi-round)
├── Assess commit impact
│   ├── Quick screening → manon_impact
│   └── risk ≥ 60 → manon_impact + native deep analysis
├── Before modifying code
│   └── manon_search + manon_graph (understand context)
└── Simple file lookup
    └── Glob
```

## ⚡ Quick Start

### Using the Official Service (Recommended)

Manon provides a free official SaaS service — no server setup required. After installation, the MCP client connects to the official API automatically (geo-routed by region).

**Environment variables** (optional, for customization):

| Variable | Default | Description |
|----------|---------|-------------|
| `MANON_API_URL` | auto (geo-routed) | Override API endpoint. Set to `http://localhost:3700` for self-hosted |
| `MANON_API_KEY` | auto-generated | API key (auto-created on first use) |
| `MANON_API_URL_CN` | `http://saas.matrixone.online:3700` | China endpoint |
| `MANON_API_URL_INTL` | `http://203.208.134.27:3700` | International endpoint (Singapore) |

To use the official service, just install and run — no environment variables needed.

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

The installer auto-detects your editor, installs dependencies, registers a free account, and configures the MCP server. On Windows, it tries Git Bash first and falls back to PowerShell — Python is installed automatically via `winget` if missing. Restart your editor and you're ready.

> **Included:** Installation automatically includes the `/dao` and `/tc` skills — see [大道至简 (Dao)](#-大道至简-dao--graph-driven-code-simplification) and [Test Coverage (TC)](#-test-coverage-tc--graph-prioritized-test-loop) below.
>
> **First use:** Type `/manon` in Claude Code to activate. Manon will index your project and enter knowledge-graph mode. In Cursor/Windsurf, tools appear automatically.

<details>
<summary>Manual MCP config</summary>

Add to your editor's MCP config (`~/.claude/settings.json` for Claude Code, `~/.cursor/mcp.json` for Cursor):

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

The API key is managed automatically in `~/.manon/config.json`. No manual setup needed.

</details>

### Initialization (One-Time)

```
After installation, first use in your IDE:

manon_init          → Auto-detect project, register repo, build knowledge graph
                      Also installs Claude Code hooks (auto-remind to check graph before search/edit)
manon_setup_hooks   → Install git pre-push hook, auto-update graph + output health score after push
manon_code_health   → First code health check, get 8-dimension baseline score
```

Three steps, then all tools work automatically.

**Claude Code Hooks (installed by install.sh/install.bat):**
- **Before Grep/Glob** — Reminds to check knowledge graph first, avoiding blind searches
- **Before Agent (Explore/general-purpose)** — Reminds to query Manon before spawning exploration agents
- **After Commit** — Automatically triggers manon_impact analysis after successful git commits

**Git Pre-Push Hook (installed by manon_init):**
- Auto-incrementally updates knowledge graph after push
- Auto-outputs code health score changes
- Can also be manually installed via manon_setup_hooks

### Daily Workflow

```
Write code → git push → hook auto-updates knowledge graph (zero effort)
                              ↓
┌─────────────────────────────────────────────────────┐
│  Find code       manon_search / manon_graph          │
│  Deep analysis   manon_deep_query                    │
│  Assess changes  manon_impact                        │
│  Code health     manon_code_health → 8 dimensions    │
│                  Module coupling · Circular deps      │
│                  Fan-in · Dead code · Test coverage   │
│                  Function size · Tech debt · Depth    │
└─────────────────────────────────────────────────────┘
```

> **code_health dimensions:** Module Coupling (MC), Circular Dependencies (CD), Fan-in Concentration (FI), Dead Code (DC), Function Complexity (FS), Technical Debt (TD), Module Fragmentation (MF), Indirection Density (RE). Score changes output automatically after each push.

---

## 🌿 大道至简 (Dao) — Graph-Driven Code Simplification

Bundled with Manon, `/dao` is a Claude Code skill that uses the knowledge graph to systematically find and remove unnecessary complexity — without guessing.

Type `/dao` in Claude Code. It queries the graph for health scores, identifies where complexity is concentrated, and classifies findings into three layers:

| Layer | Scope | Examples |
|-------|-------|---------|
| **Architecture (A)** | System structure | Unnecessary layers, over-modularization, premature generalization |
| **Module (M)** | Module boundaries | Feature bloat, unclear boundaries, duplication, excessive dependencies |
| **Code (C)** | File & function level | Dead code, over-fragmentation, circular deps, low cohesion |

**Architecture and Module issues** are shown in an interactive panel. You pick one, Claude designs and executes a plan with your approval, then commits and re-syncs the graph.

**Code-layer issues** run automatically — no confirmation needed. Each fix is validated against the graph (e.g. dead code deletion only proceeds after zero-caller confirmation), committed, and the graph is updated before moving to the next.

Issues are tracked in `.dao/issues.json`. Health scores update after every commit.

```
/dao    — query graph → show A/M panel → auto-fix all C issues → stop
```

---

## 🧪 Test Coverage (TC) — Graph-Prioritized Test Loop

Bundled with Manon, `/tc` is a Claude Code skill that uses the knowledge graph to prioritize which code needs tests most — high fan-in, high complexity, zero coverage first.

Type `/tc` in Claude Code. It scans existing coverage data, queries the graph for entity importance (fan-in, complexity, centrality), and generates a prioritized list of untested or under-tested code. Then it writes tests, runs them, and commits — in a loop until coverage targets are met.

```
/tc    — scan coverage → graph-prioritize → write tests → verify → commit → repeat
```

---

## 🔬 How It Works

### MatrixOneGraph Knowledge Graph (Edge-Cloud Architecture)

```
Local                                     Cloud
─────                                     ─────
① Scan project files
② Parse AST locally
   (functions, classes, calls, imports)
③ Hash files, send only changes ────────→ ④ Build knowledge graph
                                          ⑤ Generate vector index
                                          ⑥ Store entities & relations
                                              ↓
⑧ AI gets precise context ←──────────── ⑦ Respond to queries
```

- **Local parsing, cloud storage** — code never needs to be pushed to Git
- **Incremental sync** — only changed files are uploaded
- **Hybrid search** — graph traversal for precise relationships + vector search for semantic queries

---

## 🔍 Query Tools In Depth

Manon provides 4 core query tools covering the full spectrum from code search to architecture analysis. Each tool is built on the knowledge graph, completing in a single MCP call what native tools require 7-20 calls to achieve.

### `manon_search` — Semantic Code Search

**How it works:** Converts natural language queries into vector embeddings, retrieves semantically closest entities from the knowledge graph's vector index, then expands along graph edges to include related entities and relationships. Returns entities + relations + code snippets in one response.

**Goal:** Solve the "don't know what keyword to search" problem. Describe intent (e.g., "error handling"), find all related code regardless of naming conventions.

| Dimension | Details |
|-----------|---------|
| Input | Natural language query + top_k + depth |
| Output | Matched entities (with relevance scores) + relation edges + code snippets |
| Strength | Semantic understanding > keyword matching; cross-module aggregation |
| Best for | Exploratory search, conceptual queries, onboarding |
| Limitation | Very specific string searches are better with Grep |

### `manon_graph` — Call Graph Traversal

**How it works:** Locates the target symbol in the knowledge graph, traverses call edges directionally (callers = who calls it, callees = what it calls), supports multi-level depth expansion, returns complete structured call chains.

**Goal:** Solve the "will changing this function break something else" problem. One call reveals all usage scenarios and dependencies. Native Grep only finds reference lines without direction.

| Dimension | Details |
|-----------|---------|
| Input | Symbol name + direction (callers/callees/both) + depth |
| Output | Caller/callee lists + call chain paths + entity details |
| Strength | Directional traversal; multi-level depth; structured call chains |
| Best for | Pre-modification impact assessment, understanding module dependencies |
| Limitation | Dynamic calls (reflection, eval) may be missed |

### `manon_deep_query` — Multi-Round Deep Query

**How it works:** Server-side LLM-driven iterative querying. The LLM analyzes coverage of existing context, automatically identifies information gaps, generates supplementary queries, and iterates until all sub-aspects are covered. Single MCP call, all iterations happen server-side.

**Goal:** Solve the "cross-module questions need many rounds of exploration" problem. Ask one architecture-level question, the system automatically decomposes, queries each aspect, and synthesizes a comprehensive answer.

| Dimension | Details |
|-----------|---------|
| Input | Natural language question + max_rounds |
| Output | Comprehensive analysis report (covering all sub-aspects) + per-round query logs |
| Strength | Auto-identifies coverage gaps + auto-supplements; cross-module in one call |
| Best for | Cross-module architecture understanding, multi-subsystem analysis, onboarding |
| Limitation | Complex meta-queries may timeout and degrade to single-round |

### `manon_impact` — Commit Impact Analysis

**How it works:** Parses commit diffs, extracts changed symbols (functions/classes), traces backwards 2 hops along call edges in the knowledge graph, identifies all direct and indirect callers, computes affected modules and propagation chains, outputs a quantified risk score (0-100).

**Goal:** Solve the "will this commit break something" problem. Get a complete impact report in seconds, directly usable for CI/CD gating. High-risk commits (≥60) should be paired with manual deep review.

| Dimension | Details |
|-----------|---------|
| Input | Commit hash + max_depth |
| Output | Changed symbols + caller traces + affected modules + propagation chains + risk score |
| Strength | Instant risk screening; quantified scoring; propagation visualization |
| Best for | Quick risk screening, CI/CD gating, code review assistance |
| Limitation | 2-hop depth limit truncates distant impacts; can't detect semantic behavior changes |

---

## 🛠️ MCP Tools

### Repository Management

| Tool | Description |
|------|-------------|
| `manon_init` | Auto-detect and register current project |
| `manon_repos_list` | List all repos and their index status |
| `manon_repos_create` | Add a repo (by git URL or local path) |
| `manon_repos_get` | Get repo details |
| `manon_repos_delete` | Delete a repo |

### Indexing

| Tool | Description |
|------|-------------|
| `manon_index_status` | Check indexing progress |
| `manon_push_update` | Sync latest changes (incremental) |

### Code Intelligence

| Tool | Description |
|------|-------------|
| `manon_search` | Semantic code search — find code by natural language |
| `manon_graph` | Query call graphs and dependencies |
| `manon_impact` | Analyze impact of recent commits |
| `manon_deep_query` | Multi-round deep analysis with LLM reasoning |
| `manon_code_health` | Code health scoring — 8-dimension analysis |

### Automation

| Tool | Description |
|------|-------------|
| `manon_setup_hooks` | Install git pre-push hook, auto-update graph + output health score |

### Utilities

| Tool | Description |
|------|-------------|
| `manon_config` | Show current configuration |
| `manon_account` | Show account info and quota |
| `manon_usage` | View API usage statistics |

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

All configuration is stored in `~/.manon/config.json`, created automatically on first run.

| Setting | Default | Description |
|---------|---------|-------------|
| `api_key` | auto-generated | Free-tier key, obtained on first use |
| `api_url` | geo-routed | Server endpoint (auto-selected by region) |
| `projects` | `{}` | Local project registry and file hashes |

Override via environment variables: `MANON_API_KEY`, `MANON_API_URL`.

> **Official service vs self-hosted:** By default, Manon connects to the official SaaS service (geo-routed). To use a self-hosted server, set `MANON_API_URL=http://localhost:3700`. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for self-hosting instructions.

---

## 📋 Version History

| Version | Date | Summary |
|---------|------|---------|
| **v1.2.3** | 2026-03-22 | `/tc` skill; health dimensions MF/RE; `_resolve()` repo_id tolerance; dao code simplification; release tooling |
| **v1.2.2** | 2026-03-21 | Bugfixes: install.sh unbound variable crash, Windows MANON_DIR syntax, phantom graph nodes; TS/JS coverage support; scan mtime fast path |
| **v1.2.1** | 2026-03-20 | Knowledge graph quality overhaul: phantom node root-cause fix, cross-module edge recovery, instance method type inference; relations +74%; code health 97/100 |
| **v1.2.0** | 2026-03-19 | Script classifier filters tool scripts from index; LLM classify endpoint; +115 tests; code health 94/100 |
| **v1.1.2** | 2026-03-19 | Major code cleanup via `/dao`: dead code removed, functions decomposed, test coverage 32%→61%; TC dimension now reads real coverage data |
| **v1.1.1** | 2026-03-18 | Fixed index coverage stats inaccuracy |
| **v1.1.0** | 2026-03-18 | `/dao` skill bundled; C2/C4/C8 code simplification; MCP tools consolidated |
| **v1.0.0** | 2026-03-16 | Architecture simplification; renamed `mcp/`→`manon_mcp/`, removed `shared/`; full test suite |
| **v0.2.5** | 2026-03-13 | Scan/upload split to avoid MCP timeout; heavy ops moved out of MCP process; Claude Code hooks |
| **v0.2.2** | 2026-03-07 | Embedded codeindex into repo; eliminated external dependency; fast language detection |
| **v0.2.1** | 2026-03-07 | Migrated to brandonzyy/codeindex fork; auto tree-sitter parser installation |
| **v0.2.0** | 2026-02-23 | Initial open-source release with MCP integration and knowledge graph |

---

## 📦 Changelog

### v1.2.3 — 2026-03-22

**New `/tc` skill, health dimension rework, and robustness improvements.**

- **Added** — `/tc` skill: graph-prioritized test coverage loop — scans coverage, ranks untested code by graph importance, writes tests, verifies, and commits
- **Refactored** — Code health dimensions: replaced Test Coverage (TC) and Inheritance Depth (ID) with Module Fragmentation (MF) and Indirection Density (RE) for graph-native coverage
- **Added** — `_resolve()` repo_id tolerance across all MCP tools — fuzzy matching for robustness
- **Added** — `release.py` script to prevent master/dev divergence during releases
- **Improved** — `/dao` skill: semantic signal detection (config/event/pattern files), updated health dimension mappings
- **Improved** — Script classifier: added "skills" to `_SOURCE_DIRS` so skill scripts are properly indexed
- **Fixed** — Chunk truncation, VectorIndex resilience, repo-id recovery, multi-language classifier
- **Fixed** — dao stop hook scoped to current session (CWD match)
- **Refactored** — Skill sync moved out of MCP server to standalone tooling
- **Refactored** — `rate_limit.py` merged into `saas/auth.py`; `adaptive_config.py` merged into `codeindex/config.py`
- **Infra** — Consolidated git to GitHub, removed Gitee mirror

---

### v1.2.2 — 2026-03-21

**Bugfixes + incremental improvements.** Git repository consolidated to GitHub only (Gitee mirror removed).

- **Fixed** — `install.sh` crash: `DEFAULT_API_URL: unbound variable` (API_URL assignment moved after region detection)
- **Fixed** — Broken Windows `set` syntax for `MANON_DIR` in skill scripts
- **Fixed** — Phantom nodes and empty-caller edges in knowledge graph
- **Fixed** — dao stop hook scoped to current session (CWD match + 6h TTL)
- **Added** — TypeScript/JS coverage support in `manon-scan-tests.py`
- **Improved** — Scan performance: mtime+size fast path skips unchanged files; partial parse on syntax errors
- **Infra** — Consolidated git to GitHub (`github.com/brandonzyy/manon`), removed Gitee mirror and sync workflow

---

### v1.2.1 — 2026-03-20

**Knowledge graph quality overhaul** — Four root-cause fixes that eliminate phantom node pollution and cross-module edge loss. Validated with a full rebuild: relations +74% (600→1053), cross-module edges 0→51, health score 94→97.

- **Phantom file attribution** (`responsible_files`) — each phantom node now tracks which source files are responsible for it. `remove_by_file()` surgically cleans up orphaned phantoms on incremental updates, eliminating stale-graph pollution without requiring a full rebuild.
- **Python relative import fix** — dots in `.utils` / `..utils` were misinterpreted as hidden filenames by `posixpath.normpath`, producing double/triple-dot entity IDs. Fixed by parsing leading-dot count to correctly resolve package depth.
- **Project-internal absolute import fix** — all non-relative imports were incorrectly marked as external, silently dropping call edges to project-internal classes (e.g. `CodeGraph`, `VectorIndex`). Fixed by introducing `local_packages` (top-level dirs with `__init__.py`) to distinguish truly external packages.
- **Instance method type inference** — Python parser now tracks `var = ClassName()` and `var: ClassName = ...` assignments in function bodies. Calls like `var.method()` are resolved to `ClassName.method()` and correctly added as graph edges.
- **Refactored** — internal `_Fake*` dataclasses replaced with proper `codeindex.parser` types throughout the indexing pipeline.
- **Code health** — `97/100`, up from `94/100`.

---

### v1.2.0 — 2026-03-19

**`/dao` hook enforcement** — A two-part hook (EnterPlanMode marker + Stop blocker) now guarantees `dao-commit` always runs after plan execution, closing the issue and syncing the graph.

- **Script classifier** — Filters tool scripts (`deploy_*`, `setup_*`, etc.) from the index via a 4-signal rule chain; ambiguous files go to an LLM tiebreaker.
- **`POST /api/v1/classify-scripts`** — New endpoint for LLM-based script classification.
- **Fixed** — 3 bugs in the classifier found during gray-scale testing: wrong import dict key, missing relative import resolution, wrong key in `build_imported_paths`.
- **Refactored** — `git_parser.py` + `symbol_extractor.py` merged into `parsing.py`; test framework detection extracted to `framework_detection.py`.
- **Tests** — +115 unit tests (script classifier + classify endpoint).
- **Code health** — `94/100`, up from `88/100`.

---

## 🗺️ Roadmap

### Structured Pipeline (Planned)

Another structural flaw of AI coding is **unstructured execution** — given a request, the model dives straight into writing code, leading to attention decay, requirement drift, and architecture collapse.

We're developing a structured pipeline that enforces a deterministic workflow: `clarify → spec → design → decompose → execute → review`. Each step is bounded, with clear inputs and outputs, visible and interruptible. Combined with the knowledge graph's precise context, this will eliminate the black-box problem in AI coding.

---

## 📋 Requirements

- Python 3.10+ (auto-installed on Windows via `winget` if missing)
- MCP: Claude Code, Cursor, Windsurf, Zed, Continue, or CodeBuddy
- Network connection

## 🏗️ Self-Hosting

Want to run your own Manon server? See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for:
- Local deployment with Ollama
- OpenAI-compatible LLM configuration
- Multi-user setup
- Docker deployment (coming soon)

## 🤝 Contributing

Manon is open source and welcomes contributions! See [`CONTRIBUTING.md`](CONTRIBUTING.md) for:
- Development setup
- Code style guidelines
- Pull request process
- Areas for contribution

## 💬 Community & Support

- **Issues**: [Report bugs or request features](https://github.com/brandonzyy/manon/issues)
- **Discussions**: [Ask questions or share ideas](https://github.com/brandonzyy/manon/discussions)
- **Documentation**: [`docs/`](docs/) for architecture and deployment guides

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

Copyright (c) 2026 MatrixOne (Hangzhou) Information Technology Co., Ltd.
