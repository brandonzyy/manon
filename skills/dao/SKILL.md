---
name: dao
description: 大道至简 - Universal code simplification using Manon knowledge graph (architecture → module → code)
trigger: /dao
---

# 大道至简 (Dao) - The Ultimate Simplicity

**Philosophy**: "大道至简" — The great Dao is simple
**Western parallel**: "Simplicity is the ultimate sophistication" — Leonardo da Vinci

## Prerequisites Check

**FIRST STEP**: Before starting, check if Manon MCP server is available:

1. Try calling `mcp__manon__manon_repos_list`
2. If it fails with "tool not found" or similar:
   - Stop immediately
   - Show installation instructions below
   - Do NOT proceed with simplification

### Manon Installation Instructions

```bash
# Install Manon CLI
pip install manon

# Configure MCP server in ~/.claude/settings/mcp.json:
{
  "mcpServers": {
    "manon": {
      "command": "manon",
      "args": ["mcp"],
      "disabled": false
    }
  }
}

# Restart Claude Code to load MCP server
```

After installation, run `/dao` again.

---

## Usage

**Manual**: `/dao` - Execute one iteration, stop
**Auto**: `/dao auto` - Loop until simplified (max 10 iterations)
**Auto Risk**: `/dao -autorisk` - Loop with medium/high-risk simplifications enabled

---

## Risk Levels

### Low Risk (Default Mode)
- Dead code with zero callers
- Duplicate functions (identical implementation)
- Trivial wrapper functions (one-line return)
- Small file merges (< 50 lines total)
- Empty compatibility shims with no imports

### Medium/High Risk (AutoRisk Mode)
- Deprecated compatibility layers (requires updating imports)
- Debug/test scripts in production code (scripts/_*.py)
- Architectural refactoring (> 5 files affected)
- Breaking circular dependencies (requires careful ordering)
- Removing abstractions with multiple call sites

**AutoRisk Mode** (`-autorisk`):
- Automatically handles medium/high-risk simplifications
- Updates imports when removing compatibility layers
- Deletes debug scripts after confirming no production usage
- Performs multi-file refactorings
- Still commits one principle at a time
- Still stops if tests fail

---

## Three-Layer Analysis

**CRITICAL**: Always analyze top-down. Fix architecture before modules, modules before code.

### Layer 1: Architecture (宏观 - 7 principles)

**A1. Eliminate Unnecessary Layers**
- Pattern: Repository → Service → Controller → DTO → Entity for simple CRUD
- Detection: `mcp__manon__manon_search "repository service controller layer"` + count layers
- Action: Collapse layers that only forward calls

**A2. Reduce Over-Modularization**
- Pattern: 10 packages for a CLI tool
- Detection: `mcp__manon__manon_search "package module boundary"` + module count vs features
- Action: Merge related modules

**A3. Remove Premature Generalization**
- Pattern: Plugin system with 1 plugin
- Detection: `mcp__manon__manon_search "plugin extensible generic"` + check git history
- Action: Replace with concrete implementation

**A4. Simplify Over-Decoupling**
- Pattern: DI container for 3 classes
- Detection: `mcp__manon__manon_search "interface inject dependency"` + interface/impl ratio
- Action: Use direct imports

**A5. Reduce Configuration Complexity**
- Pattern: 100 config options for simple app
- Detection: `mcp__manon__manon_search "config option setting"` + usage analysis
- Action: Hard-code defaults

**A6. Eliminate Unnecessary Event Systems**
- Pattern: Event bus for A→B calls
- Detection: `mcp__manon__manon_search "event emit subscribe"` + event vs direct call ratio
- Action: Replace with direct calls

**A7. Identify Over-Patterning**
- Pattern: Factory of Factories
- Detection: `mcp__manon__manon_search "factory builder strategy pattern"` + pattern depth
- Action: Replace with direct implementation

### Layer 2: Module (中观 - 4 principles)

**M1. Eliminate Feature Bloat**
- Pattern: utils.ts with 50 functions
- Detection: `mcp__manon__manon_search "utils helpers common"` + export count > 20
- Action: Split by domain, delete unused

**M2. Clarify Module Boundaries**
- Pattern: Overlapping responsibilities
- Detection: `mcp__manon__manon_graph direction=both depth=2` + circular deps
- Action: Redraw boundaries

**M3. Deduplicate Functionality**
- Pattern: 3 HTTP clients
- Detection: `mcp__manon__manon_search "similar duplicate redundant"`
- Action: Keep best, delete others

**M4. Reduce Module Dependencies**
- Pattern: Imports from 20 modules
- Detection: `mcp__manon__manon_graph direction=callers` + import count
- Action: Inline small deps

### Layer 3: Code (微观 - 8 principles)

**C1. Reduce Indirection**
- Pattern: Barrel files (index.ts re-exports)
- Detection: `mcp__manon__manon_search "index export re-export barrel"`
- Action: Inline or remove

**C2. Avoid Over-Fragmentation**
- Pattern: Single-function files (< 50 lines, 1 export)
- Detection: `mcp__manon__manon_search "single function small file"`
- Pre-merge validation (ALL three must pass — skip if any fails):
  1. **Git history check**: `git log --oneline -10 -- <file>` → if file was intentionally created or split in recent commits, SKIP (do not undo a deliberate design decision)
  2. **Operation type parity**: use `manon_graph` to verify both files are the same op type (query vs mutation); do NOT merge a mutation file into a query file or vice versa
  3. **Name accuracy after merge**: confirm the target file name still accurately describes all content after merge; if not, rename target as part of the same commit
