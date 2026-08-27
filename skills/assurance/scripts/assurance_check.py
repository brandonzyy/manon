#!/usr/bin/env python3
"""工程保障体系合规检查 —— 判据见 references/判据.md

核心判定不是「配了没有」，是「配了**并且**有人跑它」。
CONFIGURED_NOT_RUN 比 MISSING 更危险：它看起来像装了。
（判例：项目甲 的 [tool.ruff] 配置完整、零执行器、二进制未安装，
  反复审计多轮没抓到——审计的眼睛不往配置文件里看。）

用法：
    assurance-check.py <项目路径> [--json] [--include-vendor]
"""
from __future__ import annotations

import ast
import datetime as _dt
import json
import re
import sys
from pathlib import Path

OK, NOT_RUN, MISSING, NA = "OK", "CONFIGURED_NOT_RUN", "MISSING", "N/A"

# 扫描时跳过的目录。vendor 默认跳过：上游自带的 hygiene 脚本不能算你自己的覆盖
# （判例：项目甲 的 knip 只存在于 vendor/<某上游>，自有 463 个 TS 文件零覆盖）。
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
        ".next", ".turbo", "target", ".worktrees", ".pytest_cache", "coverage",
        ".mypy_cache", ".ruff_cache", "archive", ".claude"}
SKIP_VENDOR = {"vendor", "third_party", "vendored"}

# 「执行器面」——一条检查只有出现在这些地方才算真的会被跑。
EXECUTOR_GLOBS = [
    ".github/workflows/*.yml", ".github/workflows/*.yaml",      # GitHub Actions
    ".workflow/*.yml", ".workflow/*.yaml",                      # Gitee Go
    ".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml",
    ".circleci/config.yml", ".woodpecker.yml", ".drone.yml",
    "Makefile", "makefile", "justfile", "Justfile", "Taskfile.yml",
    "package.json", "lefthook.yml", "lefthook.yaml",
    ".pre-commit-config.yaml", ".husky/*",
    "scripts/*.sh", "scripts/*.py", "scripts/*.mjs",
    "scripts/git_hooks/*", "scripts/**/*.sh",
    # 执行器面**不是只有仓根**。上面每一条都隐含「在仓根」，于是任何把工作区放在
    # 子目录的仓库（monorepo、前后端同树）整层执行器对本工具不可见，而输出是「没装」。
    # 判例（项目乙，2026-08-26）：`frontend/package.json` 里写着
    # `"coverage": "vitest run --coverage"`，工具报「覆盖率 (TS) MISSING」——
    # 它只 glob 仓根那一份 package.json，而这个仓根本没有仓根 package.json；
    # 同理 `backend/scripts/run_test_suite.sh` 里的 `-m coverage run` 也看不见，
    # 「覆盖率 (Python)」一并误报。
    "**/package.json", "**/Makefile", "**/justfile",
    "**/scripts/*.sh", "**/scripts/*.py", "**/scripts/*.mjs",
    # 门禁清单放在哪儿都是清单：只在仓根找会漏掉把它收进 deploy/ 之类目录的仓库。
    # 判例：项目乙 的 deploy/release/static_gates.txt 由三个执行器共读，
    # 工具却因为只 glob 仓根而报「门禁清单 MISSING」，连带「执行器覆盖不变量」
    # 也误报——一份完全达标的 L2 被读成零。
    "*gates*.txt", "*gates*.yml", "**/*gates*.txt", "**/*gates*.yml",
    "tox.ini", "noxfile.py",
]

# **skill 载荷不是本仓的执行器面。** `skills/<名>/scripts/` 下的脚本是「给别的仓跑的」
# 代码：它们拼出 `pytest --cov`、`bun test --coverage`、`mutmut` 去驱动**被扫描的那个
# 项目**，而不是驱动自己所在的这个仓。
#
# 判例（`~/.claude` 本身，2026-08-26）：某个 skill 的脚本里拼着
# `pytest --cov --cov-report=xml`、另一个跑 `bun test --coverage`——那是给**被驱动的项目**用的，
# 于是「覆盖率」报绿，而这个仓自己**一行覆盖率都没测过**。
# 一个把「这个仓有工具去测别人」读成「这个仓被测过」的格子，正是本工具要防的那类假绿，
# 只是换了个方向：不是散文当执行，是**别人的执行当自己的**。
#
# 为什么这条在别的仓里没暴露：同类脚本在别的仓里住在 `.claude/skills/` 下，而 `.claude`
# 早就在 SKIP 里。只有当被扫的根**自己就是**那个 `.claude` 时它们才浮上来。
# 同一个道理，同一个处置——只是这次得写出来。
#
# 只挡执行器面，不挡扫描：这些文件仍然是本仓的代码，仍然该被 lint / 类型 / 覆盖。
SKILL_PAYLOAD = re.compile(r"(^|/)skills/[^/]+/(scripts|references|assets)/")

# 门禁清单的 glob 只此一份：执行器面要用它（清单登记的检查器本身就是执行器），
# L2 那一格也要用它。此前两处各写一份，加一个后缀只改了一处就会让两格互相矛盾。
MANIFEST_GLOBS = ["*gates*.txt", "*gates*.yml", "**/*gates*.txt", "**/*gates*.yml"]

CI_GLOBS = [".github/workflows/*.yml", ".github/workflows/*.yaml",
            ".workflow/*.yml", ".workflow/*.yaml", ".gitlab-ci.yml",
            "Jenkinsfile", ".circleci/config.yml", "azure-pipelines.yml",
            ".woodpecker.yml", ".drone.yml"]

# 自建 CI 的声明文件。**平台流水线不是唯一的非本机执行器**——一台自己的机器上
# 跑 systemd timer 同样满足元规则二（从干净克隆、在别人机器上跑），而它在仓里
# 长得像几个普通 shell 脚本，上面那串 glob 一个都命中不了。
#
# 判例（项目甲，2026-08-26）：自建 CI 机 自建 CI 已经在跑、已经抓到四条本机永远看不见的
# 缺陷，本工具却看不见它；那一格的绿是靠 `.workflow/PRPipeline.yml` 撑着的——
# 一个写好了、平台从没启用、从没跑过一次的收据。**结论碰巧对，证据是假的**：
# 删掉那张收据，这格会翻红，尽管 CI 反而更真了。
#
# 为什么用「声明」而不是猜 `ci/*.sh`：`.github/workflows/x.yml` 只有一个意思，
# 而 `ci/run.sh` 可能是任何东西。靠目录名猜，会把本机脚本认成非本机执行器——
# 那正是本工具要防的假绿，只不过换了个方向。
SELF_HOSTED_CI_DECL = ".assurance-ci.txt"

# 一次性动作的结论登记。**变异测试这一格问的不是「装了没」，是「跑过没」**——
# 而它按判据就是一次性的（规范 §3：核心模块成型后 / 一年一次）。把 mutmut / Stryker
# 钉进依赖清单反而是错的：Stryker 一进 devDependencies，此后每次构建交付镜像都要
# 装它一遍。于是**正确的做法在本工具眼里长得和什么都没做一模一样**。
# 判例（项目乙）：Python 与前端各跑过一轮、结论各成一篇文档，两格都报缺。
#
# 格式一行一条：`<动作键>|<结论文档路径>|<YYYY-MM-DD>`。
# 安全性与 .assurance-ci.txt 同源——**它可被核对**：路径必须真的在仓库里、日期必须
# 能解析、动作键必须是已知的（不认的键当坏行报出来，不静默忽略）。
# 而且它会**过期**：超过 ONESHOT_VALID_DAYS 翻黄。一次性不等于一次管到底——
# 代码换过一轮之后，那份结论说的是别的代码。
ONESHOT_DECL = ".assurance-oneshot.txt"
ONESHOT_KEYS = {"mutation-python", "mutation-ts"}
ONESHOT_VALID_DAYS = 365

