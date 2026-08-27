# Health Score → Layer Mapping 与 Principle Taxonomy

## Health Score → Layer Mapping

Use `dao-scan.py context` scores to focus the inquiry — not to dictate what to fix (coupling may be by design).

| Dimension | Score < threshold | Investigate |
|-----------|-------------------|-------------|
| MC 模块耦合 | < 9 | Cross-module deps — is this intentional architecture or accidental? |
| CD 循环依赖 | < 10 | C7 or A1 — architectural cycle |
| FI 扇入集中度 | < 9 | Hot modules taking too much responsibility — M1 or A1 |
| DC 死代码 | < 10 | Likely C4 candidates — verify with `manon_graph` callers |
| FS 函数复杂度 | < 9 | Oversized functions — C-layer complexity |
| TD 技术债务 | < 9 | TODOs, any_count — C-layer debt |
| MF 模块碎片化 | < 9 | Too many tiny modules or deep paths — A2, C2, C3 |
| RE 间接层密度 | < 9 | Barrel re-exports or single-impl interfaces — C1, C6, A3 |

---

## Principle Taxonomy

Classification vocabulary for findings. Assign a code when recording issues.

**Architecture (A)** — system-level structure:
- A1 Unnecessary layers · A2 Over-modularization · A3 Premature generalization
- A4 Over-decoupling · A5 Config complexity · A6 Event system overkill · A7 Over-patterning

**Module (M)** — module responsibilities and boundaries:
- M1 Feature bloat · M2 Unclear boundaries · M3 Duplication · M4 Excessive dependencies

**Code (C)** — file and function level:
- C1 Indirection/barrel · C2 Over-fragmentation · C3 Deep directories · C4 Dead code
- C5 Split by tech layer · C6 Unnecessary abstraction · C7 Circular deps · C8 Low cohesion

---
