# Manon — AI Code Intelligence Server

Manon is a code knowledge graph service that provides semantic search, call-graph traversal, and impact analysis for codebases. It works as an MCP (Model Context Protocol) server for AI coding assistants like Claude Code and Cursor.

## Architecture

```
┌─────────────────────┐       ┌──────────────────────┐
│  Claude Code/Cursor  │       │   Manon SaaS Server  │
│  (AI Assistant)      │       │   (FastAPI :3700)     │
│         │            │       │                      │
│   ┌─────▼──────┐     │  HTTP │  ┌────────────────┐  │
│   │ Manon MCP  │─────┼──────┼─▶│ Repos/Indexing  │  │
│   │ (server.py)│     │      │  │ Search/Graph    │  │
│   └────────────┘     │      │  │ Impact/Pipeline │  │
│     Local AST        │      │  └───────┬────────┘  │
│     Extraction       │      │          │           │
└─────────────────────┘       │  ┌───────▼────────┐  │
                              │  │ MatrixoneGraph  │  │
                              │  │ (Knowledge Graph│  │
                              │  │  + Vectors)     │  │
                              │  └────────────────┘  │
                              └──────────────────────┘
```

- **Manon SaaS Server** (`saas/`) — Cloud API: repo management, indexing, search, graph queries
- **Manon MCP Client** (`manon-mcp/`) — MCP server for Claude Code/Cursor, handles local AST extraction
- **MatrixoneGraph** (`matrixone_graph/`) — Code knowledge graph engine (NetworkX + vector index)

### Skill vs MCP

Manon has two layers that work together:

| Layer | What it is | What it does |
|-------|-----------|--------------|
| **MCP Server** | `mcpServers` config in `settings.json` | Provides 16 tools (`manon_search`, `manon_graph`, etc.). Claude Code auto-starts the `server.py` process. Works without the skill. |
| **Skill** | `/manon` slash command (`~/.claude/skills/manon/SKILL.md`) | Orchestrates the MCP tools: auto-initializes your project, sets session rules (deep query by default, knowledge-graph-first). |

The MCP server provides the **capabilities**. The skill defines the **behavior**. Users install both for the full experience, but the MCP tools work standalone too.

## Quick Start

### 1. Install the SaaS Server

```bash
git clone https://github.com/brandonzyy/manon-server.git
cd manon-server
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Start the Server

```bash
# Set admin secret (required for admin operations)
export SAAS_ADMIN_SECRET="your-secret-here"

# Start on port 3700 (default)
python -m saas
```

Or with Docker:

```bash
docker compose up -d
```

### 3. Register & Get API Key

```bash
curl -X POST http://localhost:3700/api/v1/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my-team"}'
# Returns: {"api_key": "msk_xxx", "tenant_id": "xxx", "tier": "free"}
```

### 4. Set Up the MCP Client (Claude Code)

Install MCP client dependencies:

```bash
cd manon-mcp
pip install -r requirements.txt
```

Add the MCP server to your Claude Code config (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "manon": {
      "command": "python",
      "args": ["/path/to/manon-mcp/server.py"],
      "env": {
        "MANON_API_KEY": "msk_xxx",
        "MANON_API_URL": "http://your-server:3700"
      }
    }
  }
}
```

### 5. Install the `/manon` Skill (Recommended)

The `/manon` slash command provides one-step project initialization and activates Manon mode with deep query defaults.

Create the skill directory and file:

```bash
mkdir -p ~/.claude/skills/manon
```

Write `~/.claude/skills/manon/SKILL.md`:

```markdown
---
name: manon
description: /manon — Enter Manon mode, initialize project knowledge graph
user_invocable: true
---

# Manon Mode

## Initialization

1. Call `manon_init` with the current working directory
2. If repo exists and indexed → show graph stats
3. If repo exists but not indexed → poll `manon_index_status` until done
4. If repo doesn't exist → auto-create and trigger indexing
5. Call `manon_config` to show current config
6. Announce Manon mode is active

## Rules (active for entire session)

- All code understanding queries use `manon_deep_query` (not `manon_search`)
- Call graph / dependency queries use `manon_graph`
- Impact analysis uses `manon_impact`
- Always query the knowledge graph before using Grep/Glob
```

Then in Claude Code, type `/manon` to activate — it auto-registers your project and enables knowledge-graph-first querying.

### For Cursor

Add to Cursor's MCP settings (Settings → MCP Servers):

```json
{
  "manon": {
    "command": "python",
    "args": ["/path/to/manon-mcp/server.py"],
    "env": {
      "MANON_API_KEY": "msk_xxx",
      "MANON_API_URL": "http://your-server:3700"
    }
  }
}
```

## How It Works

### `/manon` Workflow

```
You type: /manon
    │
    ▼
manon_init(project_path=".")
    │
    ├─ Project not registered → create repo + scan + index
    ├─ Registered but not indexed → poll until ready
    └─ Registered and indexed → show stats, enter Manon mode
    │
    ▼
Manon mode active — all code queries go through the knowledge graph
```

