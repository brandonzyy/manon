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

> **Included:** Installation automatically includes the `/dao` skill (大道至简 - code simplification) for Claude Code users. This skill uses Manon's knowledge graph to systematically simplify codebases through architecture → module → code analysis.
>
> **Dao Commands:**
> - `/dao` — Manual mode: Execute one simplification iteration, then stop and wait
> - `/dao auto` — Auto mode: Loop until simplified (max 10 iterations), skip medium/high-risk changes
> - `/dao -autorisk` — Auto-risk mode: Loop with automatic medium/high-risk simplifications enabled
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

> **code_health dimensions:** Module Coupling (MC), Circular Dependencies (CD), Fan-in Concentration (FI), Dead Code (DC), Test Coverage (TC), Function Size (FS), Technical Debt (TD), Inheritance Depth (ID). Score changes output automatically after each push.

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

## 📦 Changelog

### v1.2.0 — 2026-03-19

#### New Features

**Script Classifier** — Manon now intelligently filters tool scripts out of the knowledge graph index. Before indexing, each Python file is run through a 4-signal priority chain:

1. **Imported by project** → keep as source code (definitive)
2. **Imports project modules** → keep as source code (definitive)
3. **Tool name + standalone entry point** (`deploy_*/setup_*/run_*` + `__main__` guard, ≤2 public APIs) → drop as tool script (definitive)
4. **Uncertain** → sent to LLM tiebreaker

This prevents deploy scripts, seed scripts, and admin utilities from polluting the graph with false call relationships.

**LLM Classify Endpoint** — New `POST /api/v1/classify-scripts` endpoint handles uncertain files that don't match the rule-based signals. The MCP scanner sends file summaries (path, imports, exports, docstring, line count) and receives `tool_script` / `source_code` verdicts.

**Dao Hooks** — Strengthened the `/dao` skill's enforcement mechanism. The `PreToolUse EnterPlanMode` hook now automatically writes a marker file from the plan header, ensuring `dao-commit.py` always runs after plan execution. The Stop hook blocks Claude Code until the commit step completes.

#### Bug Fixes

Three bugs were discovered and fixed during gray-scale testing on the actual Manon codebase (86 files):

- **Import key mismatch** — `ScriptSignals._from_parse_result` was reading imports using the `"name"` key, but `scan_and_parse` stores them under `"module"`. This caused 100% of files to fall through to the "uncertain" LLM tiebreaker.
- **Relative import resolution** — `build_imported_paths` didn't handle relative imports (`from . import _hooks` stored as `module='.'`, `names=['_hooks']`). Without resolution, files imported only via relative paths were falsely classified as tool scripts and dropped.
- **Import lookup key** — `build_imported_paths` also read the wrong dict key when iterating imports, compounding the above issue.

After all three fixes: 83 source files kept, 3 entry-point scripts correctly dropped (`__main__.py` × 2, `parser_installer.py`), 0 false positives on core source modules.

#### Refactors

- **`core/ast/framework_detection.py`** (new) — Test framework detection logic extracted from `analysis.py`. The original file was named `test_detection.py`, which caused it to be excluded by the test scanner's `**/test_*.py` pattern.
- **`matrixone_graph/impact/parsing.py`** — Merged `git_parser.py` + `symbol_extractor.py` into a single file. Both were small (74L + 64L), part of the same parsing pipeline, and had never been modified independently.

#### Tests

- +88 unit tests for `core/script_classifier` — covers all 4 signal paths, `ScriptSignals` construction from source/parse-result/empty, `classify_batch` routing, `build_imported_paths` resolution, and `is_scripts_like_path`
- +27 unit tests for `saas/routers/classify` — covers `_build_classify_prompt` formatting, `FileSummary` defaults, and the endpoint's empty/no-key/success/invalid-values/LLM-error/usage-tracking paths

#### Code Health

`94/100` — up from `88/100` at v1.1.2. All 8 dimensions tracked (MC, CD, FI, DC, TC, FS, TD, ID).

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
