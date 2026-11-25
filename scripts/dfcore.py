#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DevFlow 核心库：路径解析、原子 IO、配置、任务状态机、证据校验。

只依赖 Python 3.8+ 标准库。所有脚本共用这一份实现，避免多处逻辑漂移。
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = "1.0"
DF_DIRNAME = ".devflow"

NODE_STATUSES = [
    "pending",
    "ready",
    "in_progress",
    "needs_review",
    "completed",
    "blocked",
    "failed",
    "superseded",
    "cancelled",
]

TERMINAL_STATUSES = {"completed", "superseded", "cancelled"}

# 合法状态迁移。任何不在这里的迁移都会被拒绝。
TRANSITIONS = {
    "pending": {"ready", "blocked", "cancelled", "superseded"},
    "ready": {"in_progress", "pending", "blocked", "cancelled", "superseded"},
    "in_progress": {"needs_review", "failed", "blocked", "cancelled", "ready"},
    "needs_review": {"completed", "in_progress", "failed", "blocked"},
    "failed": {"in_progress", "blocked", "cancelled", "superseded"},
    "blocked": {"ready", "in_progress", "failed", "cancelled", "superseded"},
    "completed": {"superseded"},
    "superseded": set(),
    "cancelled": set(),
}

PHASES = ["Foundation", "Shared", "Business", "Integration", "Release"]
PRIORITIES = ["P0", "P1", "P2", "P3"]


class DevFlowError(Exception):
    """可预期的业务错误：打印信息后以非零码退出，不打印堆栈。"""


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def dwidth(text: str) -> int:
    """终端显示宽度：中日韩全角字符算 2 列。"""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    text = "" if text is None else str(text)
    return text + " " * max(0, width - dwidth(text))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path):
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        raise DevFlowError(f"文件不存在：{path}")
    except json.JSONDecodeError as exc:
        raise DevFlowError(f"JSON 解析失败：{path}\n  {exc}")


def dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    """先写临时文件再 os.replace，避免中断导致半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, dump_json(data))


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@contextmanager
def locked(df_dir: Path):
    """跨进程写锁。单写者模型下只需防止并发覆盖。"""
    df_dir.mkdir(parents=True, exist_ok=True)
    lock = df_dir / ".lock"
    acquired = False
    for _ in range(100):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > 30:  # 陈旧锁
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            time.sleep(0.05)
    if not acquired:
        raise DevFlowError("无法获取 .devflow/.lock，可能有另一个 DevFlow 进程正在写入")
    try:
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------
# 极简 YAML（只支持本项目 config 用到的子集）
# --------------------------------------------------------------------------


def _yaml_scalar(raw: str):
    text = raw.strip()
    if not text:
        return None
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def mini_yaml_load(text: str):
    """解析缩进映射 + `- ` 列表。不支持多行字符串、锚点、flow 语法。"""
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        if body.startswith("#"):
            continue
        # 去掉行尾注释（引号内的 # 保留）
        if " #" in body:
            quote = None
            cut = None
            for i, ch in enumerate(body):
                if ch in "\"'":
                    quote = None if quote == ch else (ch if quote is None else quote)
                elif ch == "#" and quote is None and i > 0 and body[i - 1] == " ":
                    cut = i
                    break
            if cut is not None:
                body = body[:cut].rstrip()
        lines.append((indent, body))

    def parse_block(index: int, indent: int):
        # 判断是列表还是映射
        if index >= len(lines):
            return None, index
        if lines[index][1].startswith("- "):
            items = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                item_body = lines[index][1][2:].strip()
                index += 1
                if not item_body:
                    value, index = parse_block(index, indent + 2)
                    items.append(value)
                elif ":" in item_body and not item_body.startswith(("'", '"')):
                    # 列表项是映射（只支持单行键 + 后续缩进子块）
                    key, _, rest = item_body.partition(":")
                    item = {}
                    rest = rest.strip()
                    if rest:
                        item[key.strip()] = _yaml_scalar(rest)
                    else:
                        value, index = parse_block(index, indent + 2)
                        item[key.strip()] = value
                    # 同一列表项的后续键
                    while index < len(lines) and lines[index][0] == indent + 2 and not lines[index][1].startswith("- "):
                        k, _, r = lines[index][1].partition(":")
                        index += 1
                        r = r.strip()
                        if r:
                            item[k.strip()] = _yaml_scalar(r)
                        else:
                            value, index = parse_block(index, indent + 4)
                            item[k.strip()] = value
                    items.append(item)
                else:
                    items.append(_yaml_scalar(item_body))
            return items, index

        mapping = {}
        while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
            body = lines[index][1]
            if ":" not in body:
                raise DevFlowError(f"YAML 子集解析失败，缺少冒号：{body}")
            key, _, rest = body.partition(":")
            key = key.strip()
            rest = rest.strip()
            index += 1
            if rest:
                mapping[key] = _yaml_scalar(rest)
            else:
                value, index = parse_block(index, indent + 2)
                mapping[key] = {} if value is None else value
        return mapping, index

    if not lines:
        return {}
    value, _ = parse_block(0, lines[0][0])
    return value


def load_config_file(path: Path):
    if path.suffix == ".json":
        return load_json(path)
    return mini_yaml_load(read_text(path))


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------


class Paths:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.df = self.root / DF_DIRNAME
        self.project = self.df / "project.json"
        self.decisions = self.df / "decisions.md"
        self.requirements = self.df / "requirements"
        self.req_source = self.requirements / "source"
        self.clarification = self.requirements / "clarification.md"
        self.traceability = self.requirements / "traceability.json"
        self.plans = self.df / "plans"
        self.nodes = self.df / "nodes"
        self.changes = self.df / "changes"
        self.regression = self.df / "regression"
        self.releases = self.df / "releases"
        self.sessions = self.df / "sessions"

    def confirmed_requirements(self, version: int) -> Path:
        return self.requirements / f"confirmed-requirements-v{version}.md"

    def backlog(self, version: int) -> Path:
        return self.plans / f"backlog-v{version}.json"

    def board(self, version: int) -> Path:
        return self.plans / f"board-v{version}.md"

    def graph(self, version: int) -> Path:
        return self.plans / f"dependency-graph-v{version}.md"

    def node_dir(self, node_id: str) -> Path:
        return self.nodes / node_id

    def change_dir(self, change_id: str) -> Path:
        return self.changes / change_id

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


def find_root(start: Path | None = None) -> Path:
    cur = Path(start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / DF_DIRNAME / "project.json").exists():
            return candidate
    raise DevFlowError(
        f"当前目录不在 DevFlow 项目内（未找到 {DF_DIRNAME}/project.json）。先运行 df.py init。"
    )


# --------------------------------------------------------------------------
# 项目与计划
# --------------------------------------------------------------------------


class Project:
    def __init__(self, root: Path):
        self.paths = Paths(root)
        self.data = load_json(self.paths.project)

    @property
    def root(self) -> Path:
        return self.paths.root

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        atomic_write_json(self.paths.project, self.data)

    def plan_versions(self) -> list[int]:
        versions = []
        if self.paths.plans.exists():
            for p in self.paths.plans.glob("backlog-v*.json"):
                m = re.fullmatch(r"backlog-v(\d+)\.json", p.name)
                if m:
                    versions.append(int(m.group(1)))
        return sorted(versions)

    def next_plan_version(self) -> int:
        versions = self.plan_versions()
        return (max(versions) + 1) if versions else 1

    def load_plan(self, version: int) -> dict:
        return load_json(self.paths.backlog(version))

    def current_plan(self, required: bool = True) -> dict | None:
        version = self.data.get("current_plan_version")
        if not version:
            if required:
                raise DevFlowError("当前没有生效的计划。先 plan-import 生成草稿，再由用户确认后 plan-activate。")
            return None
        plan = self.load_plan(version)
        if plan.get("status") != "active":
            if required:
                raise DevFlowError(
                    f"计划 v{version} 的状态是 {plan.get('status')}，不是 active。"
                    "草稿计划不能执行，需先 plan-activate。"
                )
        return plan

    def find_node(self, node_id: str, plan: dict | None = None) -> dict:
        plan = plan or self.current_plan()
        for node in plan["nodes"]:
            if node["node_id"] == node_id:
                return node
        raise DevFlowError(f"计划 v{plan['plan_version']} 中没有节点 {node_id}")

    def save_plan(self, plan: dict) -> None:
        atomic_write_json(self.paths.backlog(plan["plan_version"]), plan)

    def requirement_version(self) -> int | None:
        return self.data.get("current_requirement_version")


def load_traceability(paths: Paths) -> dict | None:
    if paths.traceability.exists():
        return load_json(paths.traceability)
    return None


# --------------------------------------------------------------------------
# 状态与依赖
# --------------------------------------------------------------------------


def dependents_map(nodes: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {n["node_id"]: [] for n in nodes}
    for node in nodes:
        for dep in node.get("dependencies", []):
            out.setdefault(dep, []).append(node["node_id"])
    return out


def topological_order(nodes: list[dict]) -> list[str]:
    """返回拓扑序；存在环时抛错。"""
    indeg = {n["node_id"]: 0 for n in nodes}
    deps = {n["node_id"]: [d for d in n.get("dependencies", []) if d in indeg] for n in nodes}
    for nid, dl in deps.items():
        indeg[nid] = len(set(dl))
    queue = sorted([nid for nid, deg in indeg.items() if deg == 0])
    order = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for other in nodes:
            if nid in deps[other["node_id"]]:
                indeg[other["node_id"]] -= 1
                if indeg[other["node_id"]] == 0:
                    queue.append(other["node_id"])
        queue.sort()
    if len(order) != len(nodes):
        remaining = [nid for nid, deg in indeg.items() if deg > 0]
        raise DevFlowError(f"任务依赖存在循环，涉及节点：{', '.join(sorted(remaining))}")
    return order


def find_cycles(nodes: list[dict]) -> list[list[str]]:
    graph = {n["node_id"]: list(n.get("dependencies", [])) for n in nodes}
    cycles: list[list[str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in graph}
    stack: list[str] = []

    def dfs(node: str):
        color[node] = GRAY
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if color[dep] == GRAY:
                idx = stack.index(dep)
                cycles.append(stack[idx:] + [dep])
            elif color[dep] == WHITE:
                dfs(dep)
        stack.pop()
        color[node] = BLACK

    for nid in sorted(graph):
        if color[nid] == WHITE:
            dfs(nid)
    return cycles


def unmet_dependencies(node: dict, plan: dict) -> list[str]:
    by_id = {n["node_id"]: n for n in plan["nodes"]}
    missing = []
    for dep in node.get("dependencies", []):
        target = by_id.get(dep)
        if target is None or target.get("status") != "completed":
            missing.append(dep)
    return missing


def recompute_ready(plan: dict) -> list[tuple[str, str, str]]:
    """把依赖已全部完成的 pending 节点提升为 ready。

    只做 pending -> ready 的单向提升；不会把 ready 退回 pending。
    返回 [(node_id, old, new)]。
    """
    changes = []
    for node in plan["nodes"]:
        if node.get("status") != "pending":
            continue
        if not unmet_dependencies(node, plan):
            node["status"] = "ready"
            changes.append((node["node_id"], "pending", "ready"))
    return changes


def ready_nodes(plan: dict) -> list[dict]:
    return [
        n
        for n in plan["nodes"]
        if n.get("status") == "ready" and not unmet_dependencies(n, plan)
    ]


def stats(plan: dict) -> dict:
    nodes = plan["nodes"]
    counts = {status: 0 for status in NODE_STATUSES}
    for node in nodes:
        counts[node.get("status", "pending")] = counts.get(node.get("status", "pending"), 0) + 1
    effective = [
        n for n in nodes if n.get("status") not in ("superseded", "cancelled")
    ]
    done = counts.get("completed", 0)
    percent = round(done * 100.0 / len(effective), 1) if effective else 0.0
    return {
        "total": len(nodes),
        "effective": len(effective),
        "counts": counts,
        "completed": done,
        "percent": percent,
        "ready": len(ready_nodes(plan)),
        "groups": parallel_groups(plan),
    }


def parallel_groups(plan: dict) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for node in plan["nodes"]:
        group = node.get("parallel_group")
        if group and node.get("status") in ("ready", "pending", "in_progress"):
            groups.setdefault(group, []).append(node["node_id"])
    return {k: sorted(v) for k, v in sorted(groups.items())}


# --------------------------------------------------------------------------
# 证据
# --------------------------------------------------------------------------


def report_path(paths: Paths, node: dict, kind: str) -> Path:
    """节点报告路径：优先用节点里登记的相对路径，否则用约定路径。

    约定路径让执行 Agent 完全不需要碰 backlog.json：
      .devflow/nodes/DF-001/execution-report.md
      .devflow/nodes/DF-001/test-report.json
    """
    ref = node.get(kind)
    if ref:
        return paths.root / ref
    name = "test-report.json" if kind == "test_report" else "execution-report.md"
    return paths.node_dir(node["node_id"]) / name


def load_test_report(paths: Paths, node: dict) -> dict | None:
    path = report_path(paths, node, "test_report")
    if not path.exists():
        return None
    return load_json(path)


def evidence_problems(paths: Paths, node: dict, gates: dict | None = None) -> list[str]:
    """验收门槛：返回阻止 completed 的问题列表（空列表=可验收）。"""
    gates = (gates or {}).get("gates", {}).get("node_accept", {})
    problems: list[str] = []
    node_id = node["node_id"]

    if gates.get("require_execution_report", True):
        path = report_path(paths, node, "execution_report")
        if not path.exists():
            problems.append(f"{node_id} 缺少执行报告 {paths.rel(path)}")
        else:
            evidence = load_execution_evidence(paths, node)
            if evidence is None:
                problems.append(
                    f"{node_id} 的执行报告里没有 ```devflow-execution 证据块，无法核实改动与测试"
                )
            else:
                if evidence.get("node_id") not in (None, node_id):
                    problems.append(
                        f"{node_id} 执行报告证据块的 node_id 是 {evidence.get('node_id')}"
                    )
                if evidence.get("result") == "blocked":
                    problems.append(f"{node_id} 执行报告标记为 blocked，不能验收")

    if node.get("no_test"):
        if not node.get("no_test_reason"):
            problems.append(f"{node_id} 标记了 no_test 但没有写 no_test_reason")
        return problems

    report = load_test_report(paths, node)
    if report is None:
        if gates.get("require_test_report", True):
            problems.append(f"{node_id} 缺少测试证据 test-report.json")
        return problems

    if report.get("node_id") != node_id:
        problems.append(
            f"{node_id} 的 test-report.json 里 node_id 是 {report.get('node_id')}，对不上"
        )

    status = report.get("status")
    if gates.get("require_tests_passed", True) and status != "passed":
        problems.append(f"{node_id} 测试状态是 {status}，只有 passed 才能验收")

    if gates.get("require_commands_executed", True):
        commands = report.get("commands") or []
        if not commands:
            problems.append(f"{node_id} 测试报告没有记录任何实际执行的命令")
        elif all(int(c.get("exit_code", 0)) != 0 for c in commands):
            problems.append(f"{node_id} 记录的命令全部非零退出，不能算通过")

    summary = report.get("summary") or {}
    if summary.get("failed", 0) > 0:
        problems.append(f"{node_id} 测试报告里 failed={summary.get('failed')}")
    return problems


EXEC_BLOCK_PATTERNS = [
    re.compile(r"```devflow-execution\s*\n(.*?)\n```", re.S),
    re.compile(r"```json\s+devflow-execution\s*\n(.*?)\n```", re.S),
]


def parse_execution_evidence(text: str) -> dict | None:
    """从 execution-report.md 里取出机器可读证据块。

    执行报告是给人看的 Markdown，但里面必须带一个围栏代码块：

        ```devflow-execution
        {"node_id": "DF-001", "result": "success", "modified_files": [...]}
        ```

    没有这个块，主控就无法核实"改了哪些文件、测试是不是真的跑了"。
    """
    for pattern in EXEC_BLOCK_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise DevFlowError(f"执行报告里的 devflow-execution 块不是合法 JSON：{exc}")
    return None


def load_execution_evidence(paths: Paths, node: dict) -> dict | None:
    path = report_path(paths, node, "execution_report")
    if not path.exists():
        return None
    return parse_execution_evidence(read_text(path))


def out_of_scope_files(node: dict, modified: list[str]) -> list[str]:
    """执行报告里改了但不在 files_scope 内的文件（只做提示）。"""
    scope = [s.replace("\\", "/").rstrip("/").lstrip("./") for s in node.get("files_scope", [])]
    if not scope:
        return []
    outs = []
    for item in modified:
        norm = str(item).replace("\\", "/").lstrip("./")
        if not any(norm == s or norm.startswith(s + "/") for s in scope):
            outs.append(norm)
    return outs
