# Manon Deployment Guide

## Quick Start

### 1. Prerequisites

- Python 3.10+
- Git
- (Optional) Ollama for local LLM

### 2. Install Client

```bash
# Clone repository
git clone https://github.com/brandonzyy/manon.git
cd manon

# Run installer (auto-detects platform)
bash install.sh        # macOS/Linux
install.bat            # Windows
```

The installer will:
- Create Python virtual environment
- Install dependencies
- Auto-register and get API key
- Configure MCP for detected IDEs (Claude Code, Cursor, Windsurf, etc.)

### 3. Deploy Server (Self-Hosted)

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your configuration
nano .env

# Install server dependencies
pip install -r saas/requirements.txt

# Start server
python -m saas.main
```

Server runs on `http://localhost:3700` by default.

### 4. Configure LLM

**Option A: Ollama (Recommended for local deployment)**

```bash
# Install Ollama: https://ollama.ai
ollama pull qwen2.5-coder:7b

# In .env:
SAAS_LLM_API_URL=http://localhost:11434/v1/chat/completions
SAAS_LLM_MODEL=qwen2.5-coder:7b
SAAS_LLM_API_KEY=
```

**Option B: OpenAI**

```bash
# In .env:
SAAS_LLM_API_URL=https://api.openai.com/v1/chat/completions
SAAS_LLM_MODEL=gpt-4
SAAS_LLM_API_KEY=sk-your-key-here
```

### 5. Configure Embedding Service

**Required for semantic search.** Choose one option:

**Option A: TEI (Text Embeddings Inference) - Recommended**

```bash
# Using Docker
docker run -p 3002:80 --gpus all \
  ghcr.io/huggingface/text-embeddings-inference:latest \
  --model-id BAAI/bge-small-en-v1.5

# In .env:
SAAS_EMBEDDING_URL=http://localhost:3002
```

**Option B: Ollama**

```bash
# Pull embedding model
ollama pull nomic-embed-text

# Run embedding service on port 3002
# (Requires custom wrapper - see docs/embedding-ollama.md)

# In .env:
SAAS_EMBEDDING_URL=http://localhost:3002
```

**Option C: OpenAI**

```bash
# In .env:
SAAS_EMBEDDING_URL=https://api.openai.com/v1
SAAS_EMBEDDING_API_KEY=sk-your-key-here
```

### 6. Initialize Project

In your IDE:

```
# Claude Code / CodeBuddy
/manon

# Cursor / Windsurf
Use manon_init tool in Composer/Cascade
```

## Configuration

### Environment Variables

See `.env.example` for all available options.

Key variables:
- `MANON_API_URL` - Server endpoint
- `MANON_API_KEY` - Authentication key
- `SAAS_LLM_API_URL` - LLM service endpoint
- `SAAS_ADMIN_SECRET` - Admin operations secret

### Multi-User Setup

For team deployment:

1. Deploy server on shared host
2. Set `MANON_API_URL` to server address
3. Each user runs `install.sh` with server URL
4. Manage quotas via admin API

## Troubleshooting

**Connection failed**
- Check server is running: `curl http://localhost:3700/health`
- Verify `MANON_API_URL` in config

**Parser installation failed**
- Ensure internet access for tree-sitter downloads
- Check Python version >= 3.10

**LLM timeout**
- Increase timeout in `saas/config.py`
- Use faster model or local deployment

## Architecture

```
┌─────────────┐
│ IDE Client  │ (MCP)
└──────┬──────┘
       │
┌──────▼──────┐
│ Manon API   │ :3700
│ (FastAPI)   │
└──────┬──────┘
       │
   ┌───┴────┐
   │        │
┌──▼───┐ ┌─▼────────┐
│ LLM  │ │ Embedding│
│:11434│ │  :3002   │
└──────┘ └──────────┘
```

See `docs/ARCHITECTURE.md` for details.