### Local Project Sync (Edge-Cloud)

For local projects (not hosted on GitHub), Manon performs client-side AST extraction:

1. MCP client scans your local files using `codeindex`
2. Parses AST (functions, classes, imports, calls) locally
3. Computes file hashes for incremental detection
4. Uploads only changed files' AST + source to the server
5. Server builds knowledge graph (entities, relations, vectors)

This means your unpushed code is always visible to the knowledge graph.

## Configuration

### Server Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SAAS_PORT` | `3700` | Server port |
| `SAAS_DB_PATH` | `./saas.db` | SQLite database path |
| `SAAS_REPOS_DIR` | `./saas_repos` | Git clone directory |
| `SAAS_INDEX_DIR` | `./saas_indexes` | Knowledge graph index directory |
| `SAAS_EMBEDDING_URL` | `http://117.131.45.179:3002` | Embedding service URL |
| `SAAS_LLM_API_URL` | `https://api.matrixone.online/v1/chat/completions` | LLM API endpoint |
| `SAAS_LLM_MODEL` | `glm-4.7-fp8` | LLM model name |
| `SAAS_LLM_API_KEY` | — | LLM API key |
| `SAAS_ADMIN_SECRET` | — | Admin console password |

### MCP Client Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MANON_API_KEY` | — | API key (from registration) |
| `MANON_API_URL` | — | Override server URL (skip geo-routing) |
| `MANON_API_URL_CN` | `http://117.131.45.179:3700` | China endpoint |
| `MANON_API_URL_INTL` | — | International endpoint |

## MCP Tools

Once configured, the following tools are available in your AI assistant:

### Repository Management

| Tool | Description |
|------|-------------|
| `manon_repos_list` | List all repos and their index status |
| `manon_repos_create` | Add a repo (by git URL or local path) |
| `manon_repos_get` | Get repo details |
| `manon_repos_delete` | Delete a repo |
| `manon_init` | Auto-detect and register current project |

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
| `manon_graph` | Query call graphs and dependencies for a symbol |
| `manon_impact` | Analyze impact of recent commits |
| `manon_deep_query` | Multi-round deep analysis with LLM reasoning |

### Utilities

| Tool | Description |
|------|-------------|
| `manon_config` | Show current configuration |
| `manon_account` | Show account info and quota |
| `manon_usage` | View API usage statistics |
| `manon_embedding` | Generate text embeddings |

## Usage Examples

### Quick Start with `/manon` (Claude Code)

```
> /manon
Manon mode activated. Project "my-project" indexed: 142 entities, 89 relations, 256 chunks.

> How does the authentication work?
(Manon automatically uses deep_query to search the knowledge graph)

> What would break if I refactor UserService?
(Manon uses impact analysis to find all callers and dependents)
```

### Index a Git Repository

```
> manon_repos_create(name="my-project", git_url="https://github.com/user/repo.git")
> manon_index(repo_id="abc123")
```

### Index a Local Project

```
> manon_init(project_path="/path/to/my-project")
# Automatically scans, parses AST, and uploads to server
```

### Search Code

```
> manon_search(repo_id="abc123", query="user authentication flow")
> manon_search(repo_id="abc123", query="database connection handling")
```

### Analyze Dependencies

```
> manon_graph(repo_id="abc123", symbol="UserService", depth=2)
```

### Check Impact of Changes

```
> manon_impact(repo_id="abc123", commit="HEAD")
```

## API Reference

Base URL: `http://localhost:3700/api/v1`

All endpoints require `X-API-Key` header.

### Repos

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/repos` | Create repo |
| `GET` | `/repos` | List repos |
| `GET` | `/repos/{id}` | Get repo |
| `DELETE` | `/repos/{id}` | Delete repo |

### Indexing

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/repos/{id}/index` | Trigger indexing |
| `GET` | `/repos/{id}/index-status` | Check status |
| `POST` | `/repos/{id}/push-update` | Incremental update |
| `POST` | `/repos/{id}/sync-ast` | Upload local AST data |

### Query

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/repos/{id}/search` | Semantic search (`?q=...&top_k=10`) |
| `GET` | `/repos/{id}/graph` | Graph traversal (`?symbol=...&depth=1`) |
| `GET` | `/repos/{id}/impact` | Impact analysis (`?commit=HEAD`) |
| `POST` | `/repos/{id}/deep-query` | Multi-round deep query |

### Account

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/register` | Self-service registration |
| `GET` | `/account` | Account info |
| `GET` | `/usage` | Usage statistics |

## Tier Limits

| Feature | Free | Pro | Enterprise |
|---------|------|-----|------------|
| Repos | 2 | 20 | Unlimited |
| API rate (req/min) | 30 | 300 | 3000 |
| Deep queries/day | 10 | Unlimited | Unlimited |

## Development

```bash
# Run server in development
python -m saas

# Run with auto-reload (not recommended for production)
uvicorn saas.main:app --host 0.0.0.0 --port 3700 --reload

# Admin console
open http://localhost:3700/admin-console
```

## License

MIT
