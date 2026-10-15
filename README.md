<div align="center">

# Manon

### Your Codebase, as a Local Knowledge Graph

**Pure-local code knowledge graph engine — parse, index incrementally, query semantically, analyze impact. No server, no database service.**

[![gates](https://github.com/brandonzyy/manon/actions/workflows/ci.yml/badge.svg)](./.github/workflows/ci.yml)
License: BSL-1.1

</div>

## What it does

Manon parses a repository into an entity/relation graph (tree-sitter AST),
stores it **inside the project** at `.manon/graph/`, and answers questions
about the code:

```bash
# inside your project root
~/manon/manon-kg sync                # incremental index (SHA256 content hashes)
~/manon/manon-kg query "where is the discount logic" -j      # semantic search
~/manon/manon-kg graph discountFor --direction callers       # call relationships
~/manon/manon-kg impact --commit HEAD                        # commit blast radius
~/manon/manon-kg deep-query "what breaks if I change applyDiscount?"  # LLM-guided multi-hop Q&A
~/manon/manon-kg health                                      # 8-dimension code health score
```

## How it works

```
codeindex/       — tree-sitter parsing + language detection
core/            — incremental AST sync (SHA256), contract audit, script classification
matrixone_graph/ — graph build, vector index, query pipeline, CLI (manon-kg)
skills/          — development skills: manon / assurance
```

- **Incremental**: content-hash based — only changed files are re-parsed and
  re-embedded; unchanged files are skipped via mtime+size fast path.
- **Local-first**: the graph lives at `<project>/.manon/graph/` (JSON graph +
  npz vectors). The only network calls are embedding (SiliconFlow
  `BAAI/bge-m3`, key via `MANON_EMBEDDING_API_KEY`) and, for `deep-query`,
  an OpenAI-compatible LLM (default verispring `deepseek-flash`, key via
  `VERISPRING_API_KEY`). All network calls retry with exponential backoff.
- **Curation layer**: pair the graph with a curated `.project-map.md` entry
  table (maintained by your coding agent) — the map is the task-entry cache,
  the graph is the symbol-level source of truth.

## Install

```bash
git clone https://github.com/brandonzyy/manon.git ~/manon
cd ~/manon && ./install.sh          # venv + deps + manon-kg wrapper
export MANON_EMBEDDING_API_KEY=sk-...   # SiliconFlow
cd <your-project> && ~/manon/manon-kg sync && ~/manon/manon-kg status
```

Python 3.10+ required. See [CLAUDE.md](CLAUDE.md) for architecture and gates.

## License

BSL-1.1.
