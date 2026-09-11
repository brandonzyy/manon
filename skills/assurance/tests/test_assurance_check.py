#!/usr/bin/env python3
"""assurance_check.py 的判据测试 —— 判据实现只有一份，在 ../scripts/ 下。

**为什么这个文件必须存在**：本工具的全部价值在于它的读数可信。而 2026-08-26 实测，
它对 Agents-verispring 的 17 格误报 9 格，另有 1 格状态碰巧对而证据是假的
（打「无钩子」，实际装了一套）。一个把达标读成「几乎没装」的体检工具比没有更贵——
它的建议里有一条会把达标的仓库**改坏**（「建 docs/incidents/」，而那个仓的文档治理
规范明令不设该域，照做即造出第二事实源）。同一轮还发现它对 CaseOS 给了一格假绿：
`mutmut` 唯一一次出现是在一行注释里，句意还是「本镜像不含它」。

**判据的形状决定了测法**：本工具每一条判据都是结构性断言（「这个词出现在执行器面
上」「strict 沿 extends 链是开的」「登记指向真实存在的文档」）。按《构建发布长期记忆》
第 54 条，结构性断言的实测是**反向的**——保持结构完好跑一遍只证明相容，必须把结构
拆掉看它红。所以下面每条修复都成对出现：一个该绿的现场，一个「把修好的那一处拆掉
就必须红」的现场。放宽类的修复还多一条：**它不许把 CONFIGURED_NOT_RUN 顺手抹掉**，
那是本工具存在的头号理由。

不碰网络、不碰任何真实仓库：全部在 tmpdir 里现造。

跑法：python3 -m unittest discover -s <skill>/tests -p 'test_*.py'
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "assurance_check", SCRIPTS / "assurance_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AC = _load()

# git 环境隔离：全局 core.hooksPath 会把本机那套漂移钩子挂到临时仓上。
# 测试要造的是「一个仓库长什么样」，不是「本机装了什么」。
GITENV = {**os.environ,
          "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
          "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

# 语言门槛：Python 段要 ≥3 个 .py，TS 段要 ≥3 个 .ts/.vue。
PY3 = {f"src/m{i}.py": "x = 1\n" for i in range(3)}
TS3 = {f"src/m{i}.ts": "export const x = 1\n" for i in range(3)}


class Fixture(unittest.TestCase):
    def build(self, files: dict) -> Path:
        root = Path(tempfile.mkdtemp(prefix="assurance-test-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for rel, body in files.items():
            q = root / rel
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(body, encoding="utf-8")
        subprocess.run(["git", "-c", "core.hooksPath=", "init", "-q", "-b", "main", str(root)],
                       env=GITENV, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=", "add", "-A"],
                       env=GITENV, check=True, capture_output=True)
        return root

    def cells(self, files: dict) -> dict:
        return self.cells_with_root(files)[1]

    def cells_with_root(self, files: dict) -> tuple[Path, dict]:
        root = self.build(files)
        return root, {c.name: c for c in AC.run(AC.Project(root))}

    def cell(self, files: dict, prefix: str):
        cells = self.cells(files)
        for name, c in cells.items():
            if name.startswith(prefix):
                return c
        self.fail(f"没有以 {prefix!r} 开头的格子；实有：{sorted(cells)}")

    def assertStatus(self, files, prefix, expect, msg=""):
        c = self.cell(files, prefix)
        self.assertEqual(c.status, expect,
                         f"{prefix} 期望 {expect} 实为 {c.status}（证据：{c.evidence}）{msg}")
        return c


# ── 修复 1：整行注释不算执行器 ────────────────────────────────────────
class CommentIsNotAnExecutor(Fixture):
    """判例：CaseOS 唯一一处 `mutmut` 在一行注释里，句意还是「本镜像不含它」，
    工具却给了「变异测试 (Python) OK」。放宽执行器面之后这类假绿会成倍出现。"""

    def test_comment_line_is_not_an_executor(self):
        self.assertStatus({**PY3, "Makefile": "# 本镜像刻意不含 ruff\nall:\n\techo hi\n"},
                          "lint (Python", AC.MISSING)

    def test_same_line_without_comment_is(self):
        # 与上一条只差一个 `#`。少了这条对照，上一条的绿可能来自别的原因。
        self.assertStatus({**PY3, "Makefile": "lint:\n\truff check .\n"},
                          "lint (Python", AC.OK)

    def test_docstring_prose_is_not_an_executor(self):
        # docstring 绕过整行注释剥离。判例（就在修这一轮时撞到的）：新加的门禁
        # `test_assurance_declarations.py` 登记进清单、因而上了执行器面，而它的模块
        # docstring 里写着「Stryker 一进 devDependencies…」——**一句解释「为什么没装」
        # 的话把「装了」那一格点绿了**，还是自己刚放宽的面引进来的。
        # 夹具刻意**不叫** test_*.py：登记项是判据自己的测试时只跟一跳、不取正文
        # （见修复 11）。用 test_ 命名会让这条靠那条新规则通过，于是「剥 docstring」
        # 被拆掉也不红——正是第 54 条「验了另一件事」。
        self.assertStatus({**TS3,
                           "q/mutate_gate.py": '"""Stryker 一进 devDependencies 就每次都要装。"""\n',
                           "deploy/gates.txt": "q/mutate_gate.py|某门禁\n"},
                          "变异测试 (TS)", AC.MISSING)

    def test_string_literal_in_code_still_counts(self):
        # 只剥 docstring，不剥普通字面量：`["vulture", …]` 里的字面量正是执行本身。
        # 少了这条，上一条可以靠「把所有字符串都剥掉」通过——那会把真的调用一起剥掉。
        self.assertStatus({**PY3,
                           "q/dead_gate.py": 'import subprocess\nsubprocess.run(["vulture", "."])\n',
                           "deploy/gates.txt": "q/dead_gate.py|死代码门禁\n"},
                          "死代码 (Python)", AC.OK)

    def test_inline_hash_is_not_stripped(self):
        # 行内 `#` 是合法代码（`$#`、`${x#y}`、URL 的 fragment）。
        # 行内剥离会把真的调用一起剥掉——那是拿一类假绿换一类假红。
        self.assertStatus({**PY3, "scripts/go.sh": 'echo "${p#pre}" && ruff check .\n'},
                          "lint (Python", AC.OK)


# ── 修复 2：执行器面不只有仓根 ────────────────────────────────────────
class ExecutorFaceIsNotOnlyRepoRoot(Fixture):
    """判例：Agents-verispring 没有仓根 package.json，`"coverage": "vitest run --coverage"`
    在 frontend/package.json 里，于是「覆盖率 (TS)」报缺。"""

    def test_subdir_package_json_counts(self):
        self.assertStatus({**TS3, "frontend/package.json":
                           '{"scripts": {"coverage": "vitest run --coverage"}}'},
                          "覆盖率 (TS)", AC.OK)

    def test_subdir_scripts_sh_counts(self):
        self.assertStatus({**PY3, "backend/scripts/suite.sh": "python -m coverage run x.py\n",
                           "deploy/coveragerc": "[run]\nbranch = True\n"},
                          "覆盖率 (Python)", AC.OK)

    def test_path_off_the_face_does_not_count(self):
        # 同样的文本挪到不在执行器面上的路径 → 必须回到红。
        # 没有这条，上面两条证明的只是「文本存在」，不是「它在执行器面上」。
        self.assertStatus({**TS3, "frontend/notes/howto.sh": "vitest run --coverage\n"},
                          "覆盖率 (TS)", AC.MISSING)


# ── 修复 3：清单登记的检查器本身就是执行器面 ──────────────────────────
class ManifestRegisteredCheckersAreExecutors(Fixture):
    """判例：`deploy/quality/test_lint_ratchet.py` 登记在 static_gates.txt 里、每次提交
    都跑，而 `ruff` 三个字只写在它自己的源码里。漏掉这一跳，一份「全部登记、三个
    执行器共读」的 L1 会被读成一格都没有。"""

    BASE = {**PY3,
            "q/test_dead.py": "import dead_check\n",
            "q/dead_check.py": "CMD = ['vulture', '--min-confidence', '80']\n"}

    def test_registered_checker_is_on_the_face(self):
        self.assertStatus({**self.BASE, "deploy/gates.txt": "q/test_dead.py|死代码棘轮\n"},
                          "死代码 (Python)", AC.OK)

    def test_unregistered_file_is_not(self):
        # 同样的文件，只是清单里没登记它 → 必须红。
        # 这条钉住「绿是**登记**带来的」，不是「文件恰好存在」带来的。
        self.assertStatus({**self.BASE, "deploy/gates.txt": "q/other.py|别的\n"},
                          "死代码 (Python)", AC.MISSING)

    def test_exempt_line_also_counts(self):
        # 豁免行说的是「由别人执行」，不是「不执行」。
        self.assertStatus({**self.BASE,
                           "deploy/gates.txt": "# exempt:q/test_dead.py|由 CI 执行\n"},
                          "死代码 (Python)", AC.OK)

    def test_one_import_hop_is_followed(self):
        # 登记的常是一层薄壳，真正调工具的是它 import 的同目录模块。
        self.assertStatus({**self.BASE, "deploy/gates.txt": "q/test_dead.py|死代码棘轮\n"},
                          "死代码 (Python)", AC.OK)

    def test_second_hop_is_not_followed(self):
        # **只跟一跳**。跟深了等于把半个仓库算成执行器面，那就轮到假绿了。
        # 这条是给以后随手把它改成递归的人留的。
        self.assertStatus({**PY3,
                           "q/test_dead.py": "import shim\n",
                           "q/shim.py": "import deeper\n",
                           "q/deeper.py": "CMD = ['vulture']\n",
                           "deploy/gates.txt": "q/test_dead.py|死代码棘轮\n"},
                          "死代码 (Python)", AC.MISSING)


# ── 修复 4：配置 × 执行器 的四格，两条对角线不对称 ────────────────────
class ConfigTimesExecutor(Fixture):
    """「没有配置文件」不等于「没有这一层」——判例：本仓扫描参数刻意写死在检查器源码
    里，为的是跨机同结果。但反向必须纹丝不动：**有配置无执行器仍然是 CONFIGURED_NOT_RUN**。"""

    def test_no_config_but_executed_is_ok(self):
        self.assertStatus({**PY3, "Makefile": "t:\n\tmypy src\n"},
                          "类型检查 (Python", AC.OK)

    def test_config_without_executor_stays_not_run(self):
        # 放宽最容易顺手抹掉的就是这一格——本工具存在的头号理由。
        self.assertStatus({**PY3, "mypy.ini": "[mypy]\nstrict = True\n"},
                          "类型检查 (Python", AC.NOT_RUN)

    def test_neither_is_missing(self):
        self.assertStatus(dict(PY3), "类型检查 (Python", AC.MISSING)

    def test_coveragerc_without_dot_is_found(self):
        # `coveragerc`（不带点）是 `--rcfile=` 的常用命名，与 `.coveragerc` 等价。
        # 只认带点那个，等于按文件名的装饰判存在性。
        c = self.assertStatus({**PY3, "deploy/quality/coveragerc": "[run]\nbranch = True\n"},
                              "覆盖率 (Python)", AC.NOT_RUN)
        self.assertIn("coveragerc", c.evidence)


# ── 修复 5：tsconfig 的 strict 沿 extends 链判 ────────────────────────
class SupplyChainCells(Fixture):
    """六件套第六件的格子（2026-08-27 加）。成对出现，形状与 ConfigTimesExecutor 同：
    执行器点名 → OK；配置在无执行器 → CONFIGURED_NOT_RUN（有配置文件的密钥格）；
    全无 → MISSING。"""

    def test_deps_executor_named_is_ok(self):
        self.assertStatus({**PY3, "Makefile": "t:\n\tpip-audit -r requirements.txt\n"},
                          "依赖审计 (Python)", AC.OK)

    def test_deps_neither_is_missing(self):
        self.assertStatus(dict(PY3), "依赖审计 (Python)", AC.MISSING)

    def test_deps_ts_executor_named_is_ok(self):
        self.assertStatus({**TS3, "Makefile": "audit:\n\tnpm audit --audit-level=high\n"},
                          "依赖审计 (TS)", AC.OK)

    def test_secrets_config_without_executor_stays_not_run(self):
        # 放宽最容易顺手抹掉的一格：gitleaks.toml 在、没人跑它——本工具存在的头号理由。
        c = self.assertStatus({**PY3, ".gitleaks.toml": "[allowlist]\npaths:\n"},
                              "密钥扫描", AC.NOT_RUN)
        self.assertIn("gitleaks", c.evidence)

    def test_secrets_executor_named_is_ok(self):
        self.assertStatus({**PY3, "Makefile": "t:\n\tdetect-secrets scan --all-files\n"},
                          "密钥扫描", AC.OK)


class TsconfigStrictFollowsExtends(Fixture):
    """判例：`frontend/tsconfig.json` 里根本没有 strict 字样，它 extends
    `@vue/tsconfig/tsconfig.json`，strict 在那份基座里。只在本文件里 grep 一个
    `"strict": true`，会把一份完全严格的配置读成「2 个 tsconfig，0 个 strict」。"""

    RUN = {"frontend/package.json": '{"scripts": {"type-check": "vue-tsc --noEmit"}}'}

    def test_strict_via_relative_extends(self):
        self.assertStatus({**TS3, **self.RUN,
                           "frontend/base.json": '{"compilerOptions": {"strict": true}}',
                           "frontend/tsconfig.json": '{"extends": "./base.json"}'},
                          "类型检查 (TS)", AC.OK)

    def test_strict_via_node_modules_package(self):
        self.assertStatus({**TS3, **self.RUN,
                           ".gitignore": "node_modules/\n",
                           "frontend/node_modules/@vue/tsconfig/tsconfig.json":
                               '{"compilerOptions": {"strict": true}}',
                           "frontend/tsconfig.json": '{"extends": "@vue/tsconfig/tsconfig.json"}'},
                          "类型检查 (TS)", AC.OK)

    def test_own_false_overrides_strict_base(self):
        # 子覆盖父，与 tsc 一致。少了这条，上面两条可能只是「链上任意一处有 true」。
        self.assertStatus({**TS3, **self.RUN,
                           "frontend/base.json": '{"compilerOptions": {"strict": true}}',
                           "frontend/tsconfig.json":
                               '{"extends": "./base.json", "compilerOptions": {"strict": false}}'},
                          "类型检查 (TS)", AC.MISSING)

    def test_unresolvable_extends_says_unresolved_not_zero(self):
        # 本次修复的要害：**「查不到」不许印成「没有」**。格子仍然红（不可核对的
        # strict 不算保障），但证据必须说实话，否则下一个人会照着它去改一份
        # 已经严格的配置。
        c = self.assertStatus({**TS3, **self.RUN,
                               "frontend/tsconfig.json": '{"extends": "@vue/tsconfig/tsconfig.json"}'},
                              "类型检查 (TS)", AC.MISSING)
        self.assertIn("未解析", c.evidence)
        self.assertNotIn("0 个 strict", c.evidence)

    def test_strict_in_a_comment_does_not_count(self):
        # tsconfig 是 JSONC，`@vue/tsconfig` 的基座里就有注释写着 strict。
        self.assertStatus({**TS3, **self.RUN,
                           "frontend/tsconfig.json":
                               '{\n  // "strict": true —— 以后再开\n  "compilerOptions": {}\n}'},
                          "类型检查 (TS)", AC.MISSING)


# ── 修复 6：一次性动作的结论登记 ──────────────────────────────────────
class OneShotDeclaration(Fixture):
    """变异测试这一格问的不是「装了没」，是「跑过没」——而它按判据就是一次性的。
    把 Stryker 钉进 devDependencies 反而是错的（此后每次构建镜像都要装它），
    于是**正确的做法在工具眼里长得和什么都没做一模一样**。"""

    DOC = {"docs/变异测试首轮结论.md": "# 结论\n\n杀死率 82%\n"}
    TODAY = dt.date.today().isoformat()

    def test_valid_declaration_greens_the_cell(self):
        c = self.assertStatus({**PY3, **self.DOC,
                               ".assurance-oneshot.txt":
                                   f"mutation-python|docs/变异测试首轮结论.md|{self.TODAY}\n"},
                              "变异测试 (Python)", AC.OK)
        self.assertIn("仅确认结论文档存在", c.evidence)

    def test_missing_doc_keeps_the_cell_red(self):
        cells = self.cells({**PY3, ".assurance-oneshot.txt":
                            f"mutation-python|docs/没有这篇.md|{self.TODAY}\n"})
        self.assertEqual(cells["变异测试 (Python)"].status, AC.MISSING)
        self.assertIn("一次性动作登记", cells, "坏行必须自己成一行红，不能悄悄失效")

    def test_unknown_key_is_a_bad_line(self):
        cells = self.cells({**PY3, **self.DOC, ".assurance-oneshot.txt":
                            f"mutation-rust|docs/变异测试首轮结论.md|{self.TODAY}\n"})
        self.assertIn("一次性动作登记", cells)
        self.assertEqual(cells["变异测试 (Python)"].status, AC.MISSING)

    def test_bad_date_is_a_bad_line(self):
        cells = self.cells({**PY3, **self.DOC, ".assurance-oneshot.txt":
                            "mutation-python|docs/变异测试首轮结论.md|2026年8月\n"})
        self.assertIn("一次性动作登记", cells)

    def test_future_date_is_a_bad_line(self):
        future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        cells = self.cells({**PY3, **self.DOC, ".assurance-oneshot.txt":
                            f"mutation-python|docs/变异测试首轮结论.md|{future}\n"})
        self.assertIn("一次性动作登记", cells)

    def test_stale_declaration_is_not_ok(self):
        # 一次性 ≠ 一次管到底：代码换过一轮之后，那份结论说的是别的代码。
        old = (dt.date.today() - dt.timedelta(days=AC.ONESHOT_VALID_DAYS + 1)).isoformat()
        c = self.assertStatus({**PY3, **self.DOC, ".assurance-oneshot.txt":
                               f"mutation-python|docs/变异测试首轮结论.md|{old}\n"},
                              "变异测试 (Python)", AC.NOT_RUN)
        self.assertIn("已过去", c.evidence)

    # ── 这一格的判定曾经反过来，那一版有个不该有的梯度 ────────────────
    #
    # 原判据：登记**只在报缺时**兜底，不许把「配了没跑」顶绿——理由是那一格是本工具的
    # 头号目标。这条理由对**日常**检查成立，对**一次性**动作恰好反过来：按规范 §5
    # 变异测试不进钩子、不进日常 CI（要跑整套测试、会继承套件里的每一个 flake），
    # 于是「有配置无执行器」在这一格是设计内的正确形态，不是收据。
    #
    # 后果是一条**奖励删配置**的梯度：留着 `[tool.mutmut]` 得 CONFIGURED_NOT_RUN，
    # 删掉它反而变 MISSING、再被同一条登记兜成 OK。判例见 CaseOS（2026-08-26）。
    #
    # 所以下面这条不钉「是哪个状态」，钉的是**梯度不许存在**：配置在与不在，
    # 拿到的判定必须一样。钉状态的写法两个方向都拦不住——今天把 OK 改回 NOT_RUN
    # 它照样绿，而那正是被推翻的那一版。

    def _ts_cell(self, extra):
        return self.cells({**TS3, **self.DOC, **extra,
                           ".assurance-oneshot.txt":
                               f"mutation-ts|docs/变异测试首轮结论.md|{self.TODAY}\n"}
                          )["变异测试 (TS)"]

    def test_删掉配置不许换来更好的读数(self):
        有配置 = self._ts_cell({"stryker.config.json": '{"mutate": ["src/**"]}'})
        没配置 = self._ts_cell({})
        self.assertEqual(
            有配置.status, 没配置.status,
            f"配置在时判 {有配置.status}、删掉后判 {没配置.status}——"
            "一条门禁如果奖励「删掉可复现的配置」，那是判据错了")

    def test_配置证据不许被登记盖掉(self):
        # 兜底可以改判定，但不许把「配置在哪」这个事实从证据里抹掉：
        # 人打开这一格是去核那一轮结论的，得先知道它是拿什么配置跑的。
        c = self._ts_cell({"stryker.config.json": '{"mutate": ["src/**"]}'})
        self.assertIn("stryker.config.json", c.evidence)
        self.assertIn("仅确认结论文档存在", c.evidence)

    def test_过期登记兜不住有配置的那一格(self):
        # 放宽的是「什么状态可以被兜底」，不是「兜底的门槛」。过期那条路必须照旧红。
        old = (dt.date.today() - dt.timedelta(days=AC.ONESHOT_VALID_DAYS + 1)).isoformat()
        c = self.cells({**TS3, **self.DOC,
                        "stryker.config.json": '{"mutate": ["src/**"]}',
                        ".assurance-oneshot.txt":
                            f"mutation-ts|docs/变异测试首轮结论.md|{old}\n"})["变异测试 (TS)"]
        self.assertEqual(c.status, AC.NOT_RUN)
        self.assertIn("已过去", c.evidence)


# ── 修复 7：缺陷沉降的账不止一种形状 ──────────────────────────────────
class DefectLedgerHasTwoShapes(Fixture):
    """判例：Agents-verispring 的文档治理规范 §1 明令不设 docs/incidents/ 域，事故统一
    追加进一份只增不改的长期记忆。工具照旧报缺并建议「建 docs/incidents/」——
    **一个会把达标仓库改坏的建议，比报错更贵。**"""

    @staticmethod
    def _ledger(n):
        return "# 构建发布长期记忆\n\n" + "".join(f"## {i}. 某次事故\n\n处置。\n\n"
                                                 for i in range(1, n + 1))

    def test_incidents_dir_counts(self):
        self.assertStatus({**PY3, "docs/incidents/a.md": "# a\n", "docs/incidents/b.md": "# b\n"},
                          "事故账", AC.OK)

    def test_single_file_ledger_counts(self):
        c = self.assertStatus({**PY3, "docs/operations/构建发布长期记忆.md": self._ledger(6)},
                              "事故账", AC.OK)
        self.assertIn("构建发布长期记忆.md", c.evidence)

    def test_stub_ledger_does_not_count(self):
        # 空壳不算账。判据仍然可核对：名字要落在那几个词上，**且条目 ≥5**。
        self.assertStatus({**PY3, "docs/operations/构建发布长期记忆.md": self._ledger(2)},
                          "事故账", AC.MISSING)

    def test_unrelated_long_doc_does_not_count(self):
        self.assertStatus({**PY3, "docs/接口说明.md": self._ledger(30)},
                          "事故账", AC.MISSING)


# ── 修复 8：钩子机制的证据要说清在哪儿 ────────────────────────────────
class HookEvidenceMustLocate(Fixture):
    """这一格的**状态**碰巧一直是绿的（只有一套就算唯一），但证据是假的：
    「一套都没有」与「装了一套」印出同一个绿，读的人分不出来。
    证据错了的绿比红更难发现——没有人会去查一个绿格子。"""

    def test_handwritten_hooks_outside_scripts_are_found(self):
        c = self.assertStatus({**PY3,
                               "deploy/release/git_hooks/pre-commit": "#!/bin/sh\nexit 0\n",
                               "deploy/release/install_git_hooks.sh": "#!/bin/sh\ncp -a x y\n"},
                              "钩子机制唯一", AC.OK)
        self.assertIn("deploy/release", c.evidence)

    def test_none_is_distinguishable_from_one(self):
        c = self.assertStatus(dict(PY3), "钩子机制唯一", AC.OK)
        self.assertEqual(c.evidence, "无钩子")

    def test_two_mechanisms_are_flagged(self):
        self.assertStatus({**PY3,
                           "deploy/release/git_hooks/pre-commit": "#!/bin/sh\nexit 0\n",
                           "lefthook.yml": "pre-commit:\n  commands:\n    a:\n      run: echo\n"},
                          "钩子机制唯一", AC.NOT_RUN)


# ── 修复 9：skill 载荷不是本仓的执行器面 ──────────────────────────────
class SkillPayloadIsNotThisReposExecutor(Fixture):
    """判例（`~/.claude` 本身）：`skills/manon/scripts/manon-scan-tests.py` 拼
    `pytest --cov`、`skills/tc/scripts/tc-commit.py` 跑 `bun test --coverage`——
    两条都是**给被扫描的那个项目跑的**，而这个仓自己一行覆盖率都没测过。
    于是「覆盖率 (Python)」是绿的：不是散文当执行，是**别人的执行当自己的**。

    这条在别的仓里从不暴露：同类脚本住在 `.claude/skills/` 下，而 `.claude` 早在
    SKIP 里。只有当被扫的根**自己就是**那个 `.claude` 时它们才浮上来。"""

    PAYLOAD = 'import subprocess\nsubprocess.run(["pytest", "--cov"])\n'

    def test_skill_script_does_not_green_the_cell(self):
        self.assertStatus({**PY3, "skills/tc/scripts/tc-commit.py": self.PAYLOAD},
                          "覆盖率 (Python)", AC.MISSING)

    def test_same_text_outside_skills_still_counts(self):
        # 唯一的差别是路径。少了这条，上一条的红可能来自别的原因
        # （比如 `**/scripts/*.py` 那条 glob 根本没命中）。
        self.assertStatus({**PY3, "tools/scripts/tc-commit.py": self.PAYLOAD},
                          "覆盖率 (Python)", AC.OK)

    def test_registering_it_in_the_manifest_is_not_an_escape_hatch(self):
        # 清单登记不是逃生口：skill 载荷即使被登记，跑的仍然是**别的仓**。
        # 没有这条，只要把它写进 gates.txt 就能把格子点绿。
        self.assertStatus({**PY3, "skills/tc/scripts/gate.py": self.PAYLOAD,
                           "deploy/gates.txt": "skills/tc/scripts/gate.py|覆盖率门禁\n"},
                          "覆盖率 (Python)", AC.MISSING)

    def test_skill_python_still_counts_as_code(self):
        # 只挡执行器面，**不挡扫描**：这些文件仍然是本仓的代码，仍然该被 lint。
        # 少了这条，「把 skills/ 整个塞进 SKIP」也能让上面三条通过，
        # 而那会让 19 个 .py 从每一格的分母里静悄悄消失。
        c = self.cell({"skills/a/scripts/x.py": "x = 1\n",
                       "skills/b/scripts/y.py": "y = 1\n",
                       "skills/c/scripts/z.py": "z = 1\n"}, "lint (Python")
        self.assertIn("3 文件", c.name)


# ── 修复 11：依赖清单里的一个名字不是执行器 ───────────────────────────
class DependencyListIsNotAnExecutor(Fixture):
    """判例（CaseOS，2026-08-26）：`@stryker-mutator/core` 躺在仓根 devDependencies，
    「变异测试 (TS)」于是报 OK；mutmut 同样只是声明的 dev 依赖，唯一的差别是它写在
    `services/api/pyproject.toml` 的 `[dependency-groups]` 里——那份文件不在执行器面上
    ——「变异测试 (Python)」于是报 CONFIGURED_NOT_RUN。
    **同一个事实拿到两个相反的判定，差别只在依赖清单用的是哪种文件格式。**

    `package.json` 上执行器面是因为它有 `scripts`，devDependencies 只是恰好同住一个
    文件。修的方向是把假绿那一半拉回来——把 pyproject 也拉上执行器面会让假绿对称，
    那是把错的一半推广。
    """

    CFG = {"knip.json": '{"entry": ["src/index.ts"]}'}

    def _pkg(self, obj):
        return {**TS3, **self.CFG, "package.json": json.dumps(obj)}

    def test_装了不等于跑了(self):
        self.assertStatus(self._pkg({"devDependencies": {"knip": "^5.0.0"}}),
                          "死代码 (TS)", AC.NOT_RUN)

    def test_四种依赖段一视同仁(self):
        for key in ("dependencies", "peerDependencies", "optionalDependencies", "overrides"):
            with self.subTest(key=key):
                self.assertStatus(self._pkg({key: {"knip": "^5.0.0"}}),
                                  "死代码 (TS)", AC.NOT_RUN)

    def test_写进_scripts_就算(self):
        # 唯一的差别是它落在哪个段里。少了这条，上面的红可能来自
        # 「`package.json` 整个掉出了执行器面」，而那会把一整批真执行器一起判没。
        self.assertStatus(self._pkg({"scripts": {"deadcode": "knip"}}),
                          "死代码 (TS)", AC.OK)

    def test_剥的只有依赖段(self):
        # husky / lint-staged 与 scripts 一样是**真的执行器声明**，不许被一起剥掉。
        self.assertStatus(self._pkg({"lint-staged": {"*.ts": ["knip"]}}),
                          "死代码 (TS)", AC.OK)

    def test_坏_json_退回原文而不是当空文件(self):
        # 解析不了就别猜：一个语法坏了的 package.json 若被当成空的，
        # 里面所有真执行器**静悄悄**一起消失，而读数只是变绿。
        bad = {**TS3, **self.CFG, "package.json": '{"scripts": {"deadcode": "knip"},,}'}
        self.assertStatus(bad, "死代码 (TS)", AC.OK)


# ── 修复 10：清单里的路径相对谁，取决于谁读它 ─────────────────────────
class ManifestPathsResolveAgainstTheManifestDir(Fixture):
    """判例（`~/.claude`）：`bin/gates.txt` 的执行器 `bin/run_gates.sh` 先 `cd` 到自己
    所在目录再跑，所以清单里写的是 `test_tools_gates.py`——相对**清单所在目录**。
    只按仓根解析时，这份清单登记的每一个检查器都落在执行器面之外，
    一整层 L1 被读成零，而输出还是「什么都没装」。"""

    CHECKER = 'CMD = ["vulture", "."]\n'

    def test_manifest_relative_path_resolves(self):
        self.assertStatus({**PY3, "bin/gates.txt": "dead_gate.py|死代码棘轮\n",
                           "bin/dead_gate.py": self.CHECKER},
                          "死代码 (Python)", AC.OK)

    def test_repo_root_relative_path_still_resolves(self):
        # 两种写法都真实存在（Agents-verispring 的 static_gates.txt 写仓根相对）。
        # 这条钉住上一条不是靠「改成只按清单目录解析」通过的。
        self.assertStatus({**PY3, "bin/gates.txt": "q/dead_gate.py|死代码棘轮\n",
                           "q/dead_gate.py": self.CHECKER},
                          "死代码 (Python)", AC.OK)

    def test_path_that_matches_neither_is_still_missing(self):
        self.assertStatus({**PY3, "bin/gates.txt": "nowhere/dead_gate.py|死代码棘轮\n",
                           "q/dead_gate.py": self.CHECKER},
                          "死代码 (Python)", AC.MISSING)

    def test_one_dot_dot_inside_the_repo_still_resolves(self):
        # `bin/gates.txt` 里写 `../q/x.py` 是**合法**的（上一级正好是仓根）。
        # 这条钉住「拒越界」不是靠「见到 `..` 就放弃」实现的。
        self.assertStatus({**PY3, "bin/gates.txt": "../q/dead_gate.py|死代码棘轮\n",
                           "q/dead_gate.py": self.CHECKER},
                          "死代码 (Python)", AC.OK)

    def test_escaping_the_repo_does_not_silently_fold_back_in(self):
        # 越界一层再往回走，`..` 一路 pop 到空之后，剩下的段会被当成**仓根相对**——
        # 于是 `../../q/x.py` 悄悄变成 `q/x.py`，一条指到仓外的登记反而命中了仓内的
        # 文件。差别只有一个 `..`，而结果从「判不了」变成「绿」。
        #
        # 用「有真文件可命中」的形状写：`../../../etc/hosts` 那种写法拆掉守卫也仍是
        # MISSING（临时仓里根本没有 etc/hosts），变异改不动行为，测试当然不红——
        # 那是长期记忆第 4 条的形状，第一版就是这么写的。
        self.assertStatus({**PY3, "bin/gates.txt": "../../q/dead_gate.py|死代码棘轮\n",
                           "q/dead_gate.py": self.CHECKER},
                          "死代码 (Python)", AC.MISSING)


# ── 修复 11：登记项是判据自己的测试时，只跟一跳，不取正文 ──────────────
class RegisteredTestBodyIsFixtureNotExecution(Fixture):
    """一个检测器的测试必然把它能检测的每个工具名写进夹具与用例名——那是它**检测的
    对象**，不是它执行的东西。判例（`~/.claude`，就在修复 10 落地的那一刻）：
    `test_assurance_check.py` 的夹具里有 `[tool.ruff]`、`python -m coverage run`、
    `mypy`、`vulture`，清单一被正确解析，L1 四格**一起变绿**——比修复 9 修掉的那个
    假绿还多三个。

    真正的调用在它 import 的那个模块里（Agents-verispring 的 `test_lint_ratchet.py`
    里 `ruff` 只出现在一个用例名中，真调用在 `lint_check.py`），而那一跳本来就跟。"""

    def test_tool_name_only_in_the_test_body_does_not_count(self):
        self.assertStatus({**PY3,
                           "q/test_dead.py": 'FIXTURE = {"x.sh": "vulture --min-confidence 80"}\n',
                           "deploy/gates.txt": "q/test_dead.py|死代码棘轮\n"},
                          "死代码 (Python)", AC.MISSING)

    def test_the_import_hop_is_still_followed(self):
        # 排掉的只是**正文**。少了这条，「登记项叫 test_ 就整条跳过」也能让上一条
        # 通过，而那会把修复 3 整条抹掉——薄壳 + 真检查器是最常见的形状。
        self.assertStatus({**PY3, "q/test_dead.py": "import dead_check\n",
                           "q/dead_check.py": 'CMD = ["vulture", "."]\n',
                           "deploy/gates.txt": "q/test_dead.py|死代码棘轮\n"},
                          "死代码 (Python)", AC.OK)

    def test_a_non_test_checker_body_still_counts(self):
        # 只按 `test_` 前缀排，别的登记项纹丝不动。
        self.assertStatus({**PY3, "q/dead_check.py": 'CMD = ["vulture", "."]\n',
                           "deploy/gates.txt": "q/dead_check.py|死代码棘轮\n"},
                          "死代码 (Python)", AC.OK)


# ── 修复 12：同一条调用在 shell 与 argv 里长得不一样 ──────────────────
class ArgvShapedInvocationCounts(Fixture):
    """判例（`~/.claude`，2026-08-26）：覆盖率门禁装好、登记进清单、每次推送都跑，
    这一格仍然报缺——判据只认得出 `python -m coverage run x.py` 这种 **shell 写法**，
    而检查器里真正的调用是 `subprocess.run([py, "-m", "coverage", "run", ...])`，
    中间隔着引号和逗号。

    执行器面一旦包含 Python 检查器（那是本工具刚放宽的面），这个盲点就成了系统性的：
    **放宽了「谁在面上」，却没跟着放宽「调用长什么样」。**"""

    def test_argv_list_form_counts(self):
        self.assertStatus({**PY3, "q/cov_gate.py":
                           'subprocess.run([py, "-m", "coverage", "run", "x.py"])\n',
                           "deploy/gates.txt": "q/cov_gate.py|覆盖率门禁\n"},
                          "覆盖率 (Python)", AC.OK)

    def test_shell_form_still_counts(self):
        # 老写法不许被新写法挤掉。
        self.assertStatus({**PY3, "scripts/go.sh": "python3 -m coverage run x.py\n"},
                          "覆盖率 (Python)", AC.OK)

    def test_the_word_alone_is_not_an_invocation(self):
        # `-m` 那半截是判据的全部重量：只出现「coverage」三个字不算调用，
        # 否则任何一句提到覆盖率的代码都会把这一格点绿。
        self.assertStatus({**PY3, "q/cov_gate.py": 'NOTE = "coverage matters"\n',
                           "deploy/gates.txt": "q/cov_gate.py|门禁\n"},
                          "覆盖率 (Python)", AC.MISSING)


# ── 修复 9：证据里点名的文件必须在仓里 ────────────────────────────────
_HINT = re.compile(r"\[[^\]]*\]|（[^）]*）|←.*$")
# 三支按「长的在前」排：带斜杠的整条路径优先，否则 `deploy/quality/coveragerc`
# 会只被末尾那支认走 `coveragerc`，然后拿一个不存在的相对路径去 exists()。
_PATHY = re.compile(r"(?:[\w.-]+/)+[\w.-]+"
                    r"|[\w.-]+\.[A-Za-z0-9]+"
                    r"|\b(?:Makefile|justfile|Jenkinsfile|coveragerc)\b")


class EvidenceNamesARealFile(Fixture):
    """证据串点名的路径必须在这个仓里存在。

    判例（manon，2026-08-27）：覆盖率那一格 OK，证据是
    `pyproject.toml [addopts --cov]`——manon 里没有 pyproject.toml，真正的命中在
    `.github/workflows/ci.yml`。这一格**状态是对的**，所以三态读数看不出问题；
    只有照着证据去翻那个文件的人才会发现它不在。

    指错文件的绿格比红格更贵：红格会被追，假证据的绿格让人以为自己核过了。
    这条判据抓的是这一整类——凡是从执行器面命中合成出来的证据（没有独立配置文件
    的那几格：--cov / pip-audit / npm audit / mutmut / c8），都由同一个助手产出，
    在这里一次性钉住。
    """

    def assertEvidencePathsExist(self, root: Path, cells: dict):
        checked = 0
        for name, c in cells.items():
            # MISSING 那一档的证据是**反向的**（「未发现 e2e/ 目录」），它点名的路径
            # 本来就该不在场。这条判据管的是「断言在场却不在场」，两者方向相反。
            if c.status == AC.MISSING:
                continue
            for tok in _PATHY.findall(_HINT.sub(" ", c.evidence or "")):
                checked += 1
                self.assertTrue((root / tok).exists(),
                                f"「{name}」的证据点名 {tok!r}，仓里没有这个文件。"
                                f"完整证据：{c.evidence!r}")
        return checked

    def test_合成证据全部指向在场文件(self):
        # 一个把五种「无独立配置文件」的格子全点亮的仓。
        root, cells = self.cells_with_root({
            **PY3, **TS3,
            ".github/workflows/ci.yml": "run: pytest --cov=src\n",
            "Makefile": "a:\n\tpip-audit -r requirements.txt\n\tmutmut run\n"
                        "\tnpm audit --audit-level=high\n\tmypy src\n",
            "package.json": '{"scripts": {"t": "vitest --coverage"}}\n',
        })
        n = self.assertEvidencePathsExist(root, cells)
        self.assertGreaterEqual(n, 5, f"只核到 {n} 条带路径的证据——夹具没点亮那几格，断言会空过")

    def test_配置文件那几格的证据也指向在场文件(self):
        root, cells = self.cells_with_root({
            **PY3, **TS3,
            "deploy/quality/coveragerc": "[run]\nbranch = True\n",
            "mypy.ini": "[mypy]\nstrict = True\n",
            "knip.json": "{}\n",
            ".gitleaks.toml": "[allowlist]\n",
        })
        self.assertGreater(self.assertEvidencePathsExist(root, cells), 0)

    def test_把证据改回写死就必须红(self):
        # 反向：结构完好跑一遍只证明相容，得把修好的那一处拆掉看它红。
        with mock.patch.object(AC, "_executor_evidence",
                               lambda *_a: "pyproject.toml [addopts --cov]"):
            root, cells = self.cells_with_root({
                **PY3, ".github/workflows/ci.yml": "run: pytest --cov=src\n"})
            with self.assertRaises(AssertionError):
                self.assertEvidencePathsExist(root, cells)

    def test_命中了就必须说得出在哪(self):
        # executed() 与 executed_where() 是同一个实现导出的，不许各说各话：
        # 一旦 executed() 说 True 而 where 说 None，那一格会静悄悄从读数里少掉。
        root = self.build({**PY3, "Makefile": "t:\n\tmypy src\n"})
        proj = AC.Project(root)
        self.assertTrue(proj.executed(r"\bmypy\b"))
        self.assertEqual("Makefile", proj.executed_where(r"\bmypy\b"))
        self.assertFalse(proj.executed(r"\bnosuchtool\b"))
        self.assertIsNone(proj.executed_where(r"\bnosuchtool\b"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