HOOK_MECHANISMS = {
    "lefthook":     ["lefthook.yml", "lefthook.yaml"],
    "husky":        [".husky"],
    "pre-commit":   [".pre-commit-config.yaml"],
    # 手写钩子不一定放 scripts/。判例（项目乙）：装配脚本与钩子源在
    # `deploy/release/{install_git_hooks.sh,git_hooks/}`，工具报「无钩子」。
    # 这一格的**状态**碰巧还是绿的（只有一套就算唯一），但**证据是假的**——
    # 「一套都没有」与「装了一套」在这里给出同一个绿，读的人分不出来。
    # 证据错了的绿比红更难发现，因为没有人会去查一个绿格子。
    "手写 git hooks": [".githooks", "githooks",
                       "**/git_hooks", "**/install_git_hooks.sh"],
    "simple-git-hooks": [],   # 只在 package.json 里，下面单独判
}


# 执行器面上的**整行注释不算执行器**。这条从原来 lefthook 的特例推广而来：
# 一行「本镜像刻意不含 vulture/mypy/coverage」与一行真的调用它们，在纯文本搜索里
# 长得一模一样——而前者说的恰恰是「没有」。判例（项目乙）：
# `deploy/ci/r760_ci_run.sh` 第 95 行注释里三个工具名同时出现，句意是本镜像不含它们；
# 执行器面一放宽到子目录脚本，这一行就会把三格一起点绿。
#
# 只删整行、不动行内 `#`：shell 的 `$#`、`${x#y}`、URL 的 `#fragment` 都是合法代码，
# 行内剥离会连真的调用一起剥掉——那是把一类假绿换成一类假红。
_LINE_COMMENT = {".js": "//", ".mjs": "//", ".cjs": "//", ".ts": "//", ".jsonc": "//",
                 "Jenkinsfile": "//"}


