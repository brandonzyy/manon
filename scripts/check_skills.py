#!/usr/bin/env python3
"""skills/ 的两条不变量 —— 每次改 skill 或 install.sh 之后跑一次。

    python3 scripts/check_skills.py        # 退出码 0 通过 / 1 不通过

**为什么需要它**：这两类问题都不会报错，只会静默地不成立。

1. **install.sh 的装块必须覆盖 skill 的每一个文件。** 装块少 cp 一类文件，
   下次安装就把 SKILL.md 覆盖回仓里的版本、而本地那些没被覆盖的文件原样留着，
   变成**没有任何 SKILL.md 指向的孤儿**——看着还在，实际已经没人读，全程零报错。
   实测踩过：dao 与 audit 的装块只 cp SKILL.md，而它们有 references/。

2. **skill 之间的交叉引用必须指向本仓真的有的 skill。** `/assurance` 的卖点是
   「按读数分诊」，分诊表里写一个仓里没有的 `/xxx`，装了的人点过去是空的。
   实测踩过：v1.5.0 发出去时 assurance 三处指向 /retire-checks 而仓里没有它。
"""
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SH = (ROOT / "install.sh").read_text(encoding="utf-8")
names = sorted(p.name for p in SKILLS.iterdir() if p.is_dir())
bad = []

if not names:
    print("❌ skills/ 下一个目录都没有——解析失败必须报错，不能报成「没有问题」")
    sys.exit(1)

# 不变量 1：装块覆盖每个文件
for n in names:
    d = SKILLS / n
    for f in (f for f in d.rglob("*") if f.is_file() and "__pycache__" not in str(f)):
        rel = f.relative_to(d)
        if str(rel) == "SKILL.md":       pat = f'skills/{n}/SKILL.md"'
        elif rel.parts[0] == "references": pat = f'skills/{n}/references/"*.md'
        elif rel.parts[0] == "scripts":    pat = f'skills/{n}/scripts/"*.py'
        else:
            bad.append(f"{n}/{rel}: 不认识的目录层（只支持 references/ 与 scripts/）"); continue
        if pat not in SH:
            bad.append(f"{n}/{rel} —— install.sh 里没有对应的 cp（缺 {pat}）")
    for kind, ext in (("references", "*.md"), ("scripts", "*.py")):
        if f'skills/{n}/{kind}/"{ext}' in SH and not (d / kind).is_dir():
            bad.append(f"{n}: install.sh 要装 {kind}/ 但磁盘上没有 → cp 会报错")

# 不变量 2：交叉引用不悬空
KNOWN_EXTERNAL = {"worktree"}      # 明确不属于本仓、且正文里已说明的
# 文件系统根目录不是 skill 引用。这是**误报**清单，不是覆盖清单——扫描面仍然是全部
# .md 与 .py，一个不漏；只是这几个名字在反引号里恒为路径。放着不管的代价是这条检查
# 每次都吐一条谁也不会去修的噪音，然后整条输出就没人看了。
FS_ROOTS = {"var", "tmp", "usr", "opt", "etc", "bin", "dev", "srv", "mnt", "home", "root", "private"}
for n in names:
    for f in (f for f in (SKILLS / n).rglob("*") if f.suffix in (".md", ".py")):
        for m in re.finditer(r"`/([a-z][a-z0-9-]{2,})`", f.read_text(encoding="utf-8")):
            ref = m.group(1)
            if ref in names or ref in KNOWN_EXTERNAL or ref in FS_ROOTS: continue
            bad.append(f"{n}/{f.relative_to(SKILLS/n)}: 引用 /{ref}，本仓没有这个 skill")

print(f"检查 {len(names)} 个 skill：{' '.join(names)}")
for b in sorted(set(bad)): print("  ❌", b)
print("  ✅ 装块覆盖完整，交叉引用无悬空" if not bad else f"\n{len(set(bad))} 处不成立")
sys.exit(1 if bad else 0)
