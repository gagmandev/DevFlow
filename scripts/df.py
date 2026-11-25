#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DevFlow 主控命令行。

设计前提：**只有这个脚本写全局计划文件**。执行 Agent 只写业务代码和自己节点目录下的
报告，不碰 .devflow/plans/。这样多个 Agent 并行时不会互相覆盖看板。

    python df.py init --name "示例项目"
    python df.py status
    python df.py next
    python df.py start DF-003
    python df.py sync DF-003
    python df.py accept DF-003
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core
import render_board
import validate as validator
from dfcore import DevFlowError

SKILL_DIR = Path(__file__).resolve().parent.parent

# 所有会写全局状态的命令。main() 在执行它们之前统一加锁，
# 命令内部再读 project.json / backlog 时拿到的就是锁后的最新状态。
MUTATING_COMMANDS = {
    "project-set",
    "req-import",
    "req-confirm",
    "plan-import",
    "plan-activate",
    "prompt-init",
    "start",
    "sync",
    "accept",
    "block",
    "fail",
    "retry",
    "cancel",
    "supersede",
    "change-new",
    "change-set",
    "change-apply",
    "regression-check",
    "release",
    "release-ready",
    "release-accept",
    "recover",
    "migrate",
}

# accepted 是终态：没走变更流程就不能再改节点或重跑交付。
# 想继续开发只有一条路：change-new → change-apply（新计划 + 状态回 in_progress）。
FROZEN_ON_ACCEPTED = {
    "start",
    "sync",
    "accept",
    "block",
    "fail",
    "retry",
    "cancel",
    "supersede",
    "plan-activate",
    "regression-check",
    "release",
    "release-ready",
}

NODE_DEFAULTS = {
    "parallelizable": False,
    "parallel_group": None,
    "inputs": [],
    "outputs": [],
    "files_scope": [],
    "test_strategy": [],
    "no_test": False,
    "no_test_reason": None,
    "status": "pending",
    "context_file": None,
    "execution_report": None,
    "test_report": None,
    "owner": None,
    "attempts": 0,
    "current_attempt_id": None,
    "attempt_number": None,
    "prompt_version": 0,
    "git_baseline": None,
    "git_actual_changes": [],
    "git_discrepancies": [],
    "git_waivers": [],
    "scope_violations": [],
    "scope_waivers": [],
    "started_at": None,
    "completed_at": None,
    "accepted_by": None,
    "superseded_by": None,
    "blocked_reason": None,
    "remark": "",
}


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------


def ctx(args) -> tuple[Path, core.Paths, core.Project]:
    root = Path(args.project).resolve()
    if not (root / core.DF_DIRNAME / "project.json").exists():
        root = core.find_root(root)
    return root, core.Paths(root), core.Project(root)


def load_config() -> dict:
    return validator.load_config(SKILL_DIR)


def refresh(project: core.Project, plan: dict, quiet: bool = False) -> list[str]:
    """落盘计划 + 重算 ready + 重渲染看板。所有状态变更都必须走这里。"""
    unlocked = [c[0] for c in core.recompute_ready(plan)]
    project.save_plan(plan)
    text = render_board.render(plan, project.data, include_time=True)
    core.atomic_write_text(project.paths.board(plan["plan_version"]), text)
    if not quiet:
        print(f"看板已更新：{project.paths.rel(project.paths.board(plan['plan_version']))}")
    return unlocked


def transition(node: dict, new: str, **fields) -> None:
    old = node.get("status", "pending")
    if old == new:
        node.update({k: v for k, v in fields.items() if v is not None})
        return
    allowed = core.TRANSITIONS.get(old, set())
    if new not in allowed:
        raise DevFlowError(
            f"{node['node_id']}: 不允许从 {old} 变为 {new}（合法目标：{', '.join(sorted(allowed)) or '无'}）"
        )
    node["status"] = new
    for key, value in fields.items():
        if value is not None:
            node[key] = value


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if slug:
        return slug[:40]
    return "proj-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]


def detect_project_type(root: Path) -> str:
    ignore = {
        ".git", ".devflow", "node_modules", "venv", ".venv", "__pycache__",
        "dist", "build", ".idea", ".vscode",
    }
    code_ext = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cs",
        ".rb", ".php", ".kt", ".swift", ".c", ".cpp", ".h", ".vue", ".svelte",
    }
    found = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore]
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= 3:
            dirnames[:] = []
        for name in filenames:
            if Path(name).suffix.lower() in code_ext:
                found += 1
                if found > 3:
                    return "existing"
    return "greenfield"


# --------------------------------------------------------------------------
# init / project-set
# --------------------------------------------------------------------------