def _strip_py_docstrings(text: str) -> str:
    """Python 的 docstring 也是散文，不是执行——但它绕过整行注释剥离。

    判例（就在本次修复过程中撞到的，2026-08-26）：新加的门禁
    `deploy/quality/test_assurance_declarations.py` 登记进了门禁清单、因而上了
    执行器面，而它的模块 docstring 里写着「Stryker 一进 devDependencies，此后每次
    构建交付镜像都要装它一遍」——**一句解释「为什么没装」的话，把「装了」那一格
    点成了绿的**。本工具最典型的假绿形状，而且是自己刚放宽的面引进来的。

    只剥 docstring，不剥普通字符串字面量：`subprocess.run(["ruff", "check"])`
    里的 "ruff" 是字面量，也正是执行本身。剥了它就把真的调用一起剥掉了。
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return text                       # 解析不了就别猜，退回原文
    drop: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            drop.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    if not drop:
        return text
    return "\n".join(l for i, l in enumerate(text.splitlines(), 1) if i not in drop)


def _strip_line_comments(path: Path, text: str) -> str:
    if path.suffix.lower() == ".json":
        return text                       # 严格 JSON 没有注释，别去猜
    mark = _LINE_COMMENT.get(path.name) or _LINE_COMMENT.get(path.suffix.lower()) or "#"
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(mark))


# 依赖清单里的一个包名**不是执行器**。`package.json` 上执行器面是因为它有 `scripts`，
# 而 devDependencies 恰好在同一个文件里——于是「装了这个工具」被读成「有人跑这个工具」。
#
# 判例（项目甲，2026-08-26）：`@stryker-mutator/core` 躺在仓根 devDependencies，
# 「变异测试 (TS)」于是报 OK；而 mutmut 同样是声明的 dev 依赖，只不过它写在
# `services/api/pyproject.toml` 的 `[dependency-groups]` 里——那份文件不在执行器面上，
# 「变异测试 (Python)」于是报 CONFIGURED_NOT_RUN。**同一个事实，两个相反的判定**，
# 差别只在依赖清单用的是哪种文件格式。假绿那一半更该修：Python 那格至少是诚实的黄。
#
# 修法不是把 pyproject 也拉上执行器面（那会让假绿对称，是把错的一半推广），
# 而是把依赖段从执行器面上剥掉。剥的只有依赖段：`scripts` / `workspaces` /
# `husky` / `lint-staged` 全部原样留着，它们是真的执行器声明。
_PKG_DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies",
                 "optionalDependencies", "bundledDependencies", "bundleDependencies",
                 "trustedDependencies", "resolutions", "overrides", "pnpm")


def _strip_pkg_deps(path: Path, text: str) -> str:
    if path.name != "package.json":
        return text
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text                       # 解析不了就别猜，退回原文
    if not isinstance(data, dict):
        return text
    for key in _PKG_DEP_KEYS:
        data.pop(key, None)
    return json.dumps(data, ensure_ascii=False, indent=1)


def _iter_files(root: Path, include_vendor: bool, max_files: int = 40000):
    skip = set(SKIP) if include_vendor else SKIP | SKIP_VENDOR
    n = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            continue
        for e in entries:
            if e.name in skip or e.name.startswith(".DS"):
                continue
            if e.is_dir() and not e.is_symlink():
                stack.append(e)
            elif e.is_file():
                n += 1
                if n > max_files:
                    return
                yield e


def _tracked(root: Path) -> set[str] | None:
    """git 追踪的文件集。返回 None 表示不是 git 仓（那就只能信文件系统）。

    为什么要这一步：未入库的文件不是这个项目的配置。
    判例：项目甲 仓根有个 lefthook.yml，看起来是「第二套钩子机制」，
    实际是 vendor 的 pnpm postinstall 落下的示例模板，早已写进 .gitignore。
    只看文件系统的检查器会把它报成死配置——检查器自己犯了它要防的错。

    `-z` 不可省（§10.7）：git 默认对非 ASCII 路径做八进制转义，中文命名的文件
    会匹配不上文件系统里的真实路径，于是被当成「未追踪」而静默漏掉——
    这个检查器本来就是为了防这类静默失效的，自己犯一次格外讽刺。
    """
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {n for n in r.stdout.decode("utf-8", "replace").split("\0") if n}


class Project:
    def __init__(self, root: Path, include_vendor: bool = False):
        self.root = root
        self.include_vendor = include_vendor
        self.tracked = _tracked(root)
        self.files = list(_iter_files(root, include_vendor))
        self.rel = {f.relative_to(root).as_posix() for f in self.files}
        # 未入库的文件不是这个项目的代码——上面 _tracked 的理由同样适用于**计数**。
        # 不过滤的后果实测过：项目丙仓有 322 个追踪中的 .py，工具报 2589，
        # 多出来的两千多个全在 .venv-p0/ 与 .venv-l1/ 里（SKIP 只认 .venv 与 venv）。
        # 一个把「装了什么包」算成「写了多少代码」的读数，后面每一格都不可信。
        if self.tracked is not None:
            self.rel &= self.tracked
            self.files = [f for f in self.files
                          if f.relative_to(root).as_posix() in self.tracked]
        self._text_cache: dict[Path, str] = {}
        self.executor_text = self._collect_executor_text()

    def read(self, p: Path) -> str:
        if p not in self._text_cache:
            try:
                self._text_cache[p] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._text_cache[p] = ""
        return self._text_cache[p]

    def glob(self, pattern: str) -> list[Path]:
        # 必须与 _iter_files 用同一套跳过规则：否则 vendor 里上游自带的 knip /
        # coverage 会被算成本项目的覆盖，检查器自己就犯了它要防的错。
        skip = set(SKIP) if self.include_vendor else SKIP | SKIP_VENDOR
        # 只看**相对 root** 的路径段。用绝对路径的 parts 会把项目所在位置也算进去：
        # 项目恰好放在名字命中 SKIP 的目录下（`.worktrees/`、`build/`、`archive/`…），
        # 整棵树对本工具就此不可见，而输出是「什么都没装」——
        # 一个把「找不到」报成「没有」的检查器，正是它自己要防的那种失效。
        # 判例：在 `.worktrees/assurance-l1` 里跑，17 格报出 15 缺，实为 4 缺。
        try:
            hits = []
            for q in self.root.glob(pattern):
                if not q.is_file():
                    continue
                if skip & set(q.relative_to(self.root).parts):
                    continue
                hits.append(q)
        except (OSError, ValueError):
            return []
        if self.tracked is None:
            return hits
        return [p for p in hits if p.relative_to(self.root).as_posix() in self.tracked]

    def _manifest_registered(self) -> list[Path]:
        """门禁清单里登记的检查器 —— **它们本身就是执行器面**。

        清单只写路径与一句说明，工具名往往只出现在检查器自己的源码里。漏掉这一跳，
        一份「全部登记、三个执行器共读」的 L1 会被读成一格都没有。
        判例（项目乙）：`deploy/quality/test_lint_ratchet.py` 登记在
        `static_gates.txt` 里、每次提交都跑，而 `ruff` 三个字只写在它自己的源码里。

        两种行都算：正文的 `<路径>|<说明>`，以及注释区的 `# exempt:<路径>|<由谁执行>`
        ——豁免行说的是「由别人执行」，不是「不执行」。

        再跟**一跳同目录的本地 import**：登记的常常是一层薄壳（`test_x_ratchet.py`），
        真正调工具的是它 import 的那个模块（`x_check.py`）。只跟一跳、只跟同目录：
        跟深了等于把半个仓库算成执行器面，那就轮到假绿了。
        """
        out: list[Path] = []
        for pat in MANIFEST_GLOBS:
            for m in self.glob(pat):
                for line in self.read(m).splitlines():
                    s = line.strip()
                    if s.startswith("#"):
                        s = s.lstrip("#").strip()
                        if not s.startswith("exempt:"):
                            continue
                        s = s[len("exempt:"):].strip()
                    if not s or "|" not in s:
                        continue
                    rel = s.split("|", 1)[0].strip()
                    if not rel or rel.startswith("<"):
                        continue
                    for q in self._manifest_targets(m, rel):
                        # 登记项是**判据自己的测试**时，只跟那一跳，不要它的正文：
                        # 一个检测器的测试必然把它能检测的每个工具名都写进夹具与用例名
                        # ——那是它**检测的对象**，不是它执行的东西。
                        # 判例（`~/.claude`，2026-08-26）：`test_assurance_check.py` 的
                        # 夹具里有 `[tool.ruff]`、`python -m coverage run`、`mypy`、
                        # `vulture`，于是清单一被正确解析，L1 四格**一起变绿**——
                        # 比它修掉的那个假绿还多三个。
                        # 真正的调用在它 import 的那个模块里（判例：项目乙 的
                        # `test_lint_ratchet.py` 里 `ruff` 只出现在一个用例名里，
                        # 真的调用在 `lint_check.py`），而那一跳下面本来就跟。
                        if not (q.suffix == ".py" and q.name.startswith("test_")):
                            out.append(q)
                        if q.suffix == ".py":
                            for mod in re.findall(r"^\s*(?:import (\w+)|from (\w+) import)",
                                                  self.read(q), re.M):
                                name = mod[0] or mod[1]
                                out += self.glob(
                                    (q.parent / f"{name}.py").relative_to(self.root).as_posix())
        return out

    def _manifest_targets(self, manifest: Path, rel: str) -> list[Path]:
        """清单里的路径相对谁，取决于**谁读它**。两种都试，取真的存在的那些。

        判例（`~/.claude`，2026-08-26）：`bin/gates.txt` 的执行器是 `bin/run_gates.sh`，
        它先 `cd` 到自己所在的目录再跑，所以清单里写的是 `test_tools_gates.py`
        ——相对**清单所在目录**。而本方法此前只按仓根解析，于是这份清单登记的每一个
        检查器都落在执行器面之外，一整层 L1 被读成零，输出还是「什么都没装」。

        项目乙 的 `deploy/release/static_gates.txt` 写的是仓根相对路径，
        所以那边一直是对的——**两种写法都真实存在**，取决于执行器的 cwd，
        而判据不该只认自己先遇到的那一种。
        """
        out = list(self.glob(rel))
        # 纯路径运算，**不落盘、不 resolve**：macOS 的 tmpdir 是符号链（`/var` →
        # `/private/var`），`.resolve()` 之后再 `relative_to(root)` 必然 ValueError，
        # 于是这一整条在临时仓上永远不生效——判据只在真实仓库上活着，测试测不到它。
        # 顺带把 `..` 显式拒掉：清单能指到仓外，等于执行器面能被仓外的文本点绿。
        parts: list[str] = list(manifest.parent.relative_to(self.root).parts)
        for part in Path(rel).parts:
            if part == "..":
                if not parts:
                    return out
                parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        here = "/".join(parts)
        return out + [q for q in self.glob(here) if q not in out] if here else out

    def _collect_executor_text(self) -> str:
        chunks = []
        seen: set[Path] = set()
        pool = [q for pat in EXECUTOR_GLOBS for q in self.glob(pat)] + self._manifest_registered()
        for p in pool:
            if p in seen:
                continue
            seen.add(p)
            if SKILL_PAYLOAD.search(p.relative_to(self.root).as_posix()):
                continue
            txt = self.read(p)
            if p.suffix == ".py":
                txt = _strip_py_docstrings(txt)
            txt = _strip_pkg_deps(p, txt)
            chunks.append(f"### {p.relative_to(self.root)}\n{_strip_line_comments(p, txt)}")
        return "\n".join(chunks)

    def executed(self, *patterns: str) -> bool:
        """这些工具名是否出现在任何执行器面上。"""
        return any(re.search(p, self.executor_text, re.I) for p in patterns)

    def has_lang(self, ext: str, threshold: int = 3) -> int:
        return sum(1 for r in self.rel if r.endswith(ext))

    def find_config(self, *, files: list[str] = (), toml_sections: list[str] = (),
                    json_keys: list[str] = ()) -> str | None:
        """返回命中的配置位置，找不到返回 None。

        文件名同时按「仓根」和「任意子目录」找：monorepo 里工具配置常常放在
        工作区里而不是仓根（判例：项目甲 的 stryker.config.json 在
        services/caseos-node/ 下，只看仓根会把它报成 MISSING）。
        """
        for f in files:
            for p in self.glob(f) or self.glob(f"**/{f}"):
                return p.relative_to(self.root).as_posix()
        for section in toml_sections:
            for p in self.glob("pyproject.toml") + self.glob("*/pyproject.toml") + self.glob("*/*/pyproject.toml"):
                if section in self.read(p):
                    return f"{p.relative_to(self.root).as_posix()} [{section.strip('[]')}]"
        for key in json_keys:
            for p in self.glob("package.json") + self.glob("*/package.json") + self.glob("*/*/package.json"):
                try:
                    data = json.loads(self.read(p))
                except (json.JSONDecodeError, ValueError):
                    continue
                if key in data or key in data.get("scripts", {}) or key in data.get("devDependencies", {}):
                    return f"{p.relative_to(self.root).as_posix()} [{key}]"
        return None


class Check:
    def __init__(self, layer, name, status, evidence="", advice=""):
        self.layer, self.name, self.status = layer, name, status
        self.evidence, self.advice = evidence, advice

    def as_dict(self):
        return {"layer": self.layer, "name": self.name, "status": self.status,
                "evidence": self.evidence, "advice": self.advice}


def _self_hosted_ci(proj: "Project") -> tuple[list[dict], list[str]]:
    """读自建 CI 声明。返回（有效声明, 无效行的说明）。

    格式一行一条：`<执行器名>|<仓内定义路径>|<读结论的命令>`。
    三栏都必填，且第二栏必须真的存在于仓库里——**声明的价值全在于它可被核对**，
    一条指向不存在路径的声明就是本工具要防的那种收据，只不过换了个地方躺着。
    """
    decl = proj.glob(SELF_HOSTED_CI_DECL)
    if not decl:
        return [], []
    good: list[dict] = []
    bad: list[str] = []
    for line in proj.read(decl[0]).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # maxsplit=2：**第 3 栏是一条 shell 命令，里面本来就会有管道符**。
        # 判例（2026-08-26，就在给本工具自己的仓写声明时撞到的）：
        # `…|ssh r760 'ls -1t logs | head -1'` 被切成 4 段，报「不是三栏格式」。
        # 只有前两栏是结构化的，第 3 栏是「剩下的全部」——按结构切，不按分隔符数切。
        parts = [c.strip() for c in line.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            bad.append(f"`{line[:40]}` 不是三栏格式（<名字>|<路径>|<读结论的命令>）")
            continue
        name, defined, verdict = parts
        # 与 glob() 同一套判据：未入库的文件不是这个项目的定义。
        if not proj.glob(defined):
            bad.append(f"{name} 指向的 {defined} 不在仓库里")
            continue
        good.append({"name": name, "defined": defined, "verdict": verdict})
    return good, bad


def _jsonc_strip(text: str) -> str:
    """tsconfig 是 JSONC，判 strict 之前必须先剥注释。

    `@vue/tsconfig` 的基座里就有两行注释写着 strict（"…is part of `strict`"），
    正则会把它们当成配置读到——文本判据落到代码面之前先剥注释，是同一条老规矩。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))


