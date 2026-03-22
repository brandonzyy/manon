#!/usr/bin/env python3
"""Validate /idea output document structure.

Usage: idea-check.py <doc_path>

Checks 5 dimensions and outputs JSON:
  {"pass": true/false, "issues": [...]}
"""
import json
import re
import sys
from pathlib import Path

REQUIRED_SECTIONS = [
    ("需求边界", r"##\s*需求边界|##\s*Requirements"),
    ("技术方案", r"##\s*技术方案|##\s*Technical"),
    ("改动文件", r"##\s*改动文件|##\s*Files|##\s*Changes"),
    ("风险点", r"##\s*风险|##\s*Risk"),
    ("验收标准", r"##\s*验收|##\s*Acceptance"),
]

PLACEHOLDER_PATTERNS = [
    r"TODO", r"TBD", r"待定", r"FIXME", r"待补充", r"\.\.\.",
    r"<[A-Z_]+>",  # <PLACEHOLDER> style
]

PRIORITY_KEYWORDS = {"MUST", "SHOULD", "MAY"}


def check(doc: str) -> dict:
    issues = []

    # 1. Completeness — required sections
    for name, pattern in REQUIRED_SECTIONS:
        if not re.search(pattern, doc, re.IGNORECASE):
            issues.append(f"缺少章节: {name}")

    # 2. Placeholders
    for pat in PLACEHOLDER_PATTERNS:
        matches = re.findall(pat, doc)
        if matches:
            issues.append(f"含占位符: {matches[0]}")
            break

    # 3. Priority tags — at least one MUST
    has_priority = any(kw in doc for kw in PRIORITY_KEYWORDS)
    if not has_priority:
        issues.append("缺少优先级标注 (MUST/SHOULD/MAY)")

    # 4. Scope — check not too broad (more than 20 files listed)
    file_lines = re.findall(r"[-*]\s+`?[\w/]+\.\w+`?", doc)
    if len(file_lines) > 20:
        issues.append(f"范围过大: 涉及 {len(file_lines)} 个文件，建议拆分")

    # 5. YAGNI — flag "future", "later", "extensible" language
    yagni_flags = re.findall(r"未来|以后|扩展性|extensib|future|later phase", doc, re.IGNORECASE)
    if yagni_flags:
        issues.append(f"YAGNI 警告: 含前瞻性描述 '{yagni_flags[0]}'，确认是否当前必需")

    return {"pass": len(issues) == 0, "issues": issues}


def main():
    if len(sys.argv) < 2:
        print("Usage: idea-check.py <doc_path>", file=sys.stderr)
        sys.exit(1)

    doc_path = Path(sys.argv[1])
    if not doc_path.exists():
        print(json.dumps({"pass": False, "issues": [f"文件不存在: {doc_path}"]}))
        sys.exit(1)

    doc = doc_path.read_text(encoding="utf-8")
    result = check(doc)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
