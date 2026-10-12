# -*- coding: utf-8 -*-
"""自检门禁（第 5 课）：AI 交付前必须跑，任何一项失败退出码非零。

三层防线，一条命令全跑：
  1. 语法层 —— shared/backend/pet 全部 .py 语法编译（抓低级语法错误）
  2. 契约层 —— 后端路由挂的 response_model 必须来自 shared/contracts；
              前端调用契约接口的方法必须返回契约类型（抓「绕契约」）
  3. 行为层 —— 跑 pytest（第 3 课的契约 + 行为测试）

判定核心（单一事实源思想）：
  后端哪个路由挂了 response_model，那个 (method, path) 就是「契约接口」；
  前端任何调用契约接口的方法，返回类型必须是 shared/contracts 的契约名，
  否则 = 绕契约（返回 Any / 缺失 / 自造类型）。

用法（项目根）：
  backend/.venv/Scripts/python.exe selfcheck.py
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
BACKEND_MAIN = ROOT / "backend" / "app" / "main.py"
FRONTEND_CLIENT = ROOT / "pet" / "api_client.py"
SCAN_DIRS = (ROOT / "shared", ROOT / "backend", ROOT / "pet")
VENV_PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"

# 前端 HTTP helper → 实际 HTTP 方法（判断前端调用走的是 GET 还是 POST）
_HELPER_METHOD = {"_get": "get", "_post_json": "post", "_post_image": "post",
                  "_put_json": "put", "_delete_json": "delete"}
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "options", "head"})
_CONTRACTS_MODULE = "shared.contracts"


class Issue:
    __slots__ = ("file", "line", "message")

    def __init__(self, file: str, line: int, message: str) -> None:
        self.file = file
        self.line = line
        self.message = message


_issues: list[Issue] = []


def _report(file: str, line: int, message: str) -> None:
    _issues.append(Issue(file, line, message))


def _contract_imports(tree: ast.AST) -> set[str]:
    """收集 `from shared.contracts import (...)` 里的名字集合。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _CONTRACTS_MODULE:
            names.update(alias.name for alias in node.names)
    return names