def _resolve_extends(cfg: Path, spec: str, root: Path) -> Path | None:
    """解析一条 extends。基座常住 node_modules —— 本工具的扫描面刻意跳过那里，
    所以这里**按路径直读**，不走 glob 也不过 tracked 过滤：基座不是这个仓库的文件，
    本来就不该入库。依赖没装时返回 None，由调用方说「未解析」，不说「没有」。
    """
    if spec.startswith("."):
        cands = [cfg.parent / spec, cfg.parent / (spec + ".json")]
    else:
        cands, d = [], cfg.parent
        while True:
            nm = d / "node_modules"
            cands += [nm / spec, nm / (spec + ".json"), nm / spec / "tsconfig.json"]
            if d == root or d.parent == d:
                break
            d = d.parent
    for c in cands:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _tsconfig_strict(cfg: Path, root: Path, read, seen=None) -> str:
    """沿 extends 链判 strict，返回 'on' / 'off' / 'unresolved'。

    判例（项目乙）：`frontend/tsconfig.json` 里根本没有 `strict` 字样，
    它 `extends "@vue/tsconfig/tsconfig.json"`，strict 在那份基座里。只在本文件里
    grep 一个 `"strict": true`，会把一份完全严格的配置读成「2 个 tsconfig，0 个 strict」
    ——而 extends 是 tsconfig 最常见的组织方式，不是边角情况。

    本文件自己写了 strict 就以自己的为准（与 tsc 一致，子覆盖父）；数组形式的
    extends 后面的覆盖前面的，也与 tsc 一致。
    """
    seen = set() if seen is None else seen
    if cfg in seen or len(seen) > 10:
        return "unresolved"
    seen.add(cfg)
    txt = _jsonc_strip(read(cfg))
    own = re.search(r'"strict"\s*:\s*(true|false)', txt)
    if own:
        return "on" if own.group(1) == "true" else "off"
    ext = re.search(r'"extends"\s*:\s*(\[[^\]]*\]|"[^"]*")', txt)
    if not ext:
        return "off"
    verdict = "off"
    for spec in re.findall(r'"([^"]+)"', ext.group(1)):
        nxt = _resolve_extends(cfg, spec, root)
        verdict = "unresolved" if nxt is None else _tsconfig_strict(nxt, root, read, seen)
    return verdict


# 缺陷沉降的账**不止一种形状**。一事故一页（docs/incidents/）是一种；单文件、
# 只增不改的长期记忆是另一种，而且在有文档治理规范的仓里往往是**唯一被允许**的那种。
# 判例（项目乙）：`docs/文档治理规范.md` §1 明令不设 docs/incidents/ 域，
# 事故统一追加进 `docs/operations/构建发布长期记忆.md`（54 条，症状/根因/处置/门禁
# 四项，条目只增不改）。工具照旧报缺、并建议「建 docs/incidents/」——照着做会造出
# 第二事实源。**一个会把达标仓库改坏的建议，比报错更贵。**
_LEDGER_NAME = re.compile(r"事故|长期记忆|经验教训|incident|post-?mortem|lessons", re.I)


def _defect_ledger(proj) -> tuple[str, int] | None:
    """找单文件缺陷账。判据仍然可核对：文件名要落在那几个词上，**且条目数 ≥ 5**
    —— 一个只有标题的空壳不算账，那正是本工具要防的收据。"""
    best = None
    for r in sorted(proj.rel):
        if not r.endswith(".md") or not _LEDGER_NAME.search(r.rsplit("/", 1)[-1]):
            continue
        n = len(re.findall(r"^#{2,6} ", proj.read(proj.root / r), re.M))
        if n >= 5 and (best is None or n > best[1]):
            best = (r, n)
    return best


