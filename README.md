<div align="center">

# Manon

### Context Management for AI Coding

**The knowledge graph + structured pipeline that keeps LLMs focused, grounded, and transparent.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-6366f1?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJ3aGl0ZSI+PGNpcmNsZSBjeD0iMTIiIGN5PSIxMiIgcj0iMTAiLz48L3N2Zz4=)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

[Quick Start](#-quick-start) · [How It Works](#-how-it-works) · [MCP Tools](#-mcp-tools) · [Web Interface](#-web-interface) · [API Reference](#-api-reference)

</div>

---

## The Problem

LLMs are powerful coders, but they have two structural flaws that degrade output quality:

| Flaw | What happens | Result |
|------|-------------|--------|
| **Insufficient context** | The model can't see your project's call graphs, dependencies, or module boundaries | It **hallucinates** — guesses relationships, misses side effects, suggests changes that break things elsewhere |
| **Unstructured execution** | Given a vague request, the model dives straight into writing code with no plan | It **goes black-box** — attention decays across a long generation, requirements drift, architecture falls apart |

Every AI coding tool today suffers from this. They see one file at a time. They have no process discipline. The smarter the model gets, the more these context problems matter — because a powerful model with bad context just produces confident garbage faster.

## The Solution

Manon is a **context management system for LLMs**. Two mechanisms, one goal:

```
┌─────────────────────────────────────────────────────────────┐
│                     MANON                                   │
│                                                             │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │  Knowledge Graph     │    │  Structured Pipeline       │  │
│  │                      │    │                            │  │
│  │  Solves: what the    │    │  Solves: how the model     │  │
│  │  model sees          │    │  works                     │  │
│  │                      │    │                            │  │
│  │  • Entities, calls,  │    │  • Clarify → Spec →        │  │
│  │    imports, deps   │    │    Design → Decompose →  │  │
│  │  • Vector + graph   │    │    Execute → Review      │  │
│  │    hybrid search    │    │  • Each step: bounded    │  │
│  │  • Precise, minimal │    │    scope, clear I/O      │  │
│  │    sufficient       │    │  • Every output visible  │  │
│  │    context          │    │    to the user            │  │
│  └─────────────────────┘    └────────────────────────────┘  │
│                                                             │
│  Graph = what to look at    Pipeline = what to do           │
│  No hallucination           No black box                    │
└─────────────────────────────────────────────────────────────┘
```

**Knowledge Graph** — Indexes every function, class, call relationship, import chain, and module boundary in your codebase. When the model needs context, it gets precisely the relevant entities and code — not too much, not too little.

**Structured Pipeline** — Forces a deterministic workflow: `clarify → spec → design → decompose → execute → review`. The model handles one bounded step at a time, with clear inputs and outputs. No attention decay. No requirement drift. Every step is visible and interruptible.

## Two Interfaces, Two Audiences

<table>
<tr>
<td width="50%" valign="top">

### 🖥️ MCP — For Developers

Plugs into your IDE. Your AI assistant gets project-wide intelligence without changing your workflow.

**Who it's for:**
- Developers using Claude Code, Cursor, or Windsurf
- Engineers onboarding onto unfamiliar codebases
- Tech leads reviewing change impact
- Anyone tired of AI that only sees one file

**What changes:**
- AI answers grounded in actual call graphs
- Refactoring with full impact visibility
- Feature development follows a structured pipeline — no more "just write it and hope"

</td>
<td width="50%" valign="top">

### 🌐 Web — For Everyone

Browser-based. No IDE, no terminal, no coding experience required.

**Who it's for:**
- Product managers turning specs into working prototypes
- Project managers understanding technical breakdowns
- Learners experiencing AI-powered development firsthand
- Founders building MVPs from plain-language descriptions

**What changes:**
- Describe what you want in natural language
- Watch the full pipeline execute step by step
- See every decision the AI makes — no black box

</td>
</tr>
</table>

---

## ⚡ Quick Start

### MCP Setup (Claude Code / Cursor / Windsurf)

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

The installer auto-detects your editor, installs dependencies, registers a free account, and configures the MCP server. On Windows, it tries Git Bash first and falls back to native PowerShell — Python is installed automatically via `winget` if not found. Restart your editor and you're ready.

> **First use:** Type `/manon` in Claude Code to activate. Manon will index your project and enter knowledge-graph mode. In Cursor/Windsurf, the tools appear automatically.

<details>
<summary>Manual MCP config (if you prefer)</summary>

Add to your editor's MCP config (`~/.claude/settings.json` for Claude Code, `~/.cursor/mcp.json` for Cursor):

```json
{
  "mcpServers": {
    "manon": {
      "command": "python",
      "args": ["/path/to/manon/mcp/server.py"],
      "env": {}
    }
  }
}
```

The API key is managed automatically in `~/.manon/config.json`. No manual key setup needed.

</details>

### Web Setup

```bash
python -m web
# Open http://localhost:3600
```

Browser-based, no coding experience required. Projects and API keys are managed through the UI.

---

## 🔬 How It Works

### Knowledge Graph (Edge-Cloud Architecture)

```
Your Machine                              Cloud
────────────                              ─────
① Scan project files
② Parse AST locally
   (functions, classes, calls, imports)
③ Hash files, send only changes ────────→ ④ Build knowledge graph
                                          ⑤ Generate vector index
                                          ⑥ Store entities & relations
                                              ↓
⑧ AI gets precise context ←──────────── ⑦ Respond to queries
```

- **Local parsing, cloud storage** — code never needs to be pushed to GitHub
- **Incremental sync** — only changed files are uploaded
- **Hybrid search** — graph traversal for precise relationships + vector search for semantic queries

### Structured Pipeline

```
User: "Add WebSocket notifications"
         │
         ▼
┌─ Clarify ──────────────────────────────────────────┐
│  "What events should trigger notifications?         │
│   Should they be per-user or broadcast?"            │
└─────────────────────────┬──────────────────────────┘
                          ▼
┌─ Spec ─────────────────────────────────────────────┐
│  [MUST] WebSocket endpoint at /ws/notifications     │
│  [MUST] Event types: order_created, payment_failed  │
│  [SHOULD] Per-user filtering by subscription        │
└─────────────────────────┬──────────────────────────┘
                          ▼
┌─ Design ───────────────────────────────────────────┐
│  Query knowledge graph → find existing WS hub       │
│  Extend ws_hub.py, add notification router          │
│  Reuse existing auth middleware                     │
└─────────────────────────┬──────────────────────────┘
                          ▼
┌─ Decompose ────────────────────────────────────────┐
│  Task 1: Add notification event types               │
│  Task 2: Create /ws/notifications endpoint          │
│  Task 3: Wire event emitters in order service       │
└─────────────────────────┬──────────────────────────┘
                          ▼
┌─ Execute ──────────────────────────────────────────┐
│  ✓ Task 1/3 — Modified models/events.py            │
│  ✓ Task 2/3 — Created routers/notifications.py     │
│  ✓ Task 3/3 — Updated services/order.py            │
└─────────────────────────┬──────────────────────────┘
                          ▼
┌─ Review ───────────────────────────────────────────┐
│  All tasks pass. 3 files modified, 1 file created.  │
└────────────────────────────────────────────────────┘
```

Every step queries the knowledge graph for relevant context. Every step's output is visible. The user can intervene at any point.

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
| `manon_index` | Trigger code indexing (builds knowledge graph) |
| `manon_index_status` | Check indexing progress |
| `manon_push_update` | Sync latest changes (incremental) |

### Code Intelligence

| Tool | Description |
|------|-------------|
| `manon_search` | Semantic code search — find code by natural language |
| `manon_graph` | Query call graphs and dependencies for any symbol |
| `manon_impact` | Analyze impact of recent commits |
| `manon_deep_query` | Multi-round deep analysis with LLM reasoning |

### Utilities

| Tool | Description |
|------|-------------|
| `manon_config` | Show current configuration |
| `manon_account` | Show account info and quota |
| `manon_usage` | View API usage statistics |

---

## 🌐 Web Interface

The web interface at `http://localhost:3600` provides:

- **Chat** — natural language interaction with knowledge-graph-backed responses
- **Pipeline** — visual step-by-step feature development (clarify → spec → design → decompose → execute → review)
- **Project management** — add local or git projects, monitor indexing status
- **Real-time updates** — WebSocket-powered live progress for all operations

No IDE required. No terminal. Just a browser.

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
| `POST` | `/repos/{id}/index` | Trigger indexing |
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

Override via environment variables if needed: `MANON_API_KEY`, `MANON_API_URL`.

---

## Requirements

- Python 3.10+ (auto-installed on Windows via `winget` if missing)
- For MCP: Claude Code, Cursor, Windsurf, Zed, Continue, or CodeBuddy
- For Web: any modern browser
- Network connection to Manon server

## License

MIT
