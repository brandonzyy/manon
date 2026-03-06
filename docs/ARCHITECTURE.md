# Manon Architecture

## System Overview

Manon is an AI-powered code intelligence tool with three main components:

```
┌──────────────────────────────────────────────────────┐
│                   IDE Clients                        │
│  (Claude Code, Cursor, Windsurf, Zed, Continue)     │
└────────────────────┬─────────────────────────────────┘
                     │ MCP Protocol
┌────────────────────▼─────────────────────────────────┐
│              Manon MCP Server (mcp/)                 │
│  - Tool handlers (search, graph, impact, etc.)      │
│  - AST synchronization (shared/ast_sync.py)         │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP/REST
┌────────────────────▼─────────────────────────────────┐
│            Manon SaaS Backend (saas/)                │
│  - FastAPI server (:3700)                            │
│  - Knowledge graph (Neo4j-like in-memory)           │
│  - Repository management                             │
│  - Deep query orchestration                          │
└────────────────────┬─────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
┌───────▼────────┐      ┌────────▼─────────┐
│  LLM Service   │      │  Embedding API   │
│ (Ollama/OpenAI)│      │   (:3002)        │
└────────────────┘      └──────────────────┘
```

## Component Details

### 1. MCP Server (`mcp/`)

**Purpose**: IDE integration layer via Model Context Protocol

**Key modules**:
- `run_mcp.py` - MCP server entry point
- `_config.py` - Configuration and geo-routing
- `tools/` - MCP tool implementations
- `hooks/` - Git hooks (pre-push, post-push)

**Responsibilities**:
- Expose tools to IDE (search, graph, impact, etc.)
- Handle AST extraction and synchronization
- Manage project-to-repo mapping

### 2. SaaS Backend (`saas/`)

**Purpose**: Core intelligence engine

**Key modules**:
- `main.py` - FastAPI application
- `graph.py` - Knowledge graph operations
- `deep_query.py` - Multi-round query orchestration
- `code_health.py` - Health score calculation

**Data storage**:
- SQLite (`saas.db`) - Metadata, users, repos
- File system - AST indexes, embeddings

### 3. Shared Modules (`shared/`)

**Purpose**: Common utilities

- `ast_sync.py` - AST extraction via codeindex
- `saas_client.py` - HTTP client for SaaS API

### 4. Web Client (`web/`)

**Purpose**: Browser-based interface (optional, not included in open source release)

**Note**: The web client is an optional component not included in the public repository. Core functionality is accessed through IDE integration via MCP protocol.

## Data Flow

### Initialization Flow

```
1. User runs /manon in IDE
2. MCP calls manon_init(project_path)
3. MCP detects languages → installs parsers
4. MCP scans files → extracts AST
5. MCP uploads AST to SaaS backend
6. SaaS builds knowledge graph
7. MCP polls index status until complete
```

### Query Flow

```
1. User asks question in IDE
2. IDE sends to MCP tool (manon_search/manon_deep_query)
3. MCP forwards to SaaS backend
4. SaaS:
   - Embeds query
   - Searches graph (semantic + structural)
   - (deep_query) Calls LLM to decompose → iterate
5. SaaS returns results
6. MCP formats and returns to IDE
```

### Update Flow

```
1. User commits and pushes code
2. Git pre-push hook triggers
3. Hook scans changed files
4. Hook extracts AST for changed files
5. Hook uploads to SaaS backend
6. SaaS incrementally updates graph
7. Hook fetches and prints health score
```

## Key Design Decisions

### 1. Two-Phase Language Detection

**Problem**: Can't parse files without knowing languages first

**Solution**:
- Phase 1: Quick scan for file extensions
- Phase 2: Install parsers for detected languages
- Phase 3: Deep scan with AST parsing

### 2. Incremental AST Sync

**Problem**: Full repo scan is slow

**Solution**:
- Track file hashes in `~/.manon/projects.json`
- Only parse changed files
- Upload batches of 50 files

### 3. Geo-Aware Routing

**Problem**: China users need different endpoints

**Solution**:
- Detect region via locale/timezone/IP
- Cache in `~/.manon/region.json`
- Route to CN or INTL endpoints

### 4. Embedding-Free Search (Optional)

**Problem**: Embedding service may be unavailable

**Solution**:
- Primary: Semantic search via embeddings
- Fallback: Keyword + graph traversal

## Extension Points

### Adding New Languages

1. Add to `codeindex` FILE_EXTENSIONS
2. Ensure tree-sitter parser available
3. Auto-detection handles the rest

### Adding New Tools

1. Create handler in `mcp/tools/`
2. Register in `run_mcp.py`
3. Add to skill/rules documentation

### Custom Health Metrics

1. Add dimension to `saas/code_health.py`
2. Implement scoring logic
3. Update display format

## Performance Considerations

- **AST parsing**: ~100 files/sec (Python)
- **Graph search**: <100ms for typical queries
- **Deep query**: 10-30s (3 LLM rounds)
- **Index build**: ~1min per 1000 files

## Security

- API keys via environment variables
- No hardcoded credentials
- Rate limiting per tier
- Admin operations require secret