def _oneshot(proj) -> tuple[dict, list[str]]:
    """读一次性动作登记。返回（有效登记, 坏行说明）。见 ONESHOT_DECL 的注释。"""
    decl = proj.glob(ONESHOT_DECL)
    if not decl:
        return {}, []
    good: dict = {}
    bad: list[str] = []
    today = _dt.date.today()
    for line in proj.read(decl[0]).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [c.strip() for c in line.split("|")]
        if len(parts) != 3 or not all(parts):
            bad.append(f"`{line[:40]}` 不是三栏格式")
            continue
        key, doc, day = parts
        if key not in ONESHOT_KEYS:
            bad.append(f"`{key}` 不是已知动作键（{'/'.join(sorted(ONESHOT_KEYS))}）")
            continue
        if not proj.glob(doc):
            bad.append(f"{key} 指向的 {doc} 不在仓库里")
            continue
        try:
            when = _dt.date.fromisoformat(day)
        except ValueError:
            bad.append(f"{key} 的日期 `{day}` 不是 YYYY-MM-DD")
            continue
        age = (today - when).days
        if age < 0:
            bad.append(f"{key} 的日期 {day} 在未来")
            continue
        good[key] = {"doc": doc, "date": day, "age": age, "stale": age > ONESHOT_VALID_DAYS}
    return good, bad


def _with_oneshot(chk: "Check", declared: dict, key: str) -> "Check":
    """一次性动作：用登记的结论文档兜底。**本函数只服务于变异测试那两格。**

    原先只在 MISSING 时兜底，理由是「配了没跑是本工具的头号目标，一条登记不该把它盖掉」。
    那条理由对**日常**检查成立，对**一次性**动作恰好反过来。

    判例（项目甲，2026-08-26）：`[tool.mutmut]` 在仓里、mutmut 在 dev 依赖里、
    前一天真跑过一轮（80 个突变体、37→39 杀、结论逐条落在计划文档里），本格却是
    CONFIGURED_NOT_RUN——因为按判据，变异测试**不许**进钩子也不进日常 CI（要跑整套
    测试、会继承套件里的每一个 flake），于是它**本来就不该有执行器**。
    在这一格上，「有配置无执行器」是设计内的正确形态，不是收据。

    而唯一能把它翻绿的操作是**删掉配置**——删了 `[tool.mutmut]` 就变 MISSING，
    MISSING 又被这条登记兜成 OK。一条门禁如果奖励「删掉可复现的配置」，那是判据错了。

    所以对这两个键放宽到 CONFIGURED_NOT_RUN，但**只认可核对的证据**：登记必须指向
    仓库里真实存在的结论文档、日期可解析、且不超过 ONESHOT_VALID_DAYS。
    这比放宽前那条绿的门槛更高——它此前只需要工具名出现在执行器面上。
    """
    d = declared.get(key)
    if d is None or chk.status == OK:
        return chk
    if d["stale"]:
        return Check(chk.layer, chk.name, NOT_RUN,
                     f"{d['doc']}（{d['date']}，已过去 {d['age']} 天）",
                     f"结论已超过 {ONESHOT_VALID_DAYS} 天。一次性不等于一次管到底——"
                     f"代码换过一轮之后，那份结论说的是别的代码。重跑一轮并更新 {ONESHOT_DECL}")
    cfg = f"{chk.evidence} + " if chk.evidence else ""
    return Check(chk.layer, chk.name, OK,
                 f"{cfg}{d['doc']}（{d['date']}）  ← 仅确认结论文档存在",
                 "本工具查不到那一轮的结论是什么。**打开这篇文档核一次**——"
                 "跑过且看过才算，登记本身不算。")


def _tool(proj, layer, name, cfg, run_patterns, advice_missing, advice_notrun):
    """配置 × 执行器 的四格，**两条对角线不对称**。

    「没有配置文件」不等于「没有这一层」。配置文件只是**参数的一种存放位置**，
    把它当成存在性判据，就会把「参数写死在检查器源码里」读成「什么都没装」。
    判例（项目乙）：lint / 类型 / 死代码三条的扫描参数**刻意**不放
    `ruff.toml` / `[tool.mypy]`，写死在 `deploy/quality/*.py` 里——为的是跨机同结果
    （配置文件可被本机 override，写死的不行；mypy 那几条钉子一松 baseline 就漂移，
    而漂移只在别人的机器上表现为红）。三条各自都在门禁清单里、每次提交都跑。

    反向的不对称是刻意的：**有配置无执行器仍然是 CONFIGURED_NOT_RUN**。
    「跑了没配」最多是参数不可移植，还有人在跑；「配了没跑」是看起来像装了——
    本工具存在的头号理由，不能被这条放宽顺手抹掉。
    """
    ran = proj.executed(*run_patterns)
    if cfg is None:
        if ran:
            return Check(layer, name, OK, "执行器面上有调用（参数不在配置文件里）")
        return Check(layer, name, MISSING, "", advice_missing)
    if ran:
        return Check(layer, name, OK, cfg)
    return Check(layer, name, NOT_RUN, cfg, advice_notrun)