def _path_template(node: ast.expr) -> str | None:
    """提取 HTTP 路径：常量字符串直取；f-string 还原为带 {变量名} 的模板。

    api_client 的路径参数接口用 f-string 拼路径（如 f"/api/events/{event_id}"），
    AST 是 JoinedStr，必须还原成 "/api/events/{event_id}" 才能与后端路径比对。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                if isinstance(v.value, ast.Name):
                    parts.append("{" + v.value.id + "}")
                else:
                    parts.append("{...}")
        return "".join(parts)
    return None


def _scan_py_files() -> list[pathlib.Path]:
    return sorted(
        py for root in SCAN_DIRS for py in root.rglob("*.py")
        if ".venv" not in py.parts and ".venv" not in str(py)
    )


# ---------------------------------------------------------------- 1. 语法层
def check_syntax() -> None:
    """全部 .py 语法编译，抓低级语法错误（AI 最常见的翻车点）。"""
    for py in _scan_py_files():
        try:
            compile(py.read_text(encoding="utf-8"), str(py), "exec")
        except SyntaxError as exc:
            _report(str(py.relative_to(ROOT)), exc.lineno or 0,
                    f"语法错误：{exc.msg}")


# ---------------------------------------------------------------- 2. 契约层：后端
def check_backend() -> set[tuple[str, str]]:
    """后端契约检查，返回契约接口集合 {(http_method, path)} 供前端比对。

    - 挂了 response_model 的路由：该模型必须是 shared.contracts 的 import 名
      （禁止自造本地模型冒充契约 = 违反单一事实源）
    - 后端 import 的每个契约名必须至少被一个路由用作 response_model
      （改了契约却没接上 = AI 常见的「前后端改一次」漏项）
    """
    tree = ast.parse(BACKEND_MAIN.read_text(encoding="utf-8"))
    imports = _contract_imports(tree)
    used: set[str] = set()
    contract_api: set[tuple[str, str]] = set()
    rel = str(BACKEND_MAIN.relative_to(ROOT))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            # 只认 `@app.get("/api/...")` 这类路由装饰器
            if not (isinstance(deco, ast.Call)
                    and isinstance(deco.func, ast.Attribute)
                    and isinstance(deco.func.value, ast.Name)
                    and deco.func.value.id == "app"
                    and deco.func.attr in _HTTP_METHODS):
                continue
            path_val = ""
            if deco.args and isinstance(deco.args[0], ast.Constant) \
                    and isinstance(deco.args[0].value, str):
                path_val = deco.args[0].value
            rm = next((kw.value for kw in deco.keywords if kw.arg == "response_model"),
                      None)
            if rm is None:
                # 裸返回接口：违反「禁止 dict[str, Any] 裸返回」铁律，直接拦截
                _report(rel, deco.lineno,
                        f"路由 {path_val or '?'} 未挂 response_model，"
                        f"禁止 dict[str, Any] 裸返回（契约必须两端成对）")
                continue
            if path_val:
                contract_api.add((deco.func.attr, path_val))
            if isinstance(rm, ast.Name):
                if rm.id not in imports:
                    _report(rel, deco.lineno,
                            f"路由 {path_val or '?'} 的 response_model={rm.id} 不在 "
                            f"shared.contracts 的 import 里（契约必须只有一个家）")
                else:
                    used.add(rm.id)
            else:
                _report(rel, deco.lineno,
                        f"路由 {path_val or '?'} 的 response_model 必须是 "
                        f"shared.contracts 的顶层契约名（禁止表达式/别名）")

    for name in sorted(imports - used):
        _report(rel, 0,
                f"契约 {name} 已被后端 import，但没有任何路由把它挂为 "
                f"response_model（改了契约没接上？）")
    return contract_api


# ---------------------------------------------------------------- 2. 契约层：前端
def check_frontend(contract_api: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """前端契约检查：调用契约接口的方法必须返回 shared.contracts 契约类型。

    返回 frontend_contract_uses：前端用契约类型标注过的方法所调用的接口集合，
    供主流程做反向闭环（这些接口后端必须也挂了 response_model）。

    - 方法返回注解是契约 import 名 → 前端契约方法，其调用的接口记为契约接口
    - 方法返回注解不是契约类型（Any/缺失）→ 若它调用了后端契约接口 = 绕契约
    - 前端 import 的每个契约名必须被某个方法用作返回类型
    """
    tree = ast.parse(FRONTEND_CLIENT.read_text(encoding="utf-8"))
    imports = _contract_imports(tree)
    used: set[str] = set()
    rel = str(FRONTEND_CLIENT.relative_to(ROOT))
    frontend_contract_uses: set[tuple[str, str]] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # 收集该方法体内 self._get/_post_json 等调用的 (method, path)
        calls: list[tuple[str, str]] = []
        for call in ast.walk(node):
            if not (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"
                    and call.func.attr in _HELPER_METHOD):
                continue
            method = _HELPER_METHOD[call.func.attr]
            path = _path_template(call.args[0]) if call.args else None
            if path:
                calls.append((method, path.split("?")[0]))
        ann = node.returns
        if isinstance(ann, ast.Name) and ann.id in imports:
            # 前端契约方法：用契约类型标注，后端必须对应挂 response_model
            used.add(ann.id)
            frontend_contract_uses.update(calls)
        else:
            # 未标注契约类型的方法：若调用了后端契约接口 = 绕契约
            for m, p in calls:
                if (m, p) in contract_api:
                    detail = (f"返回类型 {ann.id}" if isinstance(ann, ast.Name)
                              else "返回类型缺失")
                    _report(rel, node.lineno,
                            f"方法 {node.name} 调用契约接口 {m.upper()} {p}，"
                            f"必须返回 shared.contracts 的契约类型（当前{detail}）")

    for name in sorted(imports - used):
        _report(rel, 0,
                f"契约 {name} 已被前端 import，但没有任何方法把它用作返回类型"
                f"（改了契约没接上？）")
    return frontend_contract_uses


# ---------------------------------------------------------------- 3. 行为层
def check_tests() -> None:
    """跑第 3 课建立的行为测试；pytest 失败即违规。"""
    py = VENV_PY if VENV_PY.exists() else pathlib.Path(sys.executable)
    proc = subprocess.run([str(py), "-m", "pytest", "tests", "-q"],
                          cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        _report("pytest", 0, "行为测试失败（见下方 pytest 输出）")
        print((proc.stdout or "") + (proc.stderr or ""))
    else:
        last = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if last:
            print("   pytest:", last[-1].strip())


# ---------------------------------------------------------------- 主流程
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("回顾 Agent 自检门禁（第 5 课）")
    print("=" * 46)

    check_syntax()
    contract_api = check_backend()
    frontend_uses = check_frontend(contract_api)
    # 反向闭环：前端已用契约类型标注的接口，后端必须挂 response_model
    for m, p in sorted(frontend_uses - contract_api):
        _report(str(BACKEND_MAIN.relative_to(ROOT)), 0,
                f"前端已用契约类型标注 {m.upper()} {p}，但后端该路由没有挂 "
                f"response_model（契约接口两端必须成对）")
    check_tests()

    print("=" * 46)
    if _issues:
        print(f"FAIL —— {len(_issues)} 项未通过，禁止交付：")
        for it in _issues:
            where = f"{it.file}:{it.line}" if it.line else it.file
            print(f"  ✗ {where}  {it.message}")
        return 1
    print("PASS —— 语法 / 契约 / 行为三层全绿，可以交付。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