- Action: Merge into semantically compatible target (max 1 C2 merge per auto iteration)

**C3. Reduce Directory Depth**
- Pattern: Single-file directories, > 4 levels nesting
- Detection: Directory structure analysis
- Action: Flatten structure

**C4. Remove Dead Code**
- Pattern: Zero callers
- Detection: `mcp__manon__manon_graph symbol="<name>" direction=callers`
- Action: Delete completely (no comments)

**C5. Merge Related Code**
- Pattern: Split by tech layer not domain
- Detection: `mcp__manon__manon_search "types interfaces separated"`
- Action: Group by domain

**C6. Simplify Abstractions**
- Pattern: Interface with single implementation
- Detection: `mcp__manon__manon_search "interface abstract generic"`
- Action: Collapse to concrete

**C7. Reduce Dependencies**
- Pattern: Circular dependencies
- Detection: `mcp__manon__manon_graph direction=both depth=2`
- Action: Break cycles

**C8. Increase Cohesion**
- Pattern: Unrelated code in same module (e.g. mutations mixed into query files, config mixed into business logic)
- Detection: For each file, ask "can this file's responsibility be described in one sentence?" — if not, it has cohesion debt
- Reverse detection (C2 correction): after any C2 merge, re-check the target file with `manon_search`; if it now contains mixed op types or unrelated concerns, treat as C8 violation and prioritize fixing it next iteration
- Action: Split unrelated code into separate files; rename files whose names no longer match their content

---

## Execution Flow

### Each Iteration

1. **Check Manon**: `mcp__manon__manon_repos_list` (if fails: show install instructions and STOP)
2. **Init if needed**: If repo not found, run `mcp__manon__manon_init`
3. **Analyze**: Search Layer 1 → Layer 2 → Layer 3 patterns; also check for open C_PENDING items from previous iterations
4. **Select**: Pick highest impact + lowest risk + lowest effort; C_PENDING items from prior merges take priority over new C2 candidates
5. **Validate** (required before any file merge or delete):
   - C2 merges: run the three-question gate (git history / op type parity / name accuracy)
   - Any deletion: confirm zero callers via `manon_graph`
   - Skip and pick next candidate if validation fails — never force through
6. **Execute**: Read → Simplify → Commit → `mcp__manon__manon_push_update`
7. **Post-check** (after every merge commit): scan the target file for complexity debt:
   - Any function with ≥ 6 parameters → mark as C_PENDING (parameter complexity)
   - Any function > 60 lines → mark as C_PENDING (function body complexity)
   - If C_PENDING found: note in Report as "⚠ 复杂度未消化", carry forward to next iteration
8. **Report**: Show progress
9. **Loop** (auto mode only): Continue or stop

### Auto Mode Logic

```
iteration = 0
while iteration < 10:
  analyze()
  if no_opportunities:
    report("✓ Simplified")
    break
  execute_one()
  iteration++
```

---

## Output Format

```
## 大道至简 Report [Iteration X/10]

**Principle**: [e.g., A1, M3, C1]
**Pattern**: [what found]
**Action**: [what done]
**Files**: [list]
**Impact**: [-X lines, -Y files]
**Commit**: [hash]
**Complexity debt**: [⚠ 复杂度未消化: <description> | ✓ clean]

[Auto: "Continuing..." OR "✓ Done"]
[Manual: "Run /dao to continue"]
```

---

## Constraints

1. Never break functionality
2. One principle per commit
3. Update Manon after each change
4. Stop if tests fail
5. **Default mode**: Ask before risky changes (> 5 files)
6. **AutoRisk mode**: Execute risky changes automatically, but verify safety first
7. **One C2 merge per iteration**: Never batch-merge multiple small files in a single auto run — each merge needs its own validation
8. **Fewer files ≠ simpler**: A merge is only valid if the result can be described in one sentence; if not, it introduced complexity debt, not simplicity

---

## Success Criteria

- No barrel files (except public API)
- No single-function files (except entry points)
- No single-file directories
- No dead code
- No circular deps
- File size: 100-300 lines
- Directory depth ≤ 4
- High cohesion

---

## Instructions for Claude

**CRITICAL FIRST STEP**: Check Manon availability by calling `mcp__manon__manon_repos_list`. If it fails, show installation instructions and STOP.

**Manual Mode** (`/dao`):
- Execute ONE iteration
- Stop and wait

**Auto Mode** (`/dao auto`):
- Loop until simplified or max 10 iterations
- Report after each iteration
- Stop on: no opportunities OR max iterations OR error
- Skip medium/high-risk opportunities (ask user first)
- Max 1 C2 merge per iteration — never batch
- Carry C_PENDING complexity debt forward; resolve before taking new C2 candidates

**AutoRisk Mode** (`/dao -autorisk`):
- Loop until simplified or max 10 iterations
- Report after each iteration
- Stop on: no opportunities OR max iterations OR error
- **Execute medium/high-risk simplifications automatically**:
  - Update imports when removing deprecated compatibility layers
  - Delete debug scripts after verifying no production usage via `manon_graph`
  - Perform multi-file refactorings (but still one principle per commit)
  - Break circular dependencies with careful analysis
- Still verify safety before each risky change
- Still stop if tests fail

**Priority**: Layer 1 > Layer 2 > Layer 3

**Manon First**: Always use Manon MCP tools (mcp__manon__*), never Grep/Glob

**Small Steps**: One principle, one commit, ralphy philosophy

**Quality > Speed**: Fight entropy, leave codebase better