def run(proj: Project) -> list[Check]:
    out: list[Check] = []
    oneshot, oneshot_bad = _oneshot(proj)
    py = proj.has_lang(".py")
    ts = proj.has_lang(".ts") + proj.has_lang(".tsx") + proj.has_lang(".vue")

    # ── L0 结构层 ──────────────────────────────────────────────────────
    contract = proj.find_config(files=[".manon-contract.yaml", ".manon-contract.yml"])
    if contract:
        txt = proj.read(proj.root / contract)
        bad = re.findall(r"-\s+id:\s*\"?([^\"\n]+)\"?(?![\s\S]{0,400}?reason:)", txt)
        out.append(Check("L0", "契约对账豁免表", OK if not bad else NOT_RUN, contract,
                         "" if not bad else f"{len(bad)} 条豁免缺 reason，无法被下一个人核对"))
    else:
        out.append(Check("L0", "契约对账豁免表", MISSING, "",
                         "跑 manon init 并在仓根建 .manon-contract.yaml；死面只有退役或豁免两个去处"))

    # ── L1 机器层 · Python ────────────────────────────────────────────
    if py >= 3:
        out.append(_tool(proj, "L1", f"lint (Python, {py} 文件)",
            proj.find_config(files=["ruff.toml", ".ruff.toml", ".flake8", ".pylintrc"],
                             toml_sections=["[tool.ruff]", "[tool.flake8]", "[tool.pylint"]),
            [r"\bruff\b", r"\bflake8\b", r"\bpylint\b"],
            "配 ruff（F/B/ARG/ERA/ASYNC 规则集）并进门禁清单",
            "配置在但零执行器——把它加进门禁清单或 CI"))
        out.append(_tool(proj, "L1", f"类型检查 (Python, {py} 文件)",
            proj.find_config(files=["mypy.ini", ".mypy.ini", "pyrightconfig.json"],
                             toml_sections=["[tool.mypy]", "[tool.pyright]", "[tool.ty"]),
            [r"\bmypy\b", r"\bpyright\b", r"\bbasedpyright\b", r"\bty check\b"],
            "上 mypy + baseline 棘轮（存量冻结、新代码强制）——单位投入产出最高的一件",
            "配置在但零执行器"))
        out.append(_tool(proj, "L1", "死代码 (Python)",
            proj.find_config(files=[".vulture", "vulture.toml"], toml_sections=["[tool.vulture]"]),
            [r"\bvulture\b", r"\bdeadcode\b"],
            "上 vulture（ruff 的 F 系列覆盖不到跨文件死函数）",
            "配置在但零执行器"))
        out.append(_tool(proj, "L1", "覆盖率 (Python)",
            # `coveragerc`（不带点）是 `coverage --rcfile=` 的常用命名，与 `.coveragerc`
            # 等价。只认带点那个，等于按**文件名的装饰**判存在性。
            # 判例（项目乙）：配置在 `deploy/quality/coveragerc`。
            proj.find_config(files=[".coveragerc", "coveragerc"], toml_sections=["[tool.coverage"],
                             json_keys=[]) or (
                "pyproject.toml [addopts --cov]" if proj.executed(r"--cov\b|pytest-cov") else None),
            [r"--cov\b", r"pytest-cov", r"\bcoverage run\b", r"-m[\"'\s,]+coverage\b"],
            "上 pytest-cov。重点不是追高数字，是找**零覆盖的分支**——错误处理路径是 bug 高发区",
            "配置在但零执行器"))
        out.append(_with_oneshot(_tool(proj, "L1", "变异测试 (Python)",
            proj.find_config(files=["setup.cfg"], toml_sections=["[tool.mutmut]", "[tool.cosmic-ray]"])
            or ("mutmut/cosmic-ray 依赖" if proj.executed(r"\bmutmut\b|\bcosmic-ray\b") else None),
            [r"\bmutmut\b", r"\bcosmic-ray\b"],
            "核心模块跑一轮 mutmut——唯一直接回答「测试全绿但我还是不放心」的方法。"
            f"跑过一轮但刻意不留依赖的，在 {ONESHOT_DECL} 里登记 "
            "`mutation-python|<结论文档>|<日期>`",
            "配置在但零执行器"), oneshot, "mutation-python"))

        # 六件套的第六件：前五件审「你写的」，这一格审「你带进来的」。vibe coding 里
        # 模型自己挑包（投毒包正是冲这个来的）——这条向量没有人的先验可依赖，只能
        # 机器守。配置面刻意宽松（执行器点名即算配置）：pip-audit 这类工具本来就没
        # 有独立配置文件，判据不能按文件名的装饰判存在性（判例同 coveragerc 那条）。
        out.append(_tool(proj, "L1", "依赖审计 (Python)",
            proj.find_config(files=["osv-scanner.toml", ".osv-scanner.toml"])
            or ("pip-audit 执行" if proj.executed(r"\bpip-audit\b", r"\bosv-scanner\b") else None),
            [r"\bpip-audit\b", r"\bosv-scanner\b"],
            "上 pip-audit（或 osv-scanner）+ 存量棘轮——已知漏洞冻结、新依赖带新漏洞即红",
            "配置在但零执行器"))
        # 执行器面刻意认**自建扫描器**，理由与本函数 docstring 第一段同一条：
        # 配置文件只是参数的一种存放位置，把三个上游工具名当成存在性判据，
        # 就会把「自己写的扫描器 + 指纹 allowlist 棘轮」读成「什么都没装」。
        # 判例（项目甲，2026-08-27）：`scripts/scan_release_secrets.mjs` 带 sha256
        # 指纹棘轮，由在册门禁 `check_architecture_boundaries.sh` 每次提交调用、
        # 发布链再调一次，本格却报 MISSING——**同一条教训上一格（依赖审计）
        # 已经写过，紧挨着的这一格没跟上。**
        #
        # 放宽只放执行器面，且只认**标识符形状**的名字（下划线/连字符相连），
        # 不认散文。写成 `密钥扫描` 那样的中文词会命中门禁清单里的说明栏——
        # 那是一句描述，不是一个执行器；一条把描述读成执行器的判据，
        # 比现在这个假红更坏。注释与 docstring 在 executor_text 里已被剥离，
        # 所以剩下的命中只可能来自真被调用的文件名或符号名。
        out.append(_tool(proj, "L1", "密钥扫描",
            proj.find_config(files=[".gitleaks.toml", "gitleaks.toml", ".secrets.baseline",
                                    "detect-secrets.json"]),
            [r"\bgitleaks\b", r"\bdetect-secrets\b", r"\btrufflehog\b",
             r"\bggshield\b", r"\btalisman\b", r"\bgit-secrets\b",
             r"\bscan[_-](?:\w+[_-])?(?:secret|credential)s?\b",
             r"\b(?:secret|credential)s?[_-]scan\w*"],
            "上 gitleaks 或 detect-secrets + baseline 棘轮——模型造的「像密钥的字符串」"
            "与真凭据在 diff 里长得一模一样，人眼挡不住，机器扫得出。"
            "自建扫描器同样算，但要挂在执行器面上（在册门禁/钩子/CI 调它）",
            "配置在但零执行器"))

    # ── L1 机器层 · TypeScript ────────────────────────────────────────
    if ts >= 3:
        out.append(_tool(proj, "L1", f"lint (TS, {ts} 文件)",
            proj.find_config(files=["eslint.config.js", "eslint.config.mjs", "eslint.config.ts",
                                    ".eslintrc", ".eslintrc.js", ".eslintrc.json", "biome.json",
                                    "oxlint.json", ".oxlintrc.json"]),
            [r"\beslint\b", r"\bbiome\b", r"\boxlint\b"],
            "配 eslint 或 oxlint 并进门禁清单", "配置在但零执行器"))

        all_tscfg = proj.glob("**/tsconfig*.json")
        verdicts = {q: _tsconfig_strict(q, proj.root, proj.read) for q in all_tscfg}
        strict = [q for q, v in verdicts.items() if v == "on"]
        unresolved = [q for q, v in verdicts.items() if v == "unresolved"]
        ran = proj.executed(r"\btsc\b", r"typecheck", r"vue-tsc", r"tsgo")
        if not all_tscfg:
            out.append(Check("L1", "类型检查 (TS)", MISSING, "", "建 tsconfig.json 并开 strict:true"))
        elif strict:
            note = f"（另有 {len(unresolved)} 个 extends 未解析）" if unresolved else ""
            out.append(Check("L1", "类型检查 (TS)", OK if ran else NOT_RUN,
                             f"{len(strict)}/{len(all_tscfg)} tsconfig strict{note}",
                             "" if ran else "strict 开了但没有执行器跑 tsc"))
        elif unresolved:
            # 「查不到」不是「没有」。这一格仍然红——不可核对的 strict 不算保障——
            # 但证据必须说实话，否则下一个人会照着它去改一份已经严格的配置。
            out.append(Check("L1", "类型检查 (TS)", MISSING,
                             f"{len(all_tscfg)} 个 tsconfig，0 个自带 strict，"
                             f"{len(unresolved)} 个 extends 未解析",
                             "extends 的基座常住 node_modules，装完依赖再跑本工具才判得了。"
                             "**别照这一行的红直接去开 strict**——它可能已经由基座开着。"))
        else:
            out.append(Check("L1", "类型检查 (TS)", MISSING,
                             f"{len(all_tscfg)} 个 tsconfig，0 个 strict",
                             "开 strict:true——非严格模式下 tsc 漏掉的正是最常见的那类 bug"))

        out.append(_tool(proj, "L1", "死代码 (TS)",
            proj.find_config(files=["knip.json", "knip.jsonc", "knip.config.ts", "knip.config.js",
                                    ".ts-prunerc"], json_keys=["knip", "ts-prune"]),
            [r"\bknip\b", r"\bts-prune\b"],
            "上 knip——它找未使用的文件/导出/依赖，是「冗余设计」的机器化定义",
            "配置在但零执行器"))
        out.append(_tool(proj, "L1", "覆盖率 (TS)",
            ("覆盖率脚本" if proj.executed(
                r"--coverage\b", r"(?:npx |bunx |bun |pnpm |yarn |npm run |&&\s*|\|\s*|\")c8\b",
                r"(?:npx |bunx |bun |pnpm |yarn |npm run |&&\s*|\|\s*|\")nyc\b", r"coverage-v8") else None),
            [r"--coverage\b", r"(?:npx |bunx |bun |pnpm |yarn |npm run |&&\s*|\|\s*|\")c8\b",
             r"(?:npx |bunx |bun |pnpm |yarn |npm run |&&\s*|\|\s*|\")nyc\b", r"coverage-v8"],
            "bun test --coverage 或 vitest --coverage", "配置在但零执行器"))
        out.append(_with_oneshot(_tool(proj, "L1", "变异测试 (TS)",
            proj.find_config(files=["stryker.config.json", "stryker.conf.json",
                                    "stryker.config.mjs", ".stryker.conf.js"]),
            [r"\bstryker\b"],
            "核心模块跑一轮 Stryker。跑过一轮但刻意不留依赖的（Stryker 一进 "
            f"devDependencies，此后每次构建镜像都要装它），在 {ONESHOT_DECL} 里登记 "
            "`mutation-ts|<结论文档>|<日期>`",
            "配置在但零执行器"), oneshot, "mutation-ts"))

        out.append(_tool(proj, "L1", "依赖审计 (TS)",
            proj.find_config(files=["osv-scanner.toml", ".osv-scanner.toml"])
            or ("audit 执行" if proj.executed(
                r"\bnpm audit\b", r"\bpnpm audit\b", r"\byarn audit\b", r"\bosv-scanner\b") else None),
            [r"\bnpm audit\b", r"\bpnpm audit\b", r"\byarn audit\b", r"\bosv-scanner\b"],
            "npm audit / pnpm audit / osv-scanner 进 CI——已知漏洞存量冻结，新依赖带新漏洞即红",
            "配置在但零执行器"))

    # ── L2 门禁层 ──────────────────────────────────────────────────────
    # 清单放在哪儿都是清单：只找仓根与 scripts/ 会漏掉把它收进 deploy/ 之类目录的仓库。
    # 判例：项目乙 的 deploy/release/static_gates.txt 由三个执行器共读
    # （pre-commit / 发布 preflight / 自建 CI），工具却报「门禁清单 MISSING」，
    # 连带「执行器覆盖不变量」也误报——**一份完全达标的 L2 被读成零**。
    manifests = [q for pat in MANIFEST_GLOBS for q in proj.glob(pat)]
    # 一个仓可能有主清单 + 豁免区两份。挑**条目最多**的当主清单：
    # 按路径排序会挑中 `ci_only_gates.txt`（1 条）而不是 `static_gates.txt`（36 条），
    # 于是一层达标的 L2 被读成「只有 1 条门禁」。
    def _entry_count(q):
        return sum(1 for l in proj.read(q).splitlines()
                   if l.strip() and not l.strip().startswith("#"))
    manifests = sorted(set(manifests), key=lambda q: (-_entry_count(q), q.as_posix()))
    if manifests:
        m = manifests[0].relative_to(proj.root).as_posix()
        entries = [l for l in proj.read(manifests[0]).splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        exempt = [l for l in proj.read(manifests[0]).splitlines() if "exempt:" in l]
        out.append(Check("L2", "门禁清单", OK, f"{m}：{len(entries)} 条登记 + {len(exempt)} 条豁免"))
        # 断言可能写在清单里，也可能写在检查器源码里（本仓是后者：
        # ExecutorCoverageTests 在 test_release_delivery.py 里）。两处都认。
        cov = proj.executed(r"执行器覆盖|executor.?coverage|gate.?registry|check_gate_registry")
        if not cov:
            cov = any(
                re.search(r"执行器覆盖|ExecutorCoverage|executor.?coverage", proj.read(q))
                for q in proj.glob("**/test_*.py") + proj.glob("**/check_*.py"))
        out.append(Check("L2", "执行器覆盖不变量", OK if cov else MISSING,
                         "清单声明了执行器覆盖断言" if cov else "有清单但无「磁盘 == 登记 + 豁免」的断言",
                         "" if cov else "加一条检查器断言清单覆盖全部检查器，否则会出现无人执行的孤儿"))
    else:
        out.append(Check("L2", "门禁清单", MISSING, "",
                         "建一份 <路径>|<说明> 格式的唯一清单，所有执行器都读它"))
        out.append(Check("L2", "执行器覆盖不变量", MISSING, "", "先有清单"))

    # ── 元规则二：非本机执行器 ────────────────────────────────────────
    ci = [p.relative_to(proj.root).as_posix() for g in CI_GLOBS for p in proj.glob(g)]
    declared, bad_decl = _self_hosted_ci(proj)
    # ⚠️ 本工具只能看到「配置在不在」，看不到「它有没有真的跑过」——
    # CI 平台是否启用、流水线是否成功，都在仓库之外。
    # 这一格因此是整张表里唯一可能给出**假绿**的：配置躺在仓里、平台没开，
    # 表现和「装好了」一模一样。恰恰是本规范 §2 要防的那种失效，
    # 所以宁可每次都把这句话打出来，也不让它安静地绿着。
    #
    # 自建 CI 的声明比平台配置多守一条：它必须写明**结论怎么读**。
    # 平台 CI 至少还有个网页能点进去看；自建 CI 的结论落在别人机器的某个文件里，
    # 不把那条命令写进仓库，下一个人就只能问人——而「只能问人」的保障，
    # 在问不到人的那天等于没有。
    if bad_decl:
        # **坏行优先于好行**，哪怕旁边还有两条是好的。声明会烂：脚本改名、目录搬家，
        # 而那一行留在原地指向空处。如果「有好行就绿」，烂掉的那条就永远没人看见——
        # 判据被悄悄改松而无人知晓，正是本工具存在的理由。
        # （实测：第一版写成 `if declared: OK / elif bad: NOT_RUN`，
        #   2 好 + 1 坏照样给绿，双向验红当场抓到。）
        out.append(Check("元规则", "非本机执行器 (CI)", NOT_RUN,
                         f"{SELF_HOSTED_CI_DECL} 有 {len(bad_decl)} 行无效"
                         f"（另有 {len(declared)} 行有效）：{'；'.join(bad_decl[:3])}",
                         f"修好 {SELF_HOSTED_CI_DECL} 里的这些行，或删掉它们。"
                         "一条指向不存在定义的声明，等于用收据把这格顶绿。"))
    elif declared:
        detail = "；".join(f"自建 {d['name']} → {d['defined']}（结论：{d['verdict']}）"
                           for d in declared[:2])
        if ci:
            detail += f"｜另有平台配置 {', '.join(ci[:2])}"
        out.append(Check("元规则", "非本机执行器 (CI)", OK,
                         detail + "  ← 仅确认声明与定义存在",
                         "本工具查不到它有没有真的跑过。**用上面「结论」那条命令核一次**——"
                         "跑绿过才算保障，声明本身不算。"))
    else:
        out.append(Check("元规则", "非本机执行器 (CI)", OK if ci else MISSING,
                         (", ".join(ci[:3]) + "  ← 仅确认配置存在") if ci else "零 CI 配置",
                         "本工具查不到它是否真的在跑（平台启用状态在仓库之外）。"
                         "**在流水线真跑绿之前，别把它当成已有的保障。**"
                         if ci else
                         "PR 上跑一次秒级静态门禁即可。CI 的价值是「从干净克隆、在别人机器上跑」。"
                         f"自建 CI（自己的机器 + 定时器）同样满足，在 {SELF_HOSTED_CI_DECL} "
                         "里按 `<名字>|<仓内定义路径>|<读结论的命令>` 声明一行即可"))

    # 登记文件本身也要被核，坏行**必须自己成一行红**。一条键写错或路径搬家的登记，
    # 表现和「没登记」一模一样——而作者以为自己登记过了。与 .assurance-ci.txt
    # 「坏行优先于好行」同一条理由。
    if oneshot_bad:
        out.append(Check("元规则", "一次性动作登记", NOT_RUN,
                         f"{ONESHOT_DECL} 有 {len(oneshot_bad)} 行无效："
                         + "；".join(oneshot_bad[:3]),
                         f"修好 {ONESHOT_DECL} 里的这些行，或删掉它们。"
                         "一条指向不存在文档的登记，等于用收据把这格顶绿。"))

    # ── 元规则：钩子机制唯一 ──────────────────────────────────────────
    def _hits(pat: str) -> list[Path]:
        """路径可以是字面量，也可以是 glob —— 手写钩子不一定放 scripts/。
        目录要算数得**里面真有入库的文件**：一个空目录不是一套钩子机制。"""
        skip = SKIP if proj.include_vendor else SKIP | SKIP_VENDOR
        found = []
        for q in (proj.root.glob(pat) if "*" in pat else [proj.root / pat]):
            try:
                rel = q.relative_to(proj.root).as_posix()
            except ValueError:
                continue
            if not q.exists() or skip & set(rel.split("/")):
                continue
            if proj.tracked is None or any(t == rel or t.startswith(rel + "/")
                                           for t in proj.tracked):
                found.append(q)
        return found

    # 证据要写清**在哪儿**，不只是机制名。此前这一格只打机制名，于是
    # 「一套都没有」印成「无钩子」、「装了一套」印成「手写 git hooks」——
    # 两者都是绿的，读的人分不出哪个是真的装了。
    present: dict[str, str] = {}
    for name, pats in HOOK_MECHANISMS.items():
        for pat in pats:
            hit = _hits(pat)
            if hit:
                present[name] = hit[0].relative_to(proj.root).as_posix()
                break
    for q in proj.glob("package.json") + proj.glob("**/package.json"):
        try:
            d = json.loads(proj.read(q))
        except (json.JSONDecodeError, ValueError):
            continue
        if "simple-git-hooks" in d or "husky" in d.get("devDependencies", {}):
            present.setdefault("package.json 钩子声明", q.relative_to(proj.root).as_posix())
    detail = "；".join(f"{n}（{w}）" for n, w in sorted(present.items()))
    if len(present) <= 1:
        out.append(Check("元规则", "钩子机制唯一", OK, detail or "无钩子",
                         "" if present else
                         "没有仓内钩子机制。装一套（本机那一层），但别只有这一层——"
                         "本机钩子可 --no-verify 绕过，元规则二要的是机器之外那一个。"))
    else:
        dead = []
        for n in present:
            for pat in HOOK_MECHANISMS.get(n, []):
                for f in _hits(pat):
                    if f.is_file():
                        live = [l for l in proj.read(f).splitlines()
                                if l.strip() and not l.strip().startswith("#")]
                        if not live:
                            dead.append(f"{f.relative_to(proj.root)}（全是注释，死配置）")
        out.append(Check("元规则", "钩子机制唯一", NOT_RUN, detail,
                         f"{len(present)} 套并存，其中一定有死的" + (f"：{', '.join(dead)}" if dead else "")))

    # ── L3 行为层 ──────────────────────────────────────────────────────
    inc = proj.root / "docs" / "incidents"
    pages = len([f for f in inc.glob("*.md") if f.name != "README.md"]) if inc.is_dir() else 0
    ledger = None if pages else _defect_ledger(proj)
    if pages:
        out.append(Check("L3", "事故账（缺陷沉降）", OK, f"docs/incidents/：{pages} 页"))
    elif ledger:
        out.append(Check("L3", "事故账（缺陷沉降）", OK, f"{ledger[0]}：{ledger[1]} 条",
                         "单文件账要自己盯住「防复发：哪个门禁守着它」这一项——"
                         "一事故一页时它是个标题，追加进一份长文件时它最容易被省掉。"))
    else:
        out.append(Check("L3", "事故账（缺陷沉降）", MISSING, "",
                         "缺陷沉降要有账。两种形状都算：`docs/incidents/` 一事故一页，"
                         "或一份只增不改的长期记忆（文件名带 事故 / 长期记忆 / incident / "
                         "postmortem 之一，条目 ≥5）。每条都要回答「防复发：哪个门禁守着它」。"
                         "**仓里有文档治理规范的按它的域来——别为这一格造出第二事实源。**"))
    e2e = any(re.search(r"(^|/)(e2e|integration|acceptance)/", r) for r in proj.rel)
    out.append(Check("L3", "端到端/验收用例", OK if e2e else MISSING,
                     "" if e2e else "未发现 e2e/integration/acceptance 目录"))
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    root = Path(args[0] if args else ".").expanduser().resolve()
    if not root.is_dir():
        print(f"不是目录：{root}", file=sys.stderr)
        return 2

    proj = Project(root, include_vendor="--include-vendor" in flags)
    checks = run(proj)

    if "--json" in flags:
        print(json.dumps({"project": str(root), "checks": [c.as_dict() for c in checks]},
                         ensure_ascii=False, indent=2))
        return 0 if all(c.status in (OK, NA) for c in checks) else 1

    icon = {OK: "✅", NOT_RUN: "⚠️ ", MISSING: "❌", NA: "  "}
    print(f"\n工程保障体系合规检查 —— {root.name}")
    print(f"判据：references/判据.md" + ("" if proj.include_vendor else "（已跳过 vendor/）"))
    print("=" * 78)
    layer = None
    for c in checks:
        if c.layer != layer:
            layer, = (c.layer,)
            print(f"\n【{layer}】")
        line = f"  {icon[c.status]} {c.name:<26} {c.status}"
        if c.evidence:
            line += f"   {c.evidence}"
        print(line)
        if c.advice:
            print(f"       └─ {c.advice}")

    n_ok = sum(1 for c in checks if c.status == OK)
    n_nr = sum(1 for c in checks if c.status == NOT_RUN)
    n_ms = sum(1 for c in checks if c.status == MISSING)
    print("\n" + "=" * 78)
    print(f"  OK {n_ok}   配了没跑 {n_nr}   缺 {n_ms}   合计 {len(checks)}")
    if n_nr:
        print("\n  ⚠️  「配了没跑」按缺计分，且比缺更危险——它看起来像装了。")
    return 0 if (n_nr + n_ms) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
