#!/usr/bin/env python3
"""四层架构边界检查：core ← data ← engine ← api。

为什么要有这个脚本
------------------
ADR 0006 把分层从三层改成四层（多插一层 `data` 专管真实行情落库），
但这个约束此前**只写在 README 和 ADR 里**。约束靠自觉等于没有约束：
只要有人在 `core` 里 `import sqlite3` 读一行库，或者让 `data` 反向
依赖 `engine`，整套「core 可单测、engine 可换数据源」的收益立刻归零，
而且不会有任何报错——只会在某天想换数据源时才发现拆不开。

所以这里用 AST 静态扫一遍 import，把四条硬规则做成退出码：

1. **分层矩阵**：core 不依赖任何兄弟层；data 只依赖 core；
   engine 依赖 core + data；api 可以依赖前三层。反向依赖一律违规。
2. **core 不做 I/O**：core 是纯计算层，禁止 sqlite3 / urllib / http /
   socket / pathlib / os 这类跟外界打交道的模块。它一旦能读库，
   「core 可以脱离数据库单测」这句承诺就废了。
3. **core 只带一个第三方依赖**：pydantic（用于 schemas 的字段校验）。
   其余一律标准库，保证计算内核可被单独搬走复用。
4. **data 不碰表现层**：data 不该出现 fastapi，避免把 Web 框架拖进同步任务。
5. **engine 不自己开库**：engine 不得 import sqlite3，数据访问必须经 `data.repo`。

用法：
    python scripts/check_layers.py          # 有违规 → 退出码 1
    python scripts/check_layers.py --list   # 顺带打印各层实际依赖
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"

# 包名 → 层名
LAYER_OF = {
    "alphaforge_core": "core",
    "alphaforge_data": "data",
    "alphaforge_engine": "engine",
    "alphaforge_api": "api",
}
LAYER_DIR = {v: k for k, v in LAYER_OF.items()}

# 第 1 条：每层允许依赖的兄弟层
ALLOWED = {
    "core": frozenset(),
    "data": frozenset({"core"}),
    "engine": frozenset({"core", "data"}),
    "api": frozenset({"core", "data", "engine"}),
}

# 第 2 条：core 禁止出现的 I/O 相关模块（含标准库与常见第三方）
CORE_FORBIDDEN = frozenset({
    "sqlite3", "urllib", "urllib3", "http", "httplib", "socket", "requests",
    "pathlib", "os", "io", "shutil", "glob", "subprocess", "tempfile",
    "threading", "concurrent", "multiprocessing", "asyncio", "time",
    "fastapi", "uvicorn", "starlette",
})

# 第 3 条：core 允许的第三方依赖（其余第三方一律违规，标准库不限）
CORE_THIRD_PARTY_OK = frozenset({"pydantic"})

# 第 4 条：data 禁止拖入的 Web 框架
DATA_FORBIDDEN = frozenset({"fastapi", "uvicorn", "starlette"})

# 第 5 条：engine 不得自己开库，数据访问统一走 data 层
ENGINE_FORBIDDEN = frozenset({"sqlite3"})


def _iter_modules(layer: str):
    """遍历某层的全部 .py 文件（含 sources/ 子包）。"""
    base = PACKAGES / layer / LAYER_DIR[layer]
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _top(path: Path) -> str:
    """相对仓库根的可读路径，用于报错定位。"""
    return path.relative_to(ROOT).as_posix()


def _imports(path: Path) -> list[tuple[int, str]]:
    """取出文件内所有 import 的顶层模块名，附带行号。

    函数体内的延迟 import 同样算数——分层不允许靠「藏进函数里」绕过。
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - 语法错由 py_compile 负责
        print(f"  ! 语法错误 {_top(path)}:{exc.lineno} {exc.msg}", file=sys.stderr)
        return []

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            # level > 0 是包内相对引用，不可能跨层，直接跳过
            if node.level or not node.module:
                continue
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def check() -> tuple[list[str], dict[str, set[str]]]:
    """返回 (违规列表, 各层实际依赖图)。"""
    violations: list[str] = []
    graph: dict[str, set[str]] = {layer: set() for layer in ALLOWED}

    for layer in ALLOWED:
        for path in _iter_modules(layer):
            for lineno, top in _imports(path):
                where = f"{_top(path)}:{lineno}"

                # 规则 1：分层矩阵
                target = LAYER_OF.get(top)
                if target is not None:
                    if target == layer:
                        continue
                    graph[layer].add(target)
                    if target not in ALLOWED[layer]:
                        violations.append(
                            f"分层越界  {where}  {layer} 不允许依赖 {target}"
                            f"（{top}）"
                        )
                    continue

                # 规则 2 / 3：core 是纯计算内核
                if layer == "core":
                    if top in CORE_FORBIDDEN:
                        violations.append(
                            f"core 混入 I/O  {where}  禁止 import {top}"
                        )
                    elif top not in sys.stdlib_module_names and top not in CORE_THIRD_PARTY_OK:
                        violations.append(
                            f"core 外部依赖  {where}  只允许 pydantic，出现 {top}"
                        )

                # 规则 4：data 不拖 Web 框架
                if layer == "data" and top in DATA_FORBIDDEN:
                    violations.append(
                        f"data 混入 Web  {where}  禁止 import {top}"
                    )

                # 规则 5：engine 不自己开库
                if layer == "engine" and top in ENGINE_FORBIDDEN:
                    violations.append(
                        f"engine 绕过数据层  {where}  禁止 import {top}，请经 repo 读库"
                    )

    return violations, graph


def main() -> int:
    ap = argparse.ArgumentParser(description="四层架构边界检查")
    ap.add_argument("--list", action="store_true", help="打印各层实际依赖")
    args = ap.parse_args()

    violations, graph = check()

    if args.list:
        print("各层实际依赖：")
        for layer in ("core", "data", "engine", "api"):
            deps = ", ".join(sorted(graph[layer])) or "（无兄弟层依赖）"
            print(f"  {layer:<7} → {deps}")

    if violations:
        print(f"\n架构边界检查失败，共 {len(violations)} 条：")
        for v in violations:
            print(f"  ✗ {v}")
        print("\n约束定义见 README「目录结构」与 docs/adr/0006-real-market-data.md")
        return 1

    print(f"架构边界检查通过：{len(ALLOWED)} 层，0 条越界")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