def cmd_init(args) -> int:
    root = Path(args.project).resolve()
    paths = core.Paths(root)
    if paths.project.exists() and not args.force:
        raise DevFlowError(f"{paths.rel(paths.df)} 已存在。要继续初始化请加 --force（会保留已有文件）")

    project_type = args.type or detect_project_type(root)
    project_id = args.id or slugify(args.name or root.name)

    for directory in (
        paths.df, paths.requirements, paths.req_source, paths.plans,
        paths.nodes, paths.changes, paths.regression, paths.releases, paths.sessions,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    if not paths.project.exists():
        project = {
            "schema_version": core.SCHEMA_VERSION,
            "project_id": project_id,
            "project_name": args.name or root.name,
            "project_type": project_type,
            "status": "initialized",
            "created_at": core.now_iso(),
            "updated_at": core.now_iso(),
            "current_plan_version": None,
            "current_requirement_version": None,
            "tech_stack": {},
            "commands": {"install": None, "build": None, "test": None, "lint": None, "run": None},
            "constraints": [],
            "conventions": {},
            "analysis": {},
            "notes": "",
        }
        core.atomic_write_json(paths.project, project)
    else:
        project = core.load_json(paths.project)
        project["project_type"] = project_type

    if not paths.decisions.exists():
        core.atomic_write_text(paths.decisions, _decisions_seed(project))

    config = load_config()
    if config.get("defaults", {}).get("project", {}).get("update_gitignore", True):
        _update_gitignore(root)

    print(f"DevFlow 初始化完成：{paths.rel(paths.df)}")
    print(f"- project_id: {project_id}")
    print(f"- project_type: {project_type}" + ("（自动检测）" if not args.type else ""))
    print("")
    print("下一步：")
    print("  1. 分析现有项目结构 / 技术选型，写入 .devflow/project.json（project-set）")
    print("  2. 把需求原文放到 .devflow/requirements/source/，然后进入 analyze")
    return 0


def _decisions_seed(project: dict) -> str:
    return f"""# 决策记录 · {project['project_name']}

只记"改变了做法"的决定。每条写清：背景、选项、结论、影响。

格式：

## D-001 · <决策标题>
- 日期：{core.now_iso()[:10]}
- 状态：accepted
- 背景：
- 选项：
- 结论：
- 影响：

---

（暂无记录）
"""


def _update_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    block = "\n# DevFlow（计划与报告要提交，锁文件不提交）\n.devflow/.lock\n.devflow/**/*.tmp\n"
    existing = core.read_text(path) if path.exists() else ""
    if ".devflow/.lock" in existing:
        return
    core.atomic_write_text(path, existing.rstrip() + block if existing else block.lstrip())


def cmd_project_set(args) -> int:
    _, paths, project = ctx(args)
    patch = core.load_json(Path(args.patch))
    merged = core.deep_merge(project.data, patch)
    sv = validator.SchemaValidator(SKILL_DIR / "schemas")
    errors = sv.validate(merged, "project.schema.json")
    if errors:
        raise DevFlowError("project.json 校验失败：\n  " + "\n  ".join(errors))
    project.data = merged
    project.save()
    print(f"已更新 {paths.rel(paths.project)}")
    return 0


# --------------------------------------------------------------------------
# 需求
# --------------------------------------------------------------------------


def cmd_req_import(args) -> int:
    _, paths, project = ctx(args)
    data = core.load_json(Path(args.file))
    sv = validator.SchemaValidator(SKILL_DIR / "schemas")
    errors = sv.validate(data, "requirements.schema.json")
    if errors:
        raise DevFlowError("需求追踪表校验失败：\n  " + "\n  ".join(errors))

    version = args.version or data.get("requirement_version") or 1
    data["requirement_version"] = version
    versioned = paths.requirements / f"traceability-v{version}.json"
    core.atomic_write_json(versioned, data)
    # traceability.json 始终是"当前版本"的副本，历史版本留在 traceability-vN.json
    core.atomic_write_json(paths.traceability, data)
    print(f"已写入 {paths.rel(versioned)}：{len(data['requirements'])} 条需求（v{version}）")
    print(f"当前指针已更新：{paths.rel(paths.traceability)}")

    uncovered = [r["req_id"] for r in data["requirements"] if not r.get("acceptance")]
    if uncovered:
        print(f"! 这些需求没有验收标准：{', '.join(uncovered)}")
    return 0


def cmd_req_confirm(args) -> int:
    _, paths, project = ctx(args)
    version = args.version or project.data.get("current_requirement_version") or 1
    doc = paths.confirmed_requirements(version)
    if not doc.exists():
        raise DevFlowError(f"缺少需求基准文件 {paths.rel(doc)}，不能确认")
    text = core.read_text(doc)
    if len(text.strip()) < 200:
        raise DevFlowError(f"{paths.rel(doc)} 内容过短，不像一份需求基准")
    for section in ("验收标准", "业务规则", "功能需求"):
        if section not in text:
            print(f"! 需求基准里没有出现「{section}」段落，确认前先检查")

    project.data["current_requirement_version"] = version
    project.data["status"] = "requirements_confirmed"
    project.save()
    print(f"需求基准 v{version} 已生效：{paths.rel(doc)}")
    print("下一步：plan（拆任务 → 生成草稿计划 → 用户审核 → plan-activate）")
    return 0


# --------------------------------------------------------------------------
# 计划
# --------------------------------------------------------------------------


def _normalize_node(node: dict, version: int) -> dict:
    out = dict(NODE_DEFAULTS)
    out.update(node)
    nid = out.get("node_id", "")
    out.setdefault("prompt_file", f".devflow/nodes/{nid}/prompt.md")
    if not out.get("prompt_file"):
        out["prompt_file"] = f".devflow/nodes/{nid}/prompt.md"
    if not out.get("created_in_plan"):
        out["created_in_plan"] = version
    if not out.get("remark"):
        out["remark"] = ""
    return out


def cmd_plan_import(args) -> int:
    _, paths, project = ctx(args)
    raw = core.load_json(Path(args.file))
    version = args.version or raw.get("plan_version") or project.next_plan_version()
    target = paths.backlog(version)

    if target.exists() and not args.force:
        raise DevFlowError(
            f"计划 v{version} 已存在（{paths.rel(target)}）。历史计划不覆盖；"
            "要新建版本请用 --version N，确实要重写草稿请加 --force"
        )
    if target.exists() and args.force:
        history = paths.plans / ".history"
        history.mkdir(exist_ok=True)
        stamp = core.now_iso().replace(":", "").replace("-", "")[:15]
        shutil.copy2(target, history / f"backlog-v{version}.{stamp}.json")

    plan = {
        "schema_version": core.SCHEMA_VERSION,
        "project_id": project.data["project_id"],
        "plan_version": version,
        "requirement_version": raw.get("requirement_version")
        or project.data.get("current_requirement_version")
        or 1,
        "status": "draft",
        "created_at": core.now_iso(),
        "approved_at": None,
        "approved_by": None,
        "based_on": raw.get("based_on"),
        "stats_note": raw.get("stats_note", ""),
        "parallel_exceptions": raw.get("parallel_exceptions") or [],
        "nodes": [_normalize_node(n, version) for n in raw.get("nodes", [])],
    }

    core.recompute_ready(plan)
    core.atomic_write_json(target, plan)
    report = validator.validate_all(paths.root, SKILL_DIR, version)
    if report.errors:
        if not args.keep_broken:
            target.unlink(missing_ok=True)
        raise DevFlowError(
            "计划校验失败，未导入（加 --keep-broken 可保留文件自行修）：\n  "
            + "\n  ".join(report.errors)
        )

    print(f"草稿计划已生成：{paths.rel(target)}（{len(plan['nodes'])} 个节点，status=draft）")
    for warning in report.warnings:
        print(f"! {warning}")

    # 缺提示词的节点直接提示，避免到执行时才发现
    missing = [
        n["node_id"]
        for n in plan["nodes"]
        if not (paths.root / n["prompt_file"]).exists()
        and n["status"] not in ("superseded", "cancelled")
    ]
    if missing:
        print(f"! 以下节点还没有提示词文件：{', '.join(missing)}")
        print("  按 templates/node-prompt.md 生成 .devflow/nodes/<ID>/prompt.md 后重新校验")

    render_board.main(["--project", str(paths.root), "--plan", str(version)])
    dependency_check_main = _run_dependency_check(paths.root, version)
    if dependency_check_main != 0:
        print("! 依赖检查有问题，先修依赖再看板")
    print("")
    print("计划处于 draft 状态，不能执行。请用户审核任务边界/依赖/优先级后运行：")
    print(f"  python df.py plan-activate --version {version}")
    return 0


def _run_dependency_check(root: Path, version: int) -> int:
    import dependency_check

    return dependency_check.main(["--project", str(root), "--plan", str(version), "--write"])


def cmd_graph(args) -> int:
    import dependency_check

    return dependency_check.main(["--project", args.project, "--write"])


def cmd_validate(args) -> int:
    argv = ["--project", args.project]
    if args.plan:
        argv += ["--plan", str(args.plan)]
    if args.strict:
        argv += ["--strict"]
    if args.json:
        argv += ["--json"]
    return validator.main(argv)


def cmd_plan_activate(args) -> int:
    _, paths, project = ctx(args)
    version = args.version
    plan = core.load_json(paths.backlog(version))
    if plan.get("status") == "active":
        print(f"计划 v{version} 已经是生效计划")
        return 0
    if plan.get("status") != "draft":
        raise DevFlowError(f"计划 v{version} 状态是 {plan.get('status')}，只有 draft 能激活")

    rv = plan.get("requirement_version")
    if rv and not paths.confirmed_requirements(rv).exists():
        raise DevFlowError(
            f"计划基于需求基准 v{rv}，但 {paths.rel(paths.confirmed_requirements(rv))} 不存在。"
            "先确认需求（req-confirm）再激活计划。"
        )

    report = validator.validate_all(paths.root, SKILL_DIR, version, strict=False, activating=True)
    if report.errors:
        print("✗ 计划有错误，不能激活：")
        for item in report.errors:
            print(f"  - {item}")
        return 1
    for warning in report.warnings:
        print(f"! {warning}")

    old_version = project.data.get("current_plan_version")
    if old_version and old_version != version:
        old = core.load_json(paths.backlog(old_version))
        old["status"] = "superseded"
        core.atomic_write_json(paths.backlog(old_version), old)

    plan["status"] = "active"
    plan["approved_at"] = core.now_iso()
    plan["approved_by"] = args.by or "user"
    core.atomic_write_json(paths.backlog(version), plan)

    project.data["current_plan_version"] = version
    project.data["current_requirement_version"] = plan.get(
        "requirement_version", project.data.get("current_requirement_version")
    )
    project.data["status"] = "planned"
    project.save()

    refresh(project, plan)
    print(f"计划 v{version} 已生效（approved_by={plan['approved_by']}）")
    print(f"当前可执行：{', '.join(n['node_id'] for n in core.ready_nodes(plan)) or '无（检查依赖）'}")
    return 0


# --------------------------------------------------------------------------
# 查看
# --------------------------------------------------------------------------


def cmd_status(args) -> int:
    _, paths, project = ctx(args)
    data = project.data
    print(f"== {data['project_name']} · {data['project_id']} ==")
    print(f"类型：{data['project_type']} | 状态：{data['status']}")
    rv = data.get("current_requirement_version")
    print(f"需求基准：{'v' + str(rv) if rv else '未确认'}")
    plan = project.current_plan(required=False)
    if not plan:
        draft = project.next_plan_version() - 1
        if draft >= 1:
            print(f"计划：v{draft} 仍是草稿（未激活）")
        else:
            print("计划：无")
        return 0

    st = core.stats(plan)
    counts = st["counts"]
    print(
        f"生效计划：v{plan['plan_version']}（{plan['status']}，{st['total']} 节点）"
        f" | 需求 v{plan.get('requirement_version')}"
    )
    print(f"进度：{st['percent']:.1f}%（{st['completed']}/{st['effective']}）")
    print("")
    print(f"可执行 {counts.get('ready', 0)}：{', '.join(n['node_id'] for n in core.ready_nodes(plan)) or '无'}")
    if counts.get("in_progress"):
        ids = [n["node_id"] for n in plan["nodes"] if n["status"] == "in_progress"]
        print(f"执行中 {len(ids)}：{', '.join(ids)}")
    if counts.get("needs_review"):
        ids = [n["node_id"] for n in plan["nodes"] if n["status"] == "needs_review"]
        print(f"待验收 {len(ids)}：{', '.join(ids)}")
    stuck = [n for n in plan["nodes"] if n["status"] in ("blocked", "failed")]
    if stuck:
        print(f"阻塞/失败 {len(stuck)}：")
        for node in stuck:
            print(f"  - {node['node_id']}（{node['status']}）：{node.get('blocked_reason') or node.get('remark') or '未填原因'}")
    print("")
    print(f"看板：{paths.rel(paths.board(plan['plan_version']))}")
    return 0


def cmd_next(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    ready = core.ready_nodes(plan)
    if args.json:
        print(json.dumps(
            [
                {
                    "node_id": n["node_id"],
                    "node_name": n["node_name"],
                    "priority": n["priority"],
                    "parallel_group": n.get("parallel_group"),
                    "prompt_file": n["prompt_file"],
                }
                for n in ready
            ],
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    if not ready:
        print("当前没有可执行节点。")
        waiting = [
            (n["node_id"], core.unmet_dependencies(n, plan))
            for n in plan["nodes"]
            if n["status"] == "pending"
        ]
        for nid, deps in waiting:
            print(f"  {nid} 等待：{', '.join(deps)}")
        stuck = [n for n in plan["nodes"] if n["status"] in ("blocked", "failed")]
        for node in stuck:
            print(f"  {node['node_id']} {node['status']}：{node.get('blocked_reason') or '未填原因'}")
        return 0

    groups: dict[str, list[dict]] = {}
    solo = []
    for node in ready:
        if node.get("parallel_group"):
            groups.setdefault(node["parallel_group"], []).append(node)
        else:
            solo.append(node)

    print(f"可执行节点 {len(ready)} 个：")
    for node in solo:
        print(f"- {node['node_id']} {node['node_name']}（{node['priority']}）")
        print(f"  提示词：{node['prompt_file']}")
    for group, members in sorted(groups.items()):
        print(f"- 并行组 {group}（{len(members)} 个，确认无文件冲突再并行）：")
        for node in members:
            print(f"    {node['node_id']} {node['node_name']}（{node['priority']}）→ {node['prompt_file']}")
    print("")
    print("投喂方式：把提示词文件全文交给 Code Agent；它只改 files_scope 内的文件，")
    print("完成后产出 execution-report.md + test-report.json，再回来 sync。")
    return 0


def cmd_task(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    if args.json:
        print(json.dumps(node, ensure_ascii=False, indent=2))
        return 0
    if args.prompt:
        path = paths.root / node["prompt_file"]
        if not path.exists():
            raise DevFlowError(f"提示词文件不存在：{node['prompt_file']}")
        sys.stdout.write(core.read_text(path))
        return 0

    print(f"== {node['node_id']} {node['node_name']} ==")
    print(f"阶段/类型：{node['phase']} / {node['type']} | 优先级：{node['priority']} | 状态：{node['status']}")
    print(f"描述：{node['description']}")
    print(f"关联需求：{', '.join(node['requirement_ids']) or '无'}")
    deps = node.get("dependencies") or []
    if deps:
        by_id = {n["node_id"]: n for n in plan["nodes"]}
        print("前置依赖：")
        for dep in deps:
            target = by_id.get(dep, {})
            print(f"  - {dep} {target.get('node_name', '(缺失)')} [{target.get('status', '?')}]")
    else:
        print("前置依赖：无")
    print(f"修改范围：{', '.join(node['files_scope']) or '（未限定）'}")
    print(f"测试策略：{'；'.join(node['test_strategy']) or '（未填写）'}")
    print("验收标准：")
    for item in node["acceptance_criteria"]:
        print(f"  - {item}")
    print(f"提示词：{node['prompt_file']}")
    print(f"执行报告：{paths.rel(core.report_path(paths, node, 'execution_report'))}")
    print(f"测试证据：{paths.rel(core.report_path(paths, node, 'test_report'))}")
    if node.get("blocked_reason"):
        print(f"阻塞原因：{node['blocked_reason']}")
    return 0


# --------------------------------------------------------------------------
# 执行 / 同步 / 验收
# --------------------------------------------------------------------------


def resolve_plan(project: core.Project, version: int | None = None) -> dict:
    if version:
        return core.load_json(project.paths.backlog(version))
    current = project.data.get("current_plan_version")
    if current:
        return core.load_json(project.paths.backlog(current))
    versions = project.plan_versions()
    if not versions:
        raise DevFlowError("还没有任何计划文件，先 plan-import")
    return core.load_json(project.paths.backlog(max(versions)))


def _bullets(items, empty: str = "（无）") -> str:
    items = [str(i) for i in (items or []) if str(i).strip()]
    return "\n".join(f"- {i}" for i in items) if items else empty


def _build_prompt(paths: core.Paths, project: core.Project, plan: dict, node: dict) -> str:
    template = core.read_text(SKILL_DIR / "templates" / "node-prompt.md")
    by_id = {n["node_id"]: n for n in plan["nodes"]}
    trace = core.load_traceability(paths) or {}
    req_by_id = {r["req_id"]: r for r in trace.get("requirements", [])}
    rv = plan.get("requirement_version") or project.data.get("current_requirement_version") or 1

    req_lines = []
    for rid in node.get("requirement_ids", []):
        req = req_by_id.get(rid)
        if req:
            req_lines.append(f"- **{rid} · {req.get('title', '')}**：{req.get('statement', '')}")
            for acc in req.get("acceptance", [])[:4]:
                req_lines.append(f"  - 验收：{acc}")
        else:
            req_lines.append(f"- {rid}（需求追踪表里没有这条，先确认编号是否写错）")
    requirement_block = "\n".join(req_lines) or "- 本节点不直接对应功能需求（基础设施 / 交付类）。"

    dep_lines = []
    for dep in node.get("dependencies", []):
        target = by_id.get(dep)
        if not target:
            dep_lines.append(f"- {dep}（不在本计划中，先修依赖）")
            continue
        dep_lines.append(f"- **{dep} · {target['node_name']}**（当前状态：{target.get('status')}）")
        if target.get("outputs"):
            dep_lines.append(f"  - 已产出：{', '.join(target['outputs'])}")
        if target.get("acceptance_criteria"):
            dep_lines.append(f"  - 它保证的事：{target['acceptance_criteria'][0]}")
    dependency_block = "\n".join(dep_lines) or "无前置依赖，可以从零开始。"

    read_lines = list(node.get("inputs") or [])
    read_lines.append(f".devflow/requirements/confirmed-requirements-v{rv}.md（只查上面列出的需求编号）")
    for dep in node.get("dependencies", []):
        read_lines.extend(by_id.get(dep, {}).get("outputs", []))
    if paths.decisions.exists():
        read_lines.append(".devflow/decisions.md（只看与本节点相关的决策）")
    read_block = _bullets(dict.fromkeys(read_lines))

    scope = node.get("files_scope") or []
    if scope:
        scope_block = "允许新增/修改：\n" + _bullets(scope)
    else:
        scope_block = "本节点没有限定文件范围，但**只允许改实现本节点目标所必需的文件**。"

    others = []
    for other in plan["nodes"]:
        if other["node_id"] == node["node_id"]:
            continue
        if other.get("status") in ("superseded", "cancelled"):
            continue
        others.extend(other.get("files_scope", []))
    conflicts = sorted({o for o in others if o in scope})
    if conflicts:
        scope_block += (
            "\n\n> ⚠️ 这些路径同时出现在其他节点的范围里，动手前先确认边界：\n"
            + _bullets(conflicts)
        )

    constraint_lines = [
        f"技术栈：{json.dumps(project.data.get('tech_stack', {}), ensure_ascii=False)}",
        f"测试命令：{project.data.get('commands', {}).get('test') or '（项目未配置，使用项目现有测试框架）'}",
        f"构建命令：{project.data.get('commands', {}).get('build') or '（无）'}",
    ]
    constraint_lines.extend(f"硬约束：{c}" for c in project.data.get("constraints", []))
    for key, value in (project.data.get("conventions") or {}).items():
        constraint_lines.append(f"约定 · {key}：{value}")
    constraint_lines.append("遵循现有代码的命名、分层、错误处理风格；不引入新的第三方运行时依赖。")
    constraint_block = _bullets(constraint_lines)

    forbid_block = _bullets([
        "不修改本节点 files_scope 之外的业务代码",
        "不修改 .devflow/plans/*.json、看板、其他节点的目录",
        "不改写需求基准文档；发现需求冲突时停下来报告",
        "不重复实现已存在的公共能力（先读依赖产物）",
        "不删除或跳过已有测试来让流水线变绿",
        "不把没执行过的测试写成 passed",
        "不提交任何密钥、令牌、真实用户数据",
    ])

    acceptance_block = "\n".join(
        f"- [ ] {item}" for item in node.get("acceptance_criteria", [])
    ) or "- [ ] （验收标准缺失，先补节点定义）"

    test_lines = list(node.get("test_strategy") or [])
    if node.get("no_test"):
        test_lines.append(f"本节点无需自动化测试：{node.get('no_test_reason') or '（未说明原因）'}")
    test_lines.append("记录每条命令的退出码和通过/失败数量，写进 test-report.json。")
    test_block = _bullets(test_lines)

    deliverable_block = _bullets(
        list(node.get("outputs") or [])
        + [
            f"`{paths.rel(core.report_path(paths, node, 'execution_report'))}`",
            f"`{paths.rel(core.report_path(paths, node, 'test_report'))}`",
        ]
    )

    mapping = {
        "node_id": node["node_id"],
        "node_name": node["node_name"],
        "phase": node["phase"],
        "priority": node["priority"],
        "description": node["description"],
        "attempt_id": "<start 命令输出的 attempt_id>",
        "plan_version": str(plan["plan_version"]),
        "requirement_version": str(rv),
        "project_name": project.data["project_name"],
        "project_background": project.data.get("notes")
        or f"{project.data['project_name']}（{project.data['project_type']}）。"
        "技术栈与约束见下方「技术约束」。",
        "requirement_version": str(rv),
        "requirement_block": requirement_block,
        "dependency_block": dependency_block,
        "read_block": read_block,
        "scope_block": scope_block,
        "steps_block": "1. 读上面的必读文件，确认现有接口与数据结构\n"
                       "2. 按目标实现（先写最小可运行版本）\n"
                       "3. 补测试，跑通\n"
                       "4. 自查验收标准，写执行报告\n"
                       "\n> 由拆解者补充本节点特有的实现细节。",
        "constraint_block": constraint_block,
        "forbid_block": forbid_block,
        "acceptance_block": acceptance_block,
        "test_block": test_block,
        "deliverable_block": deliverable_block,
        "report_path": paths.rel(core.report_path(paths, node, "execution_report")),
        "test_report_path": paths.rel(core.report_path(paths, node, "test_report")),
    }
    for key, value in mapping.items():
        template = template.replace("{{" + key + "}}", value)

    header = (
        "---\n"
        f"project_id: {project.data['project_id']}\n"
        f"node_id: {node['node_id']}\n"
        f"plan_version: {plan['plan_version']}\n"
        f"requirement_version: {rv}\n"
        f"prompt_version: {int(node.get('prompt_version', 0)) + 1}\n"
        f"generated_at: {core.now_iso()}\n"
        "---\n\n"
    )
    return header + template


def _archive_prompt(paths: core.Paths, node: dict, path: Path) -> str | None:
    """覆盖前把旧提示词按版本号存进 history/，历史不丢。"""
    if not path.exists():
        return None
    hist = paths.node_dir(node["node_id"]) / "history"
    hist.mkdir(parents=True, exist_ok=True)
    version = int(node.get("prompt_version", 0) or 0)
    target = hist / f"prompt.v{version}.md"
    if target.exists():
        stamp = re.sub(r"[-:T+]", "", core.now_iso())[:14]
        target = hist / f"prompt.v{version}.{stamp}.md"
    shutil.move(str(path), str(target))
    return paths.rel(target)


def cmd_prompt_init(args) -> int:
    _, paths, project = ctx(args)
    plan = resolve_plan(project, args.plan)
    targets = (
        [n for n in plan["nodes"] if n["status"] not in ("superseded", "cancelled")]
        if args.all or not args.node_id
        else [project.find_node(args.node_id, plan)]
    )
    written, skipped, archived = [], [], []
    for node in targets:
        path = paths.root / node["prompt_file"]
        if path.exists() and not args.force:
            skipped.append(node["node_id"])
            continue
        old = _archive_prompt(paths, node, path)
        if old:
            archived.append(old)
        core.atomic_write_text(path, _build_prompt(paths, project, plan, node))
        node["prompt_version"] = int(node.get("prompt_version", 0)) + 1
        written.append(node["node_id"])

    if written:
        project.save_plan(plan)
        print(f"已生成提示词：{', '.join(written)}（版本已绑定到计划 v{plan['plan_version']}）")
    if archived:
        print(f"旧提示词已归档 {len(archived)} 份：{archived[0]}{' …' if len(archived) > 1 else ''}")
    if skipped:
        print(f"已存在，跳过：{', '.join(skipped)}（要覆盖加 --force；旧版本提示词不能直接执行）")
    if written:
        print("下一步：补全每个提示词里「实现步骤」的具体细节，再 plan-import / validate。")
    return 0


def archive_reports(paths: core.Paths, node: dict) -> list[str]:
    """返工/重试前把旧报告挪进 history/，避免覆盖历史证据。"""
    hist = paths.node_dir(node["node_id"]) / "history"
    stamp = core.now_iso().replace(":", "").replace("-", "")[:13]
    moved = []
    for kind, name in (("execution_report", "execution-report.md"), ("test_report", "test-report.json")):
        path = core.report_path(paths, node, kind)
        if not path.exists():
            continue
        hist.mkdir(parents=True, exist_ok=True)
        target = hist / f"{path.stem}.{stamp}{path.suffix}"
        shutil.move(str(path), str(target))
        moved.append(paths.rel(target))
    return moved


def _new_attempt(node: dict) -> None:
    """开一轮新执行：生成 attempt_id，旧报告从此不能冒充本轮结果。"""
    node["current_attempt_id"] = uuid.uuid4().hex[:12]
    node["attempts"] = int(node.get("attempts", 0)) + 1
    node["attempt_number"] = node["attempts"]
    node["scope_violations"] = []


def cmd_start(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)

    if node["status"] == "in_progress":
        print(f"{node['node_id']} 已经在执行中（owner={node.get('owner')}）")
        return 0
    if node["status"] == "completed":
        raise DevFlowError(f"{node['node_id']} 已完成。要返工请用 retry --reopen")
    unmet = core.unmet_dependencies(node, plan)
    if unmet:
        raise DevFlowError(
            f"{node['node_id']} 的前置依赖未完成：{', '.join(unmet)}。不能开始执行。"
        )
    if node["status"] == "pending":
        transition(node, "ready")
    prompt_path = paths.root / node["prompt_file"]
    if not prompt_path.exists():
        raise DevFlowError(f"{node['node_id']} 的提示词不存在：{node['prompt_file']}（先 prompt-init）")
    meta = core.parse_prompt_meta(core.read_text(prompt_path))
    binding = core.prompt_binding_problems(meta, project.data, plan, node)
    if binding:
        raise DevFlowError(
            "提示词与当前计划不一致，不能执行（旧提示词会按过期需求开发）：\n  - "
            + "\n  - ".join(binding)
            + f"\n重新生成：df.py prompt-init {node['node_id']} --force"
        )

    prev_status = node["status"]
    transition(node, "in_progress", owner=args.owner or "code-agent", started_at=core.now_iso())
    _new_attempt(node)
    node["git_baseline"] = core.git_snapshot(paths.root)
    node["git_actual_changes"] = []
    node["git_discrepancies"] = []
    archived = []
    if prev_status in ("ready", "needs_review", "completed", "failed", "blocked"):
        archived = archive_reports(paths, node)
    project.data["status"] = "in_progress"
    project.save()
    refresh(project, plan)

    prompt_path = paths.root / node["prompt_file"]
    print(f"已开始 {node['node_id']}（第 {node['attempts']} 次尝试）")
    print(f"执行标识 attempt_id={node['current_attempt_id']}（两份报告都必须写这个值）")
    if archived:
        print(f"旧报告已归档：{', '.join(archived)}")
    print(f"提示词：{paths.rel(prompt_path)}")
    print("")
    print("执行边界（写进提示词里，Agent 也必须遵守）：")
    print(f"- 只改：{', '.join(node['files_scope']) or '未限定，但不得动无关模块'}")
    print("- 不改 .devflow/plans/*.json、不改其他节点目录")
    print("- 完成后写 execution-report.md（含 devflow-execution 证据块）+ test-report.json")
    print("- 测试没真跑就别写 passed")
    return 0


def _import_external_reports(paths: core.Paths, node: dict, args) -> None:
    """把外部 Agent 的报告导入节点目录。

    只做"搬文件 + 身份校验"，不降低任何验收门槛：
    导入后仍然走完整的证据校验、依赖校验和范围校验。
    """
    if not getattr(args, "source", None):
        raise DevFlowError("--external 需要同时指定 --from <目录>")
    src = Path(args.source).resolve()
    if not src.is_dir():
        raise DevFlowError(f"外部报告目录不存在：{src}")

    exec_src = src / "execution-report.md"
    test_src = src / "test-report.json"
    if not exec_src.exists():
        raise DevFlowError(f"外部报告缺少 execution-report.md：{exec_src}")

    evidence = core.parse_execution_evidence(core.read_text(exec_src))
    if evidence is None:
        raise DevFlowError("外部执行报告里没有 ```devflow-execution 证据块")
    if evidence.get("node_id") != node["node_id"]:
        raise DevFlowError(
            f"外部报告 node_id={evidence.get('node_id')!r} 与 {node['node_id']} 不一致"
        )
    aid = node.get("current_attempt_id")
    if aid and evidence.get("attempt_id") != aid:
        raise DevFlowError(
            f"外部报告的 attempt_id={evidence.get('attempt_id')!r} 与当前执行 {aid} 不一致"
        )

    if not node.get("no_test"):
        if not test_src.exists():
            raise DevFlowError(f"外部报告缺少 test-report.json：{test_src}")
        report = core.load_json(test_src)
        if report.get("node_id") != node["node_id"]:
            raise DevFlowError(
                f"外部测试报告 node_id={report.get('node_id')!r} 与 {node['node_id']} 不一致"
            )
        if aid and report.get("attempt_id") != aid:
            raise DevFlowError(
                f"外部测试报告的 attempt_id={report.get('attempt_id')!r} 与当前执行 {aid} 不一致"
            )

    shutil.copy2(exec_src, core.report_path(paths, node, "execution_report"))
    if test_src.exists():
        shutil.copy2(test_src, core.report_path(paths, node, "test_report"))
    print(f"外部报告已导入：{paths.rel(core.report_path(paths, node, 'execution_report'))}")


def cmd_sync(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    config = load_config()
    gates = config.get("quality-gates", {})
    accept_gates = gates.get("gates", {}).get("node_accept", {})
    empty_policy = gates.get("gates", {}).get("empty_scope", "warn")

    if node["status"] in ("completed", "superseded", "cancelled"):
        raise DevFlowError(f"{node['node_id']} 状态是 {node['status']}，不需要同步")
    if node["status"] != "in_progress":
        raise DevFlowError(
            f"{node['node_id']} 状态是 {node['status']}，只有 in_progress 能同步。\n"
            "外部 Agent 执行也必须先 `df.py start`（它只认这一条入口），"
            "同步时用 --result 说明结果。"
        )
    if not node.get("current_attempt_id"):
        # 升级前就开始执行的节点：补一个执行标识，旧报告将无法通过绑定校验
        _new_attempt(node)

    if getattr(args, "external", False):
        _import_external_reports(paths, node, args)

    exec_path = core.report_path(paths, node, "execution_report")
    test_path = core.report_path(paths, node, "test_report")
    missing = [paths.rel(p) for p in (exec_path, test_path) if not p.exists()]
    if missing and not node.get("no_test"):
        raise DevFlowError(
            "缺少证据文件：\n  " + "\n  ".join(missing) +
            "\n执行 Agent 必须实际跑测试并留下证据，否则不能同步。"
        )

    evidence = core.load_execution_evidence(paths, node) if exec_path.exists() else None
    report = core.load_test_report(paths, node)

    if args.result == "blocked":
        reason = args.reason or "执行 Agent 报告阻塞"
        transition(node, "blocked", blocked_reason=reason)
        refresh(project, plan)
        print(f"{node['node_id']} 已标记 blocked：{reason}")
        return 0

    test_status = (report or {}).get("status")
    if args.result == "failed" or test_status in ("failed", "partial", "not_run"):
        reason = args.reason or f"测试状态 {test_status or 'unknown'}"
        transition(node, "failed", blocked_reason=reason)
        refresh(project, plan)
        print(f"{node['node_id']} 同步为 failed：{reason}")
        print("修复后运行 retry，再重新执行。")
        return 1

    problems = core.evidence_problems(paths, node, gates, plan)
    if problems:
        print("✗ 证据不通过，置为 failed（不能进入验收）：")
        for item in problems:
            print(f"  - {item}")
        transition(node, "failed", blocked_reason="；".join(problems))
        refresh(project, plan)
        return 1

    modified = (evidence or {}).get("modified_files") or []
    violations = core.scope_violations(node, modified, paths.root, empty_policy)
    node["scope_violations"] = violations

    git_gate = gates.get("gates", {}).get("git", {}).get("verify_changes", "warn")
    discrepancies: list[str] = []
    if git_gate != "off":
        actual = core.git_changes_since(paths.root, node.get("git_baseline"))
        if actual is not None:
            node["git_actual_changes"] = actual
            reported = set()
            for item in modified:
                try:
                    reported.add(core.normalize_rel(item))
                except ValueError:
                    continue
            for path in sorted(set(actual) - reported):
                discrepancies.append(f"实际改动但报告未列：{path}")
            for path in sorted(reported - set(actual)):
                discrepancies.append(f"报告列出但未检测到改动：{path}")
            node["git_discrepancies"] = discrepancies

    transition(node, "needs_review")
    refresh(project, plan)
    print(f"{node['node_id']} 已同步，等待验收（needs_review）")
    if node.get("no_test"):
        print(f"测试：no_test（{node.get('no_test_reason')}）")
    else:
        summary = (report or {}).get("summary") or {}
        print(f"测试：{test_status}，{summary.get('passed', '?')}/{summary.get('total', '?')} 通过")
        print(f"命令：{len((report or {}).get('commands') or [])} 条，退出码均为 0")

    if node.get("git_baseline"):
        print(f"Git 基准：{node['git_baseline'].get('head', '(无提交)')}，"
              f"实际变更 {len(node.get('git_actual_changes') or [])} 个文件")
    if discrepancies:
        print("")
        print("! Git 实际变更与报告不一致：")
        for item in discrepancies[:10]:
            print(f"  - {item}")
        if git_gate == "error":
            print("验收已被阻止。确认无误后：accept --ack-changes --reason \"...\"")
            return 1
        print("（配置为 warn，验收前请人工确认）")

    if violations:
        print("")
        print("✗ 发现越界修改（files_scope 之外）：")
        for item in violations[:10]:
            print(f"  - {item}")
        if accept_gates.get("out_of_scope", "error") == "error":
            print("验收已被阻止。处理方式：")
            print("  1) 撤掉越界改动后重新同步；或")
            print("  2) 确认必要 → 更新 files_scope 并走变更；或")
            print("  3) `accept --allow-scope --reason \"...\"` 记录豁免（会留档）")
            return 1
        print("（质量门槛配置为 warning，仅提示）")

    print(f"运行 `python df.py accept {node['node_id']}` 完成验收")
    if args.accept:
        return cmd_accept(argparse.Namespace(
            project=str(paths.root), node_id=node["node_id"], by="auto",
            allow_scope=False, reason=None,
        ))
    return 0


def cmd_accept(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    gates = load_config().get("quality-gates", {})

    if node["status"] == "completed":
        print(f"{node['node_id']} 已经是 completed")
        return 0
    if node["status"] != "needs_review":
        raise DevFlowError(
            f"{node['node_id']} 状态是 {node['status']}，只有 needs_review 能验收。先运行 sync。"
        )

    violations = node.get("scope_violations") or []
    if violations:
        allow = getattr(args, "allow_scope", False)
        reason = getattr(args, "reason", None)
        if not (allow and reason):
            print("✗ 存在越界修改，不能验收：")
            for item in violations[:10]:
                print(f"  - {item}")
            print("确认必要请用：accept --allow-scope --reason \"为什么必须改这些文件\"")
            return 1
        node.setdefault("scope_waivers", []).append({
            "reason": reason,
            "at": core.now_iso(),
            "by": args.by or "user",
        })
        node["scope_violations"] = []
        print(f"! 已记录范围豁免：{reason}")

    git_gate = gates.get("gates", {}).get("git", {}).get("verify_changes", "warn")
    discrepancies = node.get("git_discrepancies") or []
    if discrepancies and git_gate == "error":
        allow = getattr(args, "ack_changes", False)
        reason = getattr(args, "reason", None)
        if not (allow and reason):
            print("✗ Git 实际变更与报告不一致，不能验收：")
            for item in discrepancies[:10]:
                print(f"  - {item}")
            print("确认无误请用：accept --ack-changes --reason \"为什么差异可以接受\"")
            return 1
        node.setdefault("git_waivers", []).append({
            "reason": reason,
            "at": core.now_iso(),
            "by": args.by or "user",
        })
        print(f"! 已确认 Git 差异：{reason}")

    problems = core.evidence_problems(paths, node, gates, plan)
    if problems:
        print("✗ 验收不通过：")
        for item in problems:
            print(f"  - {item}")
        return 1

    transition(node, "completed", completed_at=core.now_iso(), accepted_by=args.by or "user")
    unlocked = refresh(project, plan, quiet=True)
    print(f"✓ {node['node_id']} {node['node_name']} 验收通过")
    if unlocked:
        print(f"新解锁：{', '.join(unlocked)}")
    st = core.stats(plan)
    print(f"进度：{st['percent']:.1f}%（{st['completed']}/{st['effective']}）")
    if st["completed"] == st["effective"]:
        print("")
        print("所有有效节点已完成。下一步：regression-check → 全量回归 → release")
    return 0


def cmd_block(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    transition(node, "blocked", blocked_reason=args.reason)
    refresh(project, plan)
    print(f"{node['node_id']} 已阻塞：{args.reason}")
    return 0


def cmd_fail(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    transition(node, "failed", blocked_reason=args.reason)
    refresh(project, plan)
    print(f"{node['node_id']} 已标记失败：{args.reason}")
    return 0


def cmd_retry(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    if node["status"] not in ("failed", "blocked"):
        raise DevFlowError(f"{node['node_id']} 状态是 {node['status']}，只有 failed/blocked 需要 retry")
    max_rounds = load_config().get("defaults", {}).get("workflow", {}).get("max_auto_fix_rounds", 2)
    attempts = int(node.get("attempts", 0))
    if attempts >= max_rounds + 1 and not args.force:
        raise DevFlowError(
            f"{node['node_id']} 已尝试 {attempts} 次，超过自动修复上限 {max_rounds}。"
            "先分析真实原因或请用户决策，确需继续请加 --force"
        )
    transition(node, "in_progress", blocked_reason=None, started_at=core.now_iso())
    _new_attempt(node)
    refresh(project, plan)
    print(f"{node['node_id']} 重新开始（第 {node['attempts']} 次，attempt_id={node['current_attempt_id']}）")
    print("上一轮报告已不可用于本轮验收，必须重新产出。")
    return 0


def cmd_cancel(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    transition(node, "cancelled", remark=args.reason)
    unlocked = refresh(project, plan)
    print(f"{node['node_id']} 已取消：{args.reason}")
    if unlocked:
        print(f"新解锁：{', '.join(unlocked)}")
    return 0


def cmd_supersede(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    transition(node, "superseded", superseded_by=args.by)
    refresh(project, plan)
    print(f"{node['node_id']} 已废弃，由 {args.by} 取代")
    return 0


# --------------------------------------------------------------------------
# 变更
# --------------------------------------------------------------------------


def cmd_change_new(args) -> int:
    _, paths, project = ctx(args)
    paths.changes.mkdir(parents=True, exist_ok=True)
    numbers = [
        int(p.name[3:])
        for p in paths.changes.iterdir()
        if p.is_dir() and re.fullmatch(r"CR-\d{3,}", p.name)
    ]
    change_id = f"CR-{(max(numbers) + 1) if numbers else 1:03d}"
    cr_dir = paths.change_dir(change_id)
    cr_dir.mkdir(parents=True, exist_ok=True)

    base_plan = project.data.get("current_plan_version") or 0
    base_req = project.data.get("current_requirement_version") or 1
    cr = {
        "change_id": change_id,
        "title": args.title,
        "type": args.type,
        "status": "draft",
        "requested_by": args.by or "user",
        "requested_at": core.now_iso(),
        "base_requirement_version": base_req,
        "base_plan_version": base_plan,
        "target_requirement_version": None,
        "target_plan_version": None,
        "description": "",
        "reason": "",
        "old_behavior": "",
        "new_behavior": "",
        "requirement_ids": [],
        "impacts": [],
        "risk": {"level": "medium", "items": []},
        "recommended_plan": "",
        "confirmed_at": None,
        "confirmed_by": None,
    }
    core.atomic_write_json(cr_dir / "change-request.json", cr)

    core.atomic_write_text(cr_dir / "change-request.md", f"""# {change_id} · {args.title}

- 类型：{args.type}
- 状态：draft
- 提出人：{cr['requested_by']}
- 提出时间：{cr['requested_at']}
- 基线：需求 v{base_req} / 计划 v{base_plan}

## 变更内容

（写清楚：改之前是什么行为，改之后是什么行为）

## 变更原因

## 涉及需求

## 影响范围

见 `impact-assessment.md`。
""")

    core.atomic_write_text(cr_dir / "impact-assessment.md", f"""# {change_id} 影响评估

## 结论

（reusable / modify / add / deprecate / review 的分布与总体判断）

## 逐节点影响

| 节点 | 动作 | 依据 | 需重新测试 |
| --- | --- | --- | --- |
|  |  |  |  |

## 数据与接口兼容性

- 数据库：
- API：
- 历史数据：

## 风险

## 推荐实施顺序

## 需要用户确认的点
""")

    print(f"变更记录已创建：{paths.rel(cr_dir)}")
    print("下一步：填写 change-request.json 的 description/requirement_ids/impacts，")
    print("并把影响评估写进 impact-assessment.md。不要直接改当前生效计划。")
    return 0


def cmd_change_set(args) -> int:
    _, paths, _ = ctx(args)
    cr_dir = paths.change_dir(args.change)
    path = cr_dir / "change-request.json"
    cr = core.load_json(path)
    patch = core.load_json(Path(args.patch))
    merged = core.deep_merge(cr, patch)
    merged["change_id"] = args.change
    sv = validator.SchemaValidator(SKILL_DIR / "schemas")
    errors = sv.validate(merged, "change.schema.json")
    if errors:
        raise DevFlowError("变更记录校验失败：\n  " + "\n  ".join(errors))
    core.atomic_write_json(path, merged)
    print(f"已更新 {paths.rel(path)}（{len(merged.get('impacts', []))} 条影响）")
    return 0


def cmd_change_apply(args) -> int:
    _, paths, project = ctx(args)
    cr_dir = paths.change_dir(args.change)
    cr_path = cr_dir / "change-request.json"
    cr = core.load_json(cr_path)

    if cr.get("status") == "applied":
        raise DevFlowError(f"{args.change} 已经是 applied，不能重复应用")
    if cr.get("status") != "confirmed":
        raise DevFlowError(f"{args.change} 状态是 {cr.get('status')}，必须先由用户确认（status=confirmed）")

    target = cr.get("target_plan_version")
    if not target:
        raise DevFlowError("change-request.json 没有 target_plan_version，无法启用新版计划")

    # ---- 基准版本冲突检测：过期就拒绝，要求重新做影响分析 ----
    # 注意用 `is not None` 判断：base=0 表示"当时还没有计划"，也是有效基准，
    # 不能因为 0 是 falsy 就跳过检查（否则等于给过期基准开后门）。
    current_plan_version = project.data.get("current_plan_version")
    current_req_version = project.data.get("current_requirement_version")
    base_plan = cr.get("base_plan_version")
    base_req = cr.get("base_requirement_version")
    if base_plan is not None and current_plan_version is not None and \
            str(base_plan) != str(current_plan_version):
        raise DevFlowError(
            f"{args.change} 基于计划 v{base_plan}，当前生效计划已是 v{current_plan_version}。\n"
            "基准已过期：必须基于新计划重新评估影响，不能直接套用旧结论。"
        )
    if base_req is not None and current_req_version is not None and \
            str(base_req) != str(current_req_version):
        raise DevFlowError(
            f"{args.change} 基于需求 v{base_req}，当前需求已是 v{current_req_version}。\n"
            "基准已过期：重新评估后再应用。"
        )

    plan = core.load_json(paths.backlog(target))
    if plan.get("status") != "draft":
        raise DevFlowError(f"计划 v{target} 状态是 {plan.get('status')}，只有 draft 能激活")
    if plan.get("based_on") and plan["based_on"].get("change_id") not in (None, args.change):
        raise DevFlowError(
            f"计划 v{target} 是 {plan['based_on'].get('change_id')} 生成的，不是 {args.change}"
        )
    if cr.get("target_requirement_version") is not None and \
            str(plan.get("requirement_version")) != str(cr["target_requirement_version"]):
        raise DevFlowError(
            f"计划 v{target} 引用需求 v{plan.get('requirement_version')}，"
            f"与变更记录的目标需求 v{cr['target_requirement_version']} 不一致"
        )
    rv = plan.get("requirement_version")
    if rv and not paths.confirmed_requirements(rv).exists():
        raise DevFlowError(f"缺少需求基准 {paths.rel(paths.confirmed_requirements(rv))}")

    old_plan_path = paths.backlog(current_plan_version) if current_plan_version else None
    old_plan = None
    if old_plan_path and old_plan_path != paths.backlog(target):
        old_plan = core.load_json(old_plan_path)
        migration = core.migration_problems(old_plan, plan, args.change)
        if migration:
            print("✗ 节点迁移关系不合理，不能应用：")
            for item in migration:
                print(f"  - {item}")
            return 1

    report = validator.validate_all(paths.root, SKILL_DIR, target, activating=True)
    if report.errors:
        print("✗ 新版计划有错误，不能启用：")
        for item in report.errors:
            print(f"  - {item}")
        return 1

    # ---- 事务提交：快照 → 日志 → 逐个写 → 标记完成 ----
    files = [paths.project, paths.backlog(target), cr_path]
    if old_plan_path and old_plan_path != paths.backlog(target):
        files.append(old_plan_path)
    board_path = paths.board(target)
    if board_path.exists():
        files.append(board_path)

    txn = core.Transaction(
        paths, "change-apply", files,
        {"change_id": args.change, "from_plan": current_plan_version, "to_plan": target},
    )
    txn.begin()
    try:
        txn.phase("write_new_plan")
        plan["status"] = "active"
        plan["approved_at"] = plan.get("approved_at") or core.now_iso()
        plan["approved_by"] = plan.get("approved_by") or "user"
        core.atomic_write_json(paths.backlog(target), plan)

        txn.phase("supersede_old_plan")
        if old_plan is not None:
            old_plan["status"] = "superseded"
            core.atomic_write_json(old_plan_path, old_plan)

        txn.phase("update_project")
        project.data["current_plan_version"] = target
        if cr.get("target_requirement_version"):
            project.data["current_requirement_version"] = cr["target_requirement_version"]
        project.data["status"] = "in_progress" if any(
            n["status"] in ("completed", "in_progress") for n in plan["nodes"]
        ) else "planned"
        project.save()

        txn.phase("update_change")
        cr["status"] = "applied"
        cr["applied_at"] = core.now_iso()
        core.atomic_write_json(cr_path, cr)

        txn.phase("render_board")
        refresh(project, plan)
        txn.commit()
    except Exception as exc:  # noqa: BLE001 —— 任何异常都要回滚
        restored = txn.rollback(str(exc))
        print(f"✗ 变更提交失败，已回滚 {len(restored)} 个文件：{exc}")
        raise DevFlowError(f"{args.change} 未应用，项目状态保持原样。事务日志：{paths.rel(txn.journal_path)}")

    core.prune_transactions(paths)
    print(f"{args.change} 已应用：计划 v{current_plan_version or '-'} → v{target}")
    print(f"事务 {txn.txn_id} 已提交（快照保留在 {paths.rel(txn.dir)}）")
    print(f"当前可执行：{', '.join(n['node_id'] for n in core.ready_nodes(plan)) or '无'}")
    return 0


# --------------------------------------------------------------------------
# 回归 / 交付
# --------------------------------------------------------------------------


def cmd_regression_import(args) -> int:
    """导入机器可读回归报告，JSON 为准，Markdown 由它渲染。"""
    _, paths, project = ctx(args)
    plan = project.current_plan()
    src = Path(args.file).resolve()
    data = core.load_json(src)

    sv = validator.SchemaValidator(SKILL_DIR / "schemas")
    errors = sv.validate(data, "regression-report.schema.json")
    if errors:
        raise DevFlowError("回归报告结构校验失败：\n  " + "\n  ".join(errors))

    problems = _regression_data_problems(data, plan, require_pass=False)
    if problems:
        raise DevFlowError("回归报告不能导入：\n  " + "\n  ".join(problems))

    paths.regression.mkdir(parents=True, exist_ok=True)
    archived = _archive_regression(paths)
    core.atomic_write_json(paths.regression / REGRESSION_JSON, data)
    core.atomic_write_text(
        paths.regression / "final-report.md", render_regression_md(data)
    )

    print(f"回归报告已导入：{paths.rel(paths.regression / REGRESSION_JSON)}")
    print(f"Markdown 已同步渲染：{paths.rel(paths.regression / 'final-report.md')}")
    if archived:
        print(f"上一轮回归报告已归档：{archived}")
    print(f"整体结论：{data['status']}")
    if data["status"] != "passed":
        print("! 结论不是 passed：release 会被阻止，先修再重新导入。")
        return 1
    return 0


def _archive_regression(paths: core.Paths) -> str | None:
    """导入新报告前，把上一轮（含旧版纯 Markdown）挪进 history/。

    不能直接覆盖：历史回归结论是交付争议时的唯一凭据。
    """
    existing = [
        paths.regression / name
        for name in (REGRESSION_JSON, "final-report.md")
        if (paths.regression / name).exists()
    ]
    if not existing:
        return None
    hist = paths.regression / "history"
    hist.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[-:T+]", "", core.now_iso())[:14]
    first = None
    for path in existing:
        target = hist / f"{path.stem}.{stamp}{path.suffix}"
        shutil.move(str(path), str(target))
        first = first or paths.rel(target)
    return first


def _requirement_evidence_missing(
    paths: core.Paths,
    req: dict,
    completed_owners: list[dict],
) -> bool:
    """需求的验收标准是否完全没有对应的验证证据。

    "有节点覆盖"不等于"验收标准被验证过"。至少要有一个已完成节点的执行报告
    给出 met=true 且带证据的验收项，否则这条需求只是"代码写了"，没被验证。

    全部覆盖节点都是历史节点（没有 current_attempt_id）时不阻断，
    避免升级后旧项目的既有结论集体失效。
    """
    if not any(o.get("current_attempt_id") for o in completed_owners):
        return False
    for owner in completed_owners:
        if owner.get("no_test"):
            continue
        evidence = core.load_execution_evidence(paths, owner) or {}
        entries = evidence.get("acceptance")
        if not isinstance(entries, list):
            continue
        for item in entries:
            if isinstance(item, dict) and item.get("met") is True and \
                    str(item.get("evidence") or "").strip():
                return False
    return True


def cmd_regression_check(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    st = core.stats(plan)
    gates = load_config().get("quality-gates", {}).get("gates", {}).get("regression", {})

    blockers: list[str] = []
    open_nodes = [
        n for n in plan["nodes"]
        if n["status"] not in ("completed", "superseded", "cancelled")
    ]
    for node in open_nodes:
        blockers.append(f"{node['node_id']} 状态是 {node['status']}")

    if gates.get("forbid_missing_reports", True):
        for node in plan["nodes"]:
            if node["status"] != "completed":
                continue
            for problem in core.evidence_problems(paths, node, load_config().get("quality-gates", {}), plan):
                blockers.append(problem)

    traceability = core.load_traceability(paths) or {}
    by_req = {
        r.get("req_id"): r
        for r in traceability.get("requirements", [])
        if r.get("req_id")
    }
    uncovered = []
    for req in traceability.get("requirements", []):
        status = req.get("status")
        if status != "active":
            for problem in core.requirement_retirement_problems(req, by_req):
                blockers.append(problem)
            continue
        owners = [n for n in plan["nodes"] if req["req_id"] in n.get("requirement_ids", [])]
        if not owners:
            uncovered.append(f"{req['req_id']} 没有对应节点")
        elif not any(n["status"] == "completed" for n in owners):
            uncovered.append(f"{req['req_id']} 的节点都还没完成")
        else:
            completed_owners = [n for n in owners if n["status"] == "completed"]
            if all(n.get("no_test") for n in completed_owners) and \
                    req.get("category") not in ("constraint", "acceptance"):
                uncovered.append(f"{req['req_id']} 只有 no_test 节点覆盖，缺少可执行验证")
            elif req.get("acceptance") and \
                    _requirement_evidence_missing(paths, req, completed_owners):
                uncovered.append(
                    f"{req['req_id']} 的验收标准没有对应验证证据"
                    "（覆盖节点的报告里没有 met=true 且带证据的验收项）"
                )
    blockers.extend(f"需求覆盖不足：{item}" for item in uncovered)

    lines = [
        f"# 回归前置检查 · 计划 v{plan['plan_version']}",
        "",
        f"- 检查时间：{core.now_iso()}",
        f"- 有效节点：{st['effective']}，已完成：{st['completed']}，完成度：{st['percent']:.1f}%",
        "",
        "## 阻塞项",
        "",
    ]
    if blockers:
        lines += [f"- {item}" for item in blockers]
    else:
        lines.append("- 无")
    lines += ["", "## 需求覆盖", ""]
    if uncovered:
        lines += [f"- {item}" for item in uncovered]
    else:
        lines.append("- 所有 active 需求都有已完成节点覆盖")
    lines += [
        "",
        "## 回归执行清单",
        "",
        "| 项 | 命令/方式 | 结果 | 证据 |",
        "| --- | --- | --- | --- |",
        "| 编译/构建 |  |  |  |",
        "| 单元测试 |  |  |  |",
        "| 集成测试 |  |  |  |",
        "| 核心链路 |  |  |  |",
        "| 数据一致性 |  |  |  |",
        "| 权限 |  |  |  |",
        "| 数据库脚本 |  |  |  |",
        "",
        "> 结果只能填实际执行得到的值。没跑的写 not_run，不要写 passed。",
        "",
    ]
    paths.regression.mkdir(parents=True, exist_ok=True)
    out = paths.regression / "precheck.md"
    core.atomic_write_text(out, "\n".join(lines))

    print(f"== 回归前置检查 · 计划 v{plan['plan_version']} ==")
    if blockers:
        print(f"✗ 阻塞 {len(blockers)} 项：")
        for item in blockers:
            print(f"  - {item}")
    else:
        print("✓ 没有未完成节点，所有已完成节点证据齐全，需求全部覆盖")
    print(f"检查表：{paths.rel(out)}")
    if not blockers:
        project.data["status"] = "regression"
        project.save()
        print("项目状态已置为 regression。")
        print("执行真实构建与测试后，把结果写成 JSON 再导入：")
        print("  df.py regression-import --file <回归报告.json>")
    return 1 if blockers else 0


REGRESSION_CONCLUSION_RE = re.compile(r"结论[：:]\s*\**\s*(passed|failed|partial)", re.I)
REGRESSION_JSON = "final-report.json"


def derive_regression_status(checks: list[dict]) -> str:
    required = [c for c in checks if c.get("required", True)]
    if any(c.get("status") == "failed" for c in required):
        return "failed"
    if any(c.get("status") in ("not_run", "skipped") for c in required):
        return "not_run"
    if not required:
        return "not_run"
    return "passed"


def _legacy_conclusion(paths: core.Paths) -> tuple[str | None, str]:
    path = paths.regression / "final-report.md"
    if not path.exists():
        return None, f"缺少回归报告 {paths.rel(path)}"
    text = core.read_text(path)
    match = REGRESSION_CONCLUSION_RE.search(text)
    if not match:
        return None, "回归报告没有明确的结论行（需要形如 `结论：passed`）"
    return match.group(1).lower(), ""


def regression_problems(
    paths: core.Paths,
    plan: dict,
    allow_legacy: bool = False,
) -> list[str]:
    """校验机器可读回归报告。JSON 是事实来源，Markdown 只是展示。"""
    problems: list[str] = []
    path = paths.regression / REGRESSION_JSON
    if not path.exists():
        if allow_legacy:
            conclusion, reason = _legacy_conclusion(paths)
            if conclusion == "passed":
                return []
            return [reason or f"回归结论是 {conclusion}，不能进入交付"]
        return [
            f"缺少机器可读回归报告 {paths.rel(path)}。"
            "用 `df.py regression-import --file <报告.json>` 导入；"
            "旧项目可加 --legacy-regression 走 Markdown 降级（不推荐）"
        ]

    data = core.load_json(path)
    return _regression_data_problems(data, plan)


def _regression_data_problems(data: dict, plan: dict, require_pass: bool = True) -> list[str]:
    """回归报告语义校验（JSON 已在别处过 Schema）。

    require_pass=False 时只查"报告是否自洽"，用于导入失败报告留档。
    """
    problems: list[str] = []
    checks = data.get("checks") or []
    if data.get("plan_version") != plan.get("plan_version"):
        problems.append(
            f"回归报告的 plan_version={data.get('plan_version')}，当前计划是 v{plan.get('plan_version')}"
        )
    if data.get("requirement_version") != plan.get("requirement_version"):
        problems.append(
            f"回归报告的 requirement_version={data.get('requirement_version')}，"
            f"当前需求是 v{plan.get('requirement_version')}"
        )

    ids = [c.get("check_id") for c in checks]
    if len(ids) != len(set(ids)):
        problems.append("回归报告的 check_id 有重复")

    required_ids = (
        load_config()
        .get("quality-gates", {})
        .get("gates", {})
        .get("regression", {})
        .get("required_checks", [])
    )
    for rid in required_ids:
        if rid not in ids:
            problems.append(f"回归报告缺少必需检查项 {rid}")

    for check in checks:
        cid = check.get("check_id", "?")
        status = check.get("status")
        if status == "passed":
            if check.get("exit_code") != 0:
                problems.append(f"检查项 {cid} 标 passed 但 exit_code={check.get('exit_code')}")
            if not str(check.get("command") or "").strip():
                problems.append(f"检查项 {cid} 标 passed 但没有记录命令")
            if not str(check.get("evidence") or "").strip():
                problems.append(f"检查项 {cid} 标 passed 但没有证据")
        elif status == "failed":
            if require_pass:
                problems.append(f"检查项 {cid} 失败：{check.get('notes') or '未写原因'}")
        elif status in ("not_run", "skipped"):
            if not str(check.get("notes") or "").strip():
                problems.append(f"检查项 {cid} 是 {status}，但没有写原因")

    derived = derive_regression_status(checks)
    declared = data.get("status")
    if declared != derived:
        problems.append(f"回归报告整体状态 {declared!r} 与检查项推导结果 {derived!r} 不一致")
    if derived != "passed" and require_pass:
        problems.append(f"回归未通过（{derived}）")
    return problems


def render_regression_md(data: dict) -> str:
    lines = [
        "# 全量回归报告",
        "",
        f"- 计划版本：v{data.get('plan_version')}",
        f"- 需求版本：v{data.get('requirement_version')}",
        f"- 执行时间：{data.get('executed_at')}",
        f"- 执行者：{data.get('executed_by') or '-'}",
        f"- 整体结论：**{data.get('status')}**",
        "",
        "## 检查项",
        "",
        "| 检查 | 名称 | 必需 | 状态 | 命令 | 退出码 | 证据 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for check in data.get("checks", []):
        lines.append(
            "| {check_id} | {name} | {required} | {status} | {command} | {exit_code} | {evidence} | {notes} |".format(
                check_id=check.get("check_id", ""),
                name=check.get("name", ""),
                required="是" if check.get("required", True) else "否",
                status=check.get("status", ""),
                command=(check.get("command") or "-").replace("|", "\\|"),
                exit_code="-" if check.get("exit_code") is None else check.get("exit_code"),
                evidence=(check.get("evidence") or "-").replace("|", "\\|"),
                notes=(check.get("notes") or "").replace("|", "\\|"),
            )
        )
    lines += ["", "## 结论", "", f"结论：**{data.get('status')}**", ""]
    if data.get("notes"):
        lines += ["## 说明", "", data["notes"], ""]
    return "\n".join(lines)


def delivery_blockers(
    paths: core.Paths,
    project: core.Project,
    plan: dict,
    allow_legacy_regression: bool = False,
) -> list[str]:
    """交付条件检查。报告存在 ≠ 回归通过 ≠ 用户验收。"""
    gates = load_config().get("quality-gates", {})
    blockers: list[str] = []

    for node in plan["nodes"]:
        if node["status"] in ("superseded", "cancelled"):
            continue
        if node["status"] != "completed":
            blockers.append(f"{node['node_id']} 状态是 {node['status']}，尚未完成")
            continue
        for problem in core.evidence_problems(paths, node, gates, plan):
            blockers.append(problem)

    blockers.extend(regression_problems(paths, plan, allow_legacy_regression))

    for req in (core.load_traceability(paths) or {}).get("requirements", []):
        if req.get("status") != "active":
            continue
        owners = [n for n in plan["nodes"] if req["req_id"] in n.get("requirement_ids", [])]
        if not owners:
            blockers.append(f"需求 {req['req_id']} 没有对应节点")
        elif not any(n["status"] == "completed" for n in owners):
            blockers.append(f"需求 {req['req_id']} 的节点都未完成")
    return blockers


def cmd_release(args) -> int:
    """只生成交付报告并置为 release_candidate，不代表已交付。"""
    _, paths, project = ctx(args)
    plan = project.current_plan()
    if project.data.get("status") == "accepted":
        raise DevFlowError("项目已验收（accepted），不需要重新生成交付报告")

    blockers = delivery_blockers(paths, project, plan, getattr(args, "legacy_regression", False))
    if getattr(args, "legacy_regression", False):
        project.data["legacy_regression_accepted"] = True
        print("! 使用旧版 Markdown 回归报告（降级路径），已在项目档案中记录")

    traceability = core.load_traceability(paths) or {"requirements": []}
    nodes = plan["nodes"]
    verified, implemented_unverified, missing = [], [], []
    for node in nodes:
        if node["status"] in ("superseded", "cancelled"):
            continue
        if node["status"] != "completed":
            missing.append(node)
            continue
        report = core.load_test_report(paths, node)
        if report and report.get("status") == "passed" and not node.get("no_test"):
            verified.append(node)
        else:
            implemented_unverified.append(node)

    lines = [
        f"# 交付报告 · {project.data['project_name']}",
        "",
        f"- 计划版本：v{plan['plan_version']}（需求基准 v{plan.get('requirement_version')}）",
        f"- 生成时间：{core.now_iso()}",
        f"- 项目类型：{project.data['project_type']}",
        f"- 交付状态：**release_candidate**（报告已生成，未验收）",
        "",
        "## 0. 交付门槛检查",
        "",
    ]
    if blockers:
        lines.append(f"**未通过（{len(blockers)} 项）**：")
        lines.append("")
        lines += [f"- {item}" for item in blockers]
    else:
        lines.append("全部通过。可运行 `df.py release-ready` 进入待验收状态。")
    lines += [
        "",
        "## 1. 项目概述",
        "",
        project.data.get("notes") or "（待补充：项目目标与交付范围）",
        "",
        "## 2. 需求完成情况",
        "",
        "| 需求 | 内容 | 覆盖节点 | 状态 |",
        "| --- | --- | --- | --- |",
    ]
    for req in traceability.get("requirements", []):
        owners = [n["node_id"] for n in nodes if req["req_id"] in n.get("requirement_ids", [])]
        done = any(n["node_id"] in owners and n["status"] == "completed" for n in nodes)
        status = "已验证" if done and owners else ("未完成" if not done else "未验证")
        lines.append(f"| {req['req_id']} | {req.get('title', '')} | {', '.join(owners) or '-'} | {status} |")
    if not traceability.get("requirements"):
        lines.append("| - | （无需求追踪表） | - | - |")

    lines += ["", "## 3. 任务完成情况", "", "| 节点 | 名称 | 状态 | 测试证据 |", "| --- | --- | --- | --- |"]
    for node in nodes:
        report = core.load_test_report(paths, node)
        evidence = (
            f"{report.get('status')}（{report.get('summary', {}).get('passed', '?')}/"
            f"{report.get('summary', {}).get('total', '?')}）"
            if report else ("no_test" if node.get("no_test") else "缺失")
        )
        lines.append(f"| {node['node_id']} | {node['node_name']} | {node['status']} | {evidence} |")

    lines += [
        "",
        "## 4. 验证状态",
        "",
        f"- ✅ 已实际验证通过（{len(verified)}）：" + (", ".join(n["node_id"] for n in verified) or "无"),
        f"- ⚠️ 已实现但未自动化验证（{len(implemented_unverified)}）："
        + (", ".join(n["node_id"] for n in implemented_unverified) or "无"),
        f"- ❌ 未实现（{len(missing)}）：" + (", ".join(n["node_id"] for n in missing) or "无"),
        "",
        "## 5. 构建与测试",
        "",
        f"- 安装：{project.data.get('commands', {}).get('install') or '（未配置）'}",
        f"- 构建：{project.data.get('commands', {}).get('build') or '（未配置）'}",
        f"- 测试：{project.data.get('commands', {}).get('test') or '（未配置）'}",
        "",
        "（把回归报告里的实际命令与退出码粘到这里）",
        "",
        "## 6. 部署说明",
        "",
        "（待补充：部署步骤、依赖环境、启动命令）",
        "",
        "## 7. 配置项清单",
        "",
        "（待补充：环境变量、端口、外部服务地址；不要写入真实密钥）",
        "",
        "## 8. 已知问题与限制",
        "",
        "（待补充：来自回归报告与节点执行报告的遗留问题）",
        "",
        "## 9. 风险",
        "",
        "（待补充）",
        "",
        "## 10. 最终验收",
        "",
        "- [ ] 用户已确认交付范围（确认后运行 `df.py release-accept --by 用户名`）",
        "- [ ] 回归报告已归档",
        "- [ ] 部署说明可执行",
        "",
    ]

    paths.releases.mkdir(parents=True, exist_ok=True)
    out = paths.releases / "release-report.md"
    if out.exists():
        hist = paths.releases / "history"
        hist.mkdir(exist_ok=True)
        stamp = core.now_iso().replace(":", "").replace("-", "")[:13]
        shutil.copy2(out, hist / f"release-report.{stamp}.md")

    core.atomic_write_text(out, "\n".join(lines))
    project.data["status"] = "release_candidate"
    project.data["release_candidate_at"] = core.now_iso()
    project.save()

    print(f"交付报告已生成：{paths.rel(out)}")
    print(f"- 已验证 {len(verified)} | 已实现未验证 {len(implemented_unverified)} | 未实现 {len(missing)}")
    print("- 状态：release_candidate（报告已生成，**未**交付）")
    if blockers:
        print("")
        print(f"✗ 交付门槛未通过（{len(blockers)} 项）：")
        for item in blockers:
            print(f"  - {item}")
        print("修完再运行 release-ready；现在不能进入待验收状态。")
        return 1
    print("下一步：`df.py release-ready` 检查交付条件，再交用户验收。")
    return 0


def cmd_release_ready(args) -> int:
    _, paths, project = ctx(args)
    status = project.effective_status()
    if status == "accepted":
        raise DevFlowError("项目已验收")
    if status not in ("regression", "release_candidate"):
        raise DevFlowError(
            f"当前状态是 {status}，只有 regression / release_candidate 才能进入待验收。先跑回归并生成交付报告。"
        )
    plan = project.current_plan()
    blockers = delivery_blockers(
        paths, project, plan, project.data.get("legacy_regression_accepted", False)
    )
    if blockers:
        print(f"✗ 交付条件未满足（{len(blockers)} 项）：")
        for item in blockers:
            print(f"  - {item}")
        return 1
    project.data["status"] = "release_ready"
    project.data["release_ready_at"] = core.now_iso()
    project.save()
    print("✓ 交付条件已满足，状态：release_ready")
    print("等待用户验收：`df.py release-accept --by 用户名`")
    return 0


def cmd_release_accept(args) -> int:
    _, paths, project = ctx(args)
    status = project.effective_status()
    if status == "accepted":
        print("项目已经是 accepted")
        return 0
    if status != "release_ready":
        raise DevFlowError(
            f"当前状态是 {status}，只有 release_ready 才能验收。先运行 release-ready 并确认交付条件。"
        )
    if not args.by:
        raise DevFlowError("必须用 --by 记录验收人（例如 --by \"张三\"）")
    plan = project.current_plan()
    blockers = delivery_blockers(
        paths, project, plan, project.data.get("legacy_regression_accepted", False)
    )
    if blockers:
        print(f"✗ 交付条件已变化，不能验收（{len(blockers)} 项）：")
        for item in blockers:
            print(f"  - {item}")
        return 1
    project.data["status"] = "accepted"
    project.data["accepted_at"] = core.now_iso()
    project.data["accepted_by"] = args.by
    project.save()
    print(f"✓ 项目已验收（accepted），验收人：{args.by}")
    print(f"验收时间：{project.data['accepted_at']}")
    return 0


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def cmd_recover(args) -> int:
    """处理中断的事务：列出并（可选）回滚到事务前状态。"""
    _, paths, _ = ctx(args)
    pending = core.pending_transactions(paths)
    if not pending:
        print("没有未完成的事务，状态一致。")
        return 0

    print(f"发现 {len(pending)} 个未完成事务：")
    for journal in pending:
        print(
            f"- {journal['txn_id']}  {journal.get('name')}  "
            f"phase={journal.get('phase')}  status={journal.get('status')}  "
            f"at={journal.get('created_at')}"
        )
    if not args.apply:
        print("")
        print("这些都是「写到一半」的提交。回滚是确定安全的（提交完成的标志是最后一步）。")
        print("运行 `df.py recover --apply` 恢复。")
        return 1

    failed = 0
    for journal in pending:
        try:
            txn = core.load_transaction(paths, journal["txn_id"])
            restored = txn.rollback("df.py recover --apply")
            print(f"✓ {journal['txn_id']} 已回滚，恢复 {len(restored)} 个文件")
        except DevFlowError as exc:
            failed += 1
            print(f"✗ {journal['txn_id']} 回滚失败：{exc}")
    print("")
    print("回滚后请运行 `df.py doctor` 复查。")
    return 1 if failed else 0


def cmd_migrate(args) -> int:
    """把旧版 project.json 升级到当前 schema（不删字段、不重置状态）。"""
    _, paths, project = ctx(args)
    changes = core.migrate_project_data(project.data)
    if changes:
        project.save()
        print("项目档案已升级：")
        for item in changes:
            print(f"  - {item}")
    else:
        print("项目档案已是最新，无需迁移。")
    print(f"当前状态：{project.effective_status()}")
    return 0


def cmd_doctor(args) -> int:
    _, paths, project = ctx(args)
    print(f"== DevFlow doctor · {paths.rel(paths.df)} ==")
    ok = True
    report = validator.validate_all(paths.root, SKILL_DIR, args.plan)
    validator.print_report(report, "结构校验")
    ok = ok and report.ok

    version = args.plan or project.data.get("current_plan_version")
    if version:
        code = _run_dependency_check(paths.root, version)
        ok = ok and code == 0
    else:
        print("（没有生效计划，跳过依赖检查）")

    # 未完成事务
    pending = core.pending_transactions(paths)
    if pending:
        ok = False
        print(f"✗ 有 {len(pending)} 个未完成事务（状态可能不一致）：")
        for journal in pending:
            print(f"  - {journal['txn_id']} phase={journal.get('phase')}")
        print("  运行 `df.py recover` 查看，`df.py recover --apply` 回滚。")
    else:
        print("✓ 没有未完成事务")

    # 旧状态别名
    if project.data.get("status") in core.PROJECT_STATUS_MIGRATIONS:
        print(
            f"! 项目状态 {project.data['status']} 是旧版别名，"
            f"运行 `df.py migrate` 升级为 {project.effective_status()}"
        )

    # 越界记录
    plan = project.current_plan(required=False)
    if plan:
        for node in plan["nodes"]:
            if node.get("scope_violations"):
                ok = False
                print(f"✗ {node['node_id']} 有未处理的越界修改：{node['scope_violations'][:3]}")

    print("")
    print("结论：" + ("通过" if ok else "有问题，先修再继续"))
    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="df.py", description="DevFlow 项目状态机")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--project", default=".", help="项目根目录")
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "初始化 .devflow")
    p.add_argument("--name", default=None)
    p.add_argument("--id", default=None)
    p.add_argument("--type", choices=["greenfield", "existing", "refactor"], default=None)
    p.add_argument("--force", action="store_true")

    p = add("project-set", cmd_project_set, "用 patch 文件合并更新 project.json")
    p.add_argument("--patch", required=True)

    p = add("req-import", cmd_req_import, "导入需求追踪表")
    p.add_argument("--file", required=True)
    p.add_argument("--version", type=int, default=None)

    p = add("req-confirm", cmd_req_confirm, "确认需求基准")
    p.add_argument("--version", type=int, default=None)

    p = add("plan-import", cmd_plan_import, "导入草稿计划")
    p.add_argument("--file", required=True)
    p.add_argument("--version", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--keep-broken", action="store_true")

    p = add("plan-activate", cmd_plan_activate, "激活计划（需用户确认）")
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--by", default=None)

    add("status", cmd_status, "查看项目进度")
    add("next", cmd_next, "列出可执行节点").add_argument("--json", action="store_true")
    p = add("task", cmd_task, "查看节点详情")
    p.add_argument("node_id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--prompt", action="store_true")

    p = add("prompt-init", cmd_prompt_init, "按节点数据生成提示词骨架")
    p.add_argument("node_id", nargs="?", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--plan", type=int, default=None)

    p = add("start", cmd_start, "开始执行节点")
    p.add_argument("node_id")
    p.add_argument("--owner", default=None)

    p = add("sync", cmd_sync, "同步执行报告")
    p.add_argument("node_id")
    p.add_argument("--result", choices=["success", "failed", "blocked"], default=None)
    p.add_argument("--reason", default=None)
    p.add_argument("--accept", action="store_true")
    p.add_argument("--external", action="store_true", help="导入外部 Agent 的报告")
    p.add_argument("--from", dest="source", default=None, help="外部报告目录（含两份报告）")

    p = add("accept", cmd_accept, "验收节点")
    p.add_argument("node_id")
    p.add_argument("--by", default=None)
    p.add_argument("--allow-scope", action="store_true", help="记录范围豁免并放行越界修改")
    p.add_argument("--ack-changes", action="store_true", help="确认 Git 差异可以接受")
    p.add_argument("--reason", default=None, help="豁免/确认理由（必填）")

    p = add("block", cmd_block, "标记阻塞")
    p.add_argument("node_id")
    p.add_argument("--reason", required=True)

    p = add("fail", cmd_fail, "标记失败")
    p.add_argument("node_id")
    p.add_argument("--reason", required=True)

    p = add("retry", cmd_retry, "重试失败/阻塞节点")
    p.add_argument("node_id")
    p.add_argument("--force", action="store_true")

    p = add("cancel", cmd_cancel, "取消节点")
    p.add_argument("node_id")
    p.add_argument("--reason", required=True)

    p = add("supersede", cmd_supersede, "废弃节点并指向替代节点")
    p.add_argument("node_id")
    p.add_argument("--by", required=True)

    add("board", lambda a: render_board.main(["--project", a.project]), "重新渲染看板")
    p = add("validate", cmd_validate, "校验状态")
    p.add_argument("--plan", type=int, default=None)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--json", action="store_true")
    add("graph", cmd_graph, "生成依赖图")

    p = add("change-new", cmd_change_new, "新建变更记录")
    p.add_argument("--title", required=True)
    p.add_argument("--type", required=True, choices=[
        "add", "modify", "remove", "rule_change", "data_change",
        "tech_change", "nfr_change", "external_change",
    ])
    p.add_argument("--by", default=None)

    p = add("change-set", cmd_change_set, "更新变更记录")
    p.add_argument("--change", required=True)
    p.add_argument("--patch", required=True)

    p = add("change-apply", cmd_change_apply, "启用变更产生的新版计划")
    p.add_argument("--change", required=True)

    add("regression-check", cmd_regression_check, "回归前置检查")
    p = add("regression-import", cmd_regression_import, "导入机器可读回归报告")
    p.add_argument("--file", required=True)
    p = add("release", cmd_release, "生成交付报告（release_candidate）")
    p.add_argument("--legacy-regression", action="store_true",
                   help="允许用旧版 Markdown 结论（降级路径）")
    add("release-ready", cmd_release_ready, "检查交付条件（release_ready）")
    p = add("release-accept", cmd_release_accept, "用户验收（accepted）")
    p.add_argument("--by", default=None, help="验收人（必填）")

    add("recover", cmd_recover, "处理中断的事务").add_argument(
        "--apply", action="store_true", help="执行回滚"
    )
    add("migrate", cmd_migrate, "升级旧版项目档案")

    p = add("doctor", cmd_doctor, "全量体检")
    p.add_argument("--plan", type=int, default=None)

    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd in MUTATING_COMMANDS:
            root = Path(args.project).resolve()
            if not (root / core.DF_DIRNAME / "project.json").exists():
                root = core.find_root(root)
            paths = core.Paths(root)
            with core.project_lock(paths.df):
                # 锁内才读状态：ctx() 里的 Project() 会重新加载 project.json
                data = core.load_json(paths.project)
                status = core.PROJECT_STATUS_MIGRATIONS.get(
                    data.get("status"), data.get("status")
                )
                if status == "accepted" and args.cmd in FROZEN_ON_ACCEPTED:
                    raise DevFlowError(
                        f"项目已验收（accepted），不能再执行 {args.cmd}。\n"
                        "需要继续开发请走变更流程：change-new → change-apply"
                        "（生成新计划并把状态置为 in_progress）。"
                    )
                return args.func(args)
        return args.func(args)
    except DevFlowError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
