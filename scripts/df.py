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
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core
import render_board
import validate as validator
from dfcore import DevFlowError

SKILL_DIR = Path(__file__).resolve().parent.parent

# 所有会写全局状态的命令。main() 在执行它们之前统一加锁，
# 命令内部再读 project.json / backlog 时拿到的就是锁后的最新状态。
#
# 新加命令默认放进这里：只有 status/next/task/validate/doctor/self-test 是只读的，
# tests/test_v14_gates.py 会检查"新增写命令有没有漏加锁"。
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
    "board",
    "graph",
    "change-new",
    "change-set",
    "change-apply",
    "regression-check",
    "regression-import",
    "release",
    "release-ready",
    "release-accept",
    "recover",
    "migrate",
}

# accepted 是终态：白名单之外的写命令一律拒绝。
# 只有 change-* 是出口：change-apply 会生成新计划并把状态带回 in_progress。
ACCEPTED_ALLOWED = {
    # 只读
    "status", "next", "task", "validate", "doctor", "self-test",
    # 派生文件（看板/依赖图），不改状态
    "board", "graph",
    # 变更准备：导入草稿计划、生成提示词，都不改变生命周期
    "plan-import", "prompt-init",
    # 变更流程：可以创建/编辑 CR；只有 change-apply 能退出 accepted
    "change-new", "change-set", "change-apply",
    # 崩溃恢复：只回滚事务，不改变生命周期
    "recover",
}

#: project-set 允许通过 patch 修改的字段（其余由状态机/流程维护）
PROJECT_SET_FIELDS = {
    "project_name", "project_type", "tech_stack", "commands",
    "constraints", "conventions", "analysis", "notes",
}

#: 任何命令都不允许直接改的字段（只能由专用流程写）
PROJECT_PROTECTED_FIELDS = {
    "schema_version",
    "status",
    "current_plan_version",
    "current_requirement_version",
    "created_at",
    "updated_at",
    "accepted_at",
    "accepted_by",
    "release_ready_at",
    "release_candidate_at",
    "legacy_regression_accepted",
    "legacy_release_shortcut",
    "quality_profile",   # 只能通过 --profile 受控变更
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


def accepted_denial(cmd: str) -> str | None:
    """accepted 终态的默认拒绝策略：白名单之外一律拒绝。

    返回 None 表示放行；返回字符串是拒绝理由。
    """
    if cmd in ACCEPTED_ALLOWED:
        return None
    return (
        f"项目已验收（accepted），不能再执行 {cmd}。\n"
        "accepted 是终态：只有 change-new / change-set 能编辑变更申请，"
        "change-apply 是唯一出口（生成新计划并把状态置回 in_progress）。"
    )


def load_config(root: Path | None = None) -> dict:
    return validator.load_config(SKILL_DIR, root)


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
            "quality_profile": args.profile or "development",
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
        if args.profile:
            project["quality_profile"] = args.profile
        elif not project.get("quality_profile"):
            print("! 这份项目档案没有 quality_profile（旧项目），先按 development 门禁处理；"
                  "要升级请显式设置：df.py project-set --profile production")

    if not paths.decisions.exists():
        core.atomic_write_text(paths.decisions, _decisions_seed(project))

    config = load_config(root)
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
    patch = core.load_json(Path(args.patch)) if args.patch else {}
    profile = getattr(args, "profile", None)
    if not patch and not profile:
        raise DevFlowError("没有要更新的内容：给 --patch 或 --profile")

    if not isinstance(patch, dict):
        raise DevFlowError("--patch 文件必须是一个 JSON 对象")

    protected = sorted(set(patch) & PROJECT_PROTECTED_FIELDS)
    if protected:
        raise DevFlowError(
            "这些字段由状态机维护，不能用 project-set 改："
            + "、".join(protected)
            + "\n需要改状态请走对应流程（change-apply / migrate / release-*）"
        )
    unknown = sorted(set(patch) - PROJECT_SET_FIELDS)
    if unknown:
        raise DevFlowError(
            "project-set 只允许修改："
            + "、".join(sorted(PROJECT_SET_FIELDS))
            + "\n不允许："
            + "、".join(unknown)
        )

    merged = core.deep_merge(project.data, patch) if patch else dict(project.data)

    if profile:
        old_profile, _ = core.project_profile(paths.root)
        if profile != old_profile:
            reason = getattr(args, "reason", None)
            by = getattr(args, "by", None)
            if not reason or not by:
                raise DevFlowError(
                    "切换质量档位是受控操作：必须提供 --reason 和 --by，"
                    "记录会写进 project.json 的 profile_changes"
                )
            if old_profile == "production" and profile == "development" \
                    and not getattr(args, "confirm_downgrade", False):
                raise DevFlowError(
                    "把 production 降级为 development 需要显式确认："
                    "加 --confirm-downgrade（并写清理由）"
                )
            merged.setdefault("profile_changes", []).append({
                "from": old_profile,
                "to": profile,
                "reason": reason,
                "by": by,
                "at": core.now_iso(),
            })
            print(f"! 质量档位：{old_profile} → {profile}（{by}：{reason}）")
        merged["quality_profile"] = profile

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

    # 先查验收标准：这比 Schema 的 oneOf 报错更容易看懂
    # （只查 active 的验收标准；非 active 的退役凭据由 validate / 回归检查）
    spec_problems: list[str] = []
    for req in (data.get("requirements") or []) if isinstance(data, dict) else []:
        if isinstance(req, dict):
            spec_problems.extend(core.acceptance_spec_problems(req))
    if spec_problems:
        raise DevFlowError(
            "需求追踪表不满足规则，未导入：\n  " + "\n  ".join(spec_problems)
        )

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

    return 0


def cmd_req_confirm(args) -> int:
    _, paths, project = ctx(args)
    status = project.effective_status()
    if status in ("in_progress", "regression", "release_candidate", "release_ready"):
        raise DevFlowError(
            f"项目已进入 {status} 阶段，需求基准不能原地重确认。"
            "需要改需求请走变更流程：change-new → change-apply"
        )
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

    # 不能只看 Markdown 在不在：追踪表本身也得合格
    trace = core.load_traceability(paths)
    if not trace:
        raise DevFlowError(
            f"缺少需求追踪表 {paths.rel(paths.traceability)}；先 req-import 再确认"
        )
    trace_problems = core.traceability_problems(trace)
    if trace_problems:
        raise DevFlowError(
            "需求追踪表不满足规则，不能确认：\n  " + "\n  ".join(trace_problems)
        )
    trace_version = trace.get("requirement_version")
    if trace_version is not None and str(trace_version) != str(version):
        raise DevFlowError(
            f"追踪表是 v{trace_version}，确认的是 v{version}；先导入对应版本"
        )

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

    # 上下文变更：依赖产物在本节点开工前被改过，先让主控重新评估影响
    changes = core.context_changes(paths, plan, node)
    if changes:
        ack = getattr(args, "ack_context", False)
        reason = getattr(args, "reason", None)
        if not (ack and reason):
            raise DevFlowError(
                "依赖产物在执行期间发生了变化，先评估影响再开工：\n  - "
                + "\n  - ".join(changes)
                + "\n如果确认不影响本节点，用："
                f"df.py start {node['node_id']} --ack-context --reason \"为什么不影响\""
            )
        node.setdefault("context_acks", []).append({
            "reason": reason,
            "at": core.now_iso(),
            "changes": changes,
        })
        print(f"! 已记录上下文变更确认：{reason}")

    prev_status = node["status"]
    transition(node, "in_progress", owner=args.owner or "code-agent", started_at=core.now_iso())
    _new_attempt(node)
    node["git_baseline"] = core.git_snapshot(paths.root)
    # 开工前已存在的交付物：空 files_scope 时用它判断"哪些是本轮新增文件"
    node["scope_baseline"] = [
        rel for rel in core.deliverable_paths(node) if (paths.root / rel).exists()
    ]
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


def evaluate_node_changes(
    paths: core.Paths,
    node: dict,
    gates: dict,
    modified: list,
) -> dict:
    """把"报告声明 + Git 实际改动"合起来算范围与差异。

    sync 和 accept 都必须走这里：accept 不能信任 sync 时存下的旧结论，
    否则 sync 之后新加的越界文件就能溜进验收。
    """
    git_gates = gates.get("gates", {}).get("git", {})
    git_gate = git_gates.get("verify_changes", "warn")
    git_required = bool(git_gates.get("required", False))
    empty_policy = gates.get("gates", {}).get("empty_scope", "outputs_only")

    actual: list[str] | None = None
    unavailable = False
    discrepancies: list[str] = []

    if git_gate != "off":
        actual = core.git_changes_since(paths.root, node.get("git_baseline"))
        if actual is None:
            unavailable = True
            if git_required:
                discrepancies.append(
                    "Git 变更检测不可用（不是 git 仓库 / 项目不是仓库根），"
                    "当前质量配置要求必须能核对实际变更"
                )
        else:
            reported = set()
            for item in modified or []:
                try:
                    reported.add(core.normalize_rel(item))
                except ValueError:
                    continue
            for path in sorted(set(actual) - reported):
                discrepancies.append(f"实际改动但报告未列：{path}")
            for path in sorted(reported - set(actual)):
                discrepancies.append(f"报告列出但未检测到改动：{path}")

    scope_input: list[str] = []
    for item in list(modified or []) + list(actual or []):
        try:
            norm = core.normalize_rel(item)
        except ValueError:
            continue
        if norm not in scope_input:
            scope_input.append(norm)

    violations = core.scope_violations(node, scope_input, paths.root, empty_policy)
    return {
        "actual": actual,
        "unavailable": unavailable,
        "discrepancies": discrepancies,
        "scope_input": scope_input,
        "violations": violations,
        "git_required": git_required,
        "git_gate": git_gate,
    }


def _violating_files(violations: list[str]) -> list[str]:
    """从 `path: 原因` 形式的越界信息里取出文件路径。"""
    files = []
    for item in violations:
        path = str(item).split(":", 1)[0].strip()
        if path and path not in files:
            files.append(path)
    return files


def cmd_sync(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    node = project.find_node(args.node_id, plan)
    config = load_config(paths.root)
    gates = config.get("quality-gates", {})
    accept_gates = gates.get("gates", {}).get("node_accept", {})
    empty_policy = gates.get("gates", {}).get("empty_scope", "outputs_only")

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
    evaluation = evaluate_node_changes(paths, node, gates, modified)
    actual = evaluation["actual"]
    discrepancies = evaluation["discrepancies"]
    violations = evaluation["violations"]
    git_gate = evaluation["git_gate"]

    if actual is not None:
        node["git_actual_changes"] = actual
        node.pop("git_unavailable", None)
    if evaluation["unavailable"]:
        node["git_unavailable"] = True
    node["git_discrepancies"] = discrepancies
    node["scope_violations"] = violations

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
        if git_gate == "error" or (evaluation["git_required"] and evaluation["unavailable"]):
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
    gates = load_config(paths.root).get("quality-gates", {})
    accept_gates = gates.get("gates", {}).get("node_accept", {})

    if node["status"] == "completed":
        print(f"{node['node_id']} 已经是 completed")
        return 0
    if node["status"] != "needs_review":
        raise DevFlowError(
            f"{node['node_id']} 状态是 {node['status']}，只有 needs_review 能验收。先运行 sync。"
        )

    # ---- 实时复核：不信任 sync 时存下的结论 ----
    evidence = core.load_execution_evidence(paths, node) or {}
    modified = evidence.get("modified_files") or []
    evaluation = evaluate_node_changes(paths, node, gates, modified)
    violations = evaluation["violations"]
    discrepancies = evaluation["discrepancies"]

    node["scope_violations"] = violations
    node["git_discrepancies"] = discrepancies
    if evaluation["actual"] is not None:
        node["git_actual_changes"] = evaluation["actual"]
        node.pop("git_unavailable", None)
    if evaluation["unavailable"]:
        node["git_unavailable"] = True
    # 先把实时结论落盘：即使验收被拒，也要留下"当时发现了什么"
    project.save_plan(plan)

    # Git 检测不可用且配置要求必须可用：--ack-changes 也不能放行
    if evaluation["unavailable"] and evaluation["git_required"]:
        print("✗ 当前质量配置要求核对 Git 实际变更，但检测不可用：")
        print("  - 请把项目放进 Git 仓库根目录，或改用文件级快照策略")
        plan_meta = plan.get("plan_version")
        print(f"（节点 {node['node_id']} · 计划 v{plan_meta}）")
        return 1

    if violations:
        allow = getattr(args, "allow_scope", False)
        reason = getattr(args, "reason", None)
        if not (allow and reason):
            print("✗ 存在越界修改，不能验收：")
            for item in violations[:10]:
                print(f"  - {item}")
            print("确认必要请用：accept --allow-scope --reason \"为什么必须改这些文件\"")
            return 1
        files = _violating_files(violations)
        node.setdefault("scope_waivers", []).append({
            "reason": reason,
            "at": core.now_iso(),
            "by": args.by or "user",
            "files": files,
            "detail": violations[:50],
            "plan_version": plan.get("plan_version"),
        })
        node["scope_violations"] = []
        print(f"! 已记录范围豁免：{reason}")
        print(f"  豁免文件：{', '.join(files[:10]) or '（未能解析）'}")

    if discrepancies and (evaluation["git_gate"] == "error" or evaluation["git_required"]):
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
            "detail": discrepancies[:50],
        })
        print(f"! 已确认 Git 差异：{reason}")

    problems = core.evidence_problems(paths, node, gates, plan)
    if problems:
        print("✗ 验收不通过：")
        for item in problems:
            print(f"  - {item}")
        return 1

    transition(node, "completed", completed_at=core.now_iso(), accepted_by=args.by or "user")
    # 记录交付物指纹：下游开工时用它对一下"依赖产物有没有被改"
    snapshot = core.deliverable_snapshot(paths, node)
    snapshot["plan_version"] = plan["plan_version"]
    node["deliverables"] = snapshot
    unlocked = refresh(project, plan, quiet=True)
    print(f"✓ {node['node_id']} {node['node_name']} 验收通过")
    print(f"实时核对：Git 实际变更 {len(evaluation['actual'] or [])} 个文件，"
          f"越界 {len(violations)} 项")
    if snapshot["files"]:
        print(f"交付物指纹已记录：{len(snapshot['files'])} 个文件")
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
    max_rounds = load_config(paths.root).get("defaults", {}).get("workflow", {}).get("max_auto_fix_rounds", 2)
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
    # 当前没有计划时按 0 处理，不能让 None 悄悄跳过基准检查
    cur_plan = 0 if current_plan_version in (None, "") else current_plan_version
    if base_plan is not None and str(base_plan) != str(cur_plan):
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

    # ---- 目标计划必须严格属于这个 CR ----
    based_on = plan.get("based_on")
    if not isinstance(based_on, dict) or not based_on.get("change_id"):
        raise DevFlowError(
            f"计划 v{target} 没有 based_on.change_id，无法证明它由 {args.change} 生成。\n"
            "变更计划必须在 based_on 里写 {change_id, plan_version}；不接受「来路不明」的新计划。"
        )
    if based_on.get("change_id") != args.change:
        raise DevFlowError(
            f"计划 v{target} 是 {based_on.get('change_id')} 生成的，不是 {args.change}"
        )
    if str(based_on.get("plan_version")) != str(cr.get("base_plan_version")):
        raise DevFlowError(
            f"计划 v{target} 的 based_on.plan_version={based_on.get('plan_version')!r}，"
            f"与 {args.change} 的 base_plan_version={cr.get('base_plan_version')!r} 不一致"
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

    problems = _regression_data_problems(data, plan, require_pass=False, root=paths.root)
    if problems:
        raise DevFlowError("回归报告不能导入：\n  " + "\n  ".join(problems))

    # 回归报告必须绑定"跑之前那份代码"：代码变了，报告就不作数
    gates = load_config(paths.root).get("quality-gates", {}).get("gates", {})
    if (gates.get("regression") or {}).get("require_source_snapshot", True):
        snap_path = paths.regression / SNAPSHOT_FILE
        if not snap_path.exists():
            raise DevFlowError(
                f"缺少回归前置快照 {paths.rel(snap_path)}；先运行 `df.py regression-check` 记录当前代码"
            )
        baseline = core.load_json(snap_path)
        current = core.source_snapshot(paths.root)
        if current["snapshot_id"] != baseline.get("snapshot_id"):
            raise DevFlowError(
                "代码在回归前置检查之后发生了变化"
                f"（前置快照 {str(baseline.get('snapshot_id'))[:12]}，当前 {current['snapshot_id'][:12]}）。\n"
                "这份回归结果不能代表当前代码：请重新执行全量回归，再导入新报告。"
            )
        data["source_snapshot"] = baseline

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


SNAPSHOT_FILE = "source-snapshot.json"


def snapshot_problems(paths: core.Paths, plan: dict, config: dict | None = None) -> list[str]:
    """回归报告绑定的源码快照是否仍与当前代码一致。"""
    config = config or load_config(paths.root)
    gates = config.get("quality-gates", {}).get("gates", {})
    required = bool((gates.get("regression") or {}).get("require_source_snapshot", True))
    if not required:
        return []
    report_path = paths.regression / REGRESSION_JSON
    if not report_path.exists():
        return []  # 缺报告由 regression_problems 报，这里不重复
    data = core.load_json(report_path)
    recorded = data.get("source_snapshot")
    if not isinstance(recorded, dict) or not recorded.get("snapshot_id"):
        return ["回归报告没有绑定源代码快照（source_snapshot）；重新运行 regression-check 后重新导入"]
    current = core.source_snapshot(paths.root)
    if current["snapshot_id"] != recorded["snapshot_id"]:
        return [
            "回归报告对应的代码已经变化"
            f"（报告快照 {str(recorded.get('snapshot_id'))[:12]}，当前 {current['snapshot_id'][:12]}）。"
            "旧结论已失效，必须重新执行全量回归并导入新报告"
        ]
    return []


def _requirement_evidence_missing(
    paths: core.Paths,
    req: dict,
    completed_owners: list[dict],
) -> bool:
    """需求里是否还有验收标准找不到逐条证据。

    旧项目（全部覆盖节点都没有 attempt_id）不做追溯，避免历史结论集体失效。
    """
    if not any(o.get("current_attempt_id") for o in completed_owners):
        return False
    items = core.acceptance_items(req)
    if not items:
        return False
    covered: set[str] = set()
    for owner in completed_owners:
        evidence = core.load_execution_evidence(paths, owner) or {}
        for entry in evidence.get("requirement_acceptance") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("req_id") != req.get("req_id"):
                continue
            if entry.get("met") is not True:
                continue
            if not str(entry.get("evidence") or "").strip():
                continue
            covered.add(core.normalize_criterion(entry.get("criterion")))
    return any(
        core.normalize_criterion(item["text"]) not in covered for item in items
    )


def cmd_regression_check(args) -> int:
    _, paths, project = ctx(args)
    plan = project.current_plan()
    st = core.stats(plan)
    gates = load_config(paths.root).get("quality-gates", {}).get("gates", {}).get("regression", {})

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
            for problem in core.evidence_problems(paths, node, load_config(paths.root).get("quality-gates", {}), plan):
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
            spec_problems = core.acceptance_spec_problems(req)
            if spec_problems:
                uncovered.extend(spec_problems)
            elif _requirement_evidence_missing(paths, req, completed_owners):
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

    snapshot = core.source_snapshot(paths.root)
    core.atomic_write_json(paths.regression / SNAPSHOT_FILE, snapshot)

    print(f"== 回归前置检查 · 计划 v{plan['plan_version']} ==")
    if blockers:
        print(f"✗ 阻塞 {len(blockers)} 项：")
        for item in blockers:
            print(f"  - {item}")
    else:
        print("✓ 没有未完成节点，所有已完成节点证据齐全，需求全部覆盖")
    print(f"检查表：{paths.rel(out)}")
    print(f"源代码快照：{snapshot['snapshot_id'][:12]}（{snapshot['type']}，{snapshot['file_count']} 个文件）")
    if not blockers:
        project.data["status"] = "regression"
        project.save()
        print("项目状态已置为 regression。")
        print("执行真实构建与测试后，把结果写成 JSON 再导入：")
        print("  df.py regression-import --file <回归报告.json>")
    return 1 if blockers else 0


REGRESSION_CONCLUSION_RE = re.compile(r"结论[：:]\s*\**\s*(passed|failed|partial)", re.I)
REGRESSION_JSON = "final-report.json"


def derive_regression_status(
    checks: list[dict],
    required_ids: list[str] | None = None,
) -> str:
    """由检查项推导整体状态。

    `required_ids` 来自质量配置——**必需集合不接受报告自己声明**。
    报告把 build 标成 required=false 也不影响它是必需项这个事实。
    """
    required_ids = list(required_ids or [])
    by_id: dict[str, dict] = {}
    for check in checks or []:
        if not isinstance(check, dict):
            continue
        cid = check.get("check_id")
        if cid and cid not in by_id:
            by_id[cid] = check

    if required_ids:
        pool = []
        for rid in required_ids:
            check = by_id.get(rid)
            if check is None:
                return "failed"  # 必需项缺失，绝不可能 passed
            pool.append(check)
    else:
        # 没有配置时退回"报告自述"，供单元测试与旧数据使用
        pool = [c for c in by_id.values() if c.get("required", True)]

    if any(c.get("status") == "failed" for c in pool):
        return "failed"
    if any(c.get("status") in ("not_run", "skipped") for c in pool):
        return "not_run"
    if not pool:
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
    return _regression_data_problems(data, plan, root=paths.root)


def _regression_data_problems(
    data: dict,
    plan: dict,
    require_pass: bool = True,
    root: Path | None = None,
) -> list[str]:
    """回归报告语义校验（JSON 已在别处过 Schema）。

    必需集合以质量配置为准：报告不能把配置里的必需项降级成 optional，
    也不能凭空发明一个"必需项"。
    require_pass=False 时只查"报告是否自洽"，用于导入失败报告留档。
    """
    problems: list[str] = []
    config = load_config(root)
    gates = config.get("quality-gates", {}).get("gates", {})
    regression_gates = gates.get("regression", {})
    required_ids = list(regression_gates.get("required_checks") or [])
    block_optional_failures = bool(regression_gates.get("block_optional_failures", False))
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

    ids = [c.get("check_id") for c in checks if isinstance(c, dict)]
    if len(ids) != len(set(ids)):
        problems.append("回归报告的 check_id 有重复")

    for rid in required_ids:
        item = next((c for c in checks if isinstance(c, dict) and c.get("check_id") == rid), None)
        if item is None:
            problems.append(f"回归报告缺少必需检查项 {rid}")
        elif item.get("required") is not True:
            problems.append(
                f"回归报告把必需检查项 {rid} 标成了 required={item.get('required')!r}；"
                "必需项由质量配置决定，报告不能降级"
            )

    for check in checks:
        if not isinstance(check, dict):
            problems.append("回归报告 checks 里存在非对象项")
            continue
        cid = check.get("check_id", "?")
        if check.get("required") is True and cid not in required_ids:
            problems.append(
                f"检查项 {cid} 自称必需，但它不在质量配置的 required_checks 里；"
                "请改 required=false 或把它加进配置"
            )
        status = check.get("status")
        is_required = cid in required_ids
        if status == "passed":
            if check.get("exit_code") != 0:
                problems.append(f"检查项 {cid} 标 passed 但 exit_code={check.get('exit_code')}")
            if not str(check.get("command") or "").strip():
                problems.append(f"检查项 {cid} 标 passed 但没有记录命令")
            if not str(check.get("evidence") or "").strip():
                problems.append(f"检查项 {cid} 标 passed 但没有证据")
        elif status == "failed":
            if is_required and require_pass:
                problems.append(f"必需检查项 {cid} 失败：{check.get('notes') or '未写原因'}")
            elif not is_required and require_pass and block_optional_failures:
                problems.append(f"可选检查项 {cid} 失败（配置要求可选项失败也阻断）")
        elif status in ("not_run", "skipped"):
            if not str(check.get("notes") or "").strip():
                problems.append(f"检查项 {cid} 是 {status}，但没有写原因")
            if require_pass and is_required:
                problems.append(f"必需检查项 {cid} 没有执行（{status}），整体不能 passed")

    derived = derive_regression_status(checks, required_ids)
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
    gates = load_config(paths.root).get("quality-gates", {})
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
    blockers.extend(snapshot_problems(paths, plan))

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
    notes = str(project.data.get("notes") or "").strip()
    overview = notes or (
        f"{project.data['project_name']}（{project.data['project_type']}）。"
        f"本报告汇总计划 v{plan['plan_version']}（需求基准 v{plan.get('requirement_version')}）"
        "的需求覆盖、任务完成情况与验证状态；交付范围以需求基准与任务清单为准。"
    )
    commands = project.data.get("commands") or {}
    deploy_lines = []
    if commands.get("run"):
        deploy_lines += [
            f"1. 安装依赖：`{commands.get('install') or '无需安装'}`",
            f"2. 构建：`{commands.get('build') or '无需构建'}`",
            f"3. 启动：`{commands.get('run')}`",
            "4. 启动后按需求基准逐条验收；无外部服务依赖。",
        ]
    else:
        deploy_lines.append(
            "不适用：项目未配置运行命令，交付物是代码库本身（没有独立服务端入口）。"
            f"复现方式见上一节的安装/构建/测试命令。"
        )

    config_lines = []
    stack = project.data.get("tech_stack") or {}
    if stack:
        config_lines += [f"- 技术栈：{key} = {value}" for key, value in stack.items()]
    else:
        config_lines.append("- 技术栈：Python 3.8+（仅标准库）")
    config_lines.append("- 外部服务：无（不依赖数据库、消息队列或第三方 API）")
    config_lines.append("- 环境变量与密钥：无；不要在本报告中写入任何真实凭据")

    leftovers = []
    for node in nodes:
        if node["status"] in ("superseded", "cancelled"):
            continue
        evidence = core.load_execution_evidence(paths, node) or {}
        for item in evidence.get("leftovers") or []:
            leftovers.append(f"{node['node_id']}：{item}")
    if leftovers:
        known_issues = ["已知遗留（来自节点执行报告）：", ""] + [f"- {item}" for item in leftovers]
    elif blockers:
        known_issues = ["本报告生成时仍有交付门槛未通过：", ""] + [f"- {item}" for item in blockers]
    else:
        known_issues = [
            f"无已知问题：{core.now_iso()} 生成报告时回归结论为 passed，"
            "且没有节点遗留问题。"
        ]

    lines += [
        "",
        "## 1. 项目概述",
        "",
        overview,
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
        lines.append("| - | 本项目没有需求追踪表（基础设施或工具类交付） | - | 不适用 |")

    lines += ["", "## 3. 任务完成情况", "", "| 节点 | 名称 | 状态 | 测试证据 |", "| --- | --- | --- | --- |"]
    for node in nodes:
        report = core.load_test_report(paths, node)
        evidence = (
            f"{report.get('status')}（{report.get('summary', {}).get('passed', '?')}/"
            f"{report.get('summary', {}).get('total', '?')}）"
            if report else ("no_test" if node.get("no_test") else "缺失")
        )
        lines.append(f"| {node['node_id']} | {node['node_name']} | {node['status']} | {evidence} |")

    regression_path = paths.regression / REGRESSION_JSON
    regression_summary = "（尚未导入机器可读回归报告）"
    if regression_path.exists():
        regression = core.load_json(regression_path)
        passed_checks = [c for c in regression.get("checks", []) if c.get("status") == "passed"]
        regression_summary = (
            f"- 回归结论：**{regression.get('status')}**"
            f"（{len(passed_checks)}/{len(regression.get('checks') or [])} 项通过，"
            f"执行于 {regression.get('executed_at')}）"
        )

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
        f"- 安装：{commands.get('install') or '（未配置，无需安装）'}",
        f"- 构建：{commands.get('build') or '（未配置，无需构建）'}",
        f"- 测试：{commands.get('test') or '（未配置）'}",
        regression_summary,
        "",
        "## 6. 部署说明",
        "",
    ]
    lines += deploy_lines
    lines += ["", "## 7. 配置项清单", ""]
    lines += config_lines
    lines += ["", "## 8. 已知问题与限制", ""]
    lines += known_issues
    lines += [
        "",
        "## 9. 风险",
        "",
        "- 本报告基于导入的回归报告与节点证据；外部环境差异（操作系统、依赖版本）未单独验证。",
        "",
        "## 10. 最终验收",
        "",
        "- [ ] 用户已确认交付范围（确认后运行 `df.py release-accept --by 用户名`）",
        f"- [ ] 回归报告 final-report.json（计划 v{plan['plan_version']}）已归档",
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
    meta = {
        "schema_version": "1.0",
        "plan_version": plan["plan_version"],
        "requirement_version": plan.get("requirement_version"),
        "generated_at": core.now_iso(),
        "generated_by": "df.py release",
        "status": "release_candidate",
        "blockers": blockers,
        "sections": list(md_sections("\n".join(lines)).keys()),
        "counts": {
            "verified": len(verified),
            "implemented_unverified": len(implemented_unverified),
            "missing": len(missing),
        },
    }
    core.atomic_write_json(paths.releases / RELEASE_META, meta)
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


RELEASE_MD = "release-report.md"
RELEASE_META = "release-report.json"
SECTION_RE = re.compile(r"^##\s+(?:(\d+)[.、]?\s*)?(.+?)\s*$")


def md_sections(text: str) -> dict[str, str]:
    """把 Markdown 按二级标题切开：标题 → 正文（不含标题行）。"""
    sections: dict[str, str] = {}
    current = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = match.group(2).strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _find_section(sections: dict[str, str], name: str) -> tuple[str | None, str]:
    for title, body in sections.items():
        if name in title:
            return title, body
    return None, ""


def release_report_problems(
    paths: core.Paths,
    project: core.Project,
    plan: dict,
    config: dict | None = None,
) -> list[str]:
    """交付报告是否完整、是否对应当前计划。

    只检查"能不能证明写完了"：文件在、版本对、章节有实质内容、没有占位词。
    """
    config = config or load_config(paths.root)
    gates = config.get("quality-gates", {}).get("gates", {})
    release_gates = gates.get("release", {})
    spec = config.get("quality-gates", {}).get("release_report") or {}
    placeholders = config.get("quality-gates", {}).get("placeholders") or []
    required_sections = spec.get("required_sections") or []
    na_pattern = spec.get("not_applicable_pattern") or "不适用"

    problems: list[str] = []
    md_path = paths.releases / RELEASE_MD
    meta_path = paths.releases / RELEASE_META

    if not md_path.exists():
        return [f"缺少交付报告 {paths.rel(md_path)}；先运行 `df.py release` 生成"]

    text = core.read_text(md_path)
    sections = md_sections(text)

    if meta_path.exists():
        meta = core.load_json(meta_path)
        if meta.get("plan_version") != plan.get("plan_version"):
            problems.append(
                f"交付报告对应计划 v{meta.get('plan_version')}，当前计划是 v{plan.get('plan_version')}"
            )
        if meta.get("requirement_version") != plan.get("requirement_version"):
            problems.append(
                f"交付报告对应需求 v{meta.get('requirement_version')}，"
                f"当前需求是 v{plan.get('requirement_version')}"
            )
    else:
        problems.append(
            f"缺少交付报告元信息 {paths.rel(meta_path)}（版本绑定）；重新运行 `df.py release`"
        )

    for name in required_sections:
        title, body = _find_section(sections, name)
        if title is None:
            problems.append(f"交付报告缺少「{name}」章节")
            continue
        if not body.strip():
            problems.append(f"交付报告「{title}」章节为空")
            continue
        hits = [p for p in placeholders if p and p in body]
        if hits:
            problems.append(f"交付报告「{title}」还有占位内容：{'、'.join(hits[:3])}")
            continue
        if na_pattern in body:
            reason = body.replace(na_pattern, "").strip(" ：:。.\n-")
            if len(reason) < 6:
                problems.append(f"交付报告「{title}」写了不适用，但没有说明原因")
    return problems


def delivery_gate_problems(
    paths: core.Paths,
    project: core.Project,
    plan: dict,
    allow_legacy_regression: bool = False,
) -> tuple[list[str], list[str]]:
    """返回 (交付门槛问题, 交付报告问题)，供 release-ready / release-accept 共用。"""
    config = load_config(paths.root)
    release_gates = config.get("quality-gates", {}).get("gates", {}).get("release", {})
    legacy_shortcut = bool(project.data.get("legacy_release_shortcut"))

    blockers = delivery_blockers(paths, project, plan, allow_legacy_regression)
    report_problems: list[str] = []
    if release_gates.get("require_release_report", True) and not legacy_shortcut:
        report_problems = release_report_problems(paths, project, plan, config)
    return blockers, report_problems


def cmd_release_ready(args) -> int:
    _, paths, project = ctx(args)
    status = project.effective_status()
    if status == "accepted":
        raise DevFlowError("项目已验收")
    config = load_config(paths.root)
    release_gates = config.get("quality-gates", {}).get("gates", {}).get("release", {})
    legacy_shortcut = bool(project.data.get("legacy_release_shortcut"))
    allowed = {"release_candidate"}
    if legacy_shortcut or not release_gates.get("require_release_candidate", True):
        allowed.add("regression")
    if status not in allowed:
        raise DevFlowError(
            f"当前状态是 {status}，只有 release_candidate 才能进入待验收。"
            "先生成交付报告：`df.py release`（旧项目可用 `df.py migrate --legacy-release` 开兼容）"
        )
    plan = project.current_plan()
    blockers, report_problems = delivery_gate_problems(
        paths, project, plan, project.data.get("legacy_regression_accepted", False)
    )
    if blockers or report_problems:
        print(f"✗ 交付条件未满足（{len(blockers) + len(report_problems)} 项）：")
        for item in blockers:
            print(f"  - {item}")
        for item in report_problems:
            print(f"  - {item}")
        return 1
    if legacy_shortcut:
        print("! 这是旧项目的兼容路径（legacy_release_shortcut），交付报告按降级方式处理")
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
    # 不能因为之前 release-ready 通过就跳过复查：期间代码/证据可能已经变了
    blockers, report_problems = delivery_gate_problems(
        paths, project, plan, project.data.get("legacy_regression_accepted", False)
    )
    if blockers or report_problems:
        print(f"✗ 交付条件已变化，不能验收（{len(blockers) + len(report_problems)} 项）：")
        for item in blockers:
            print(f"  - {item}")
        for item in report_problems:
            print(f"  - {item}")
        print("重新跑回归并生成交付报告后再验收。")
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


# --------------------------------------------------------------------------
# self-test：在临时目录里把整条流水线跑一遍
# --------------------------------------------------------------------------

SELF_TEST_REQUIREMENTS = """# 需求确认说明书 · self-test

> 这份文档由 `df.py self-test` 生成，只用于验证流水线能否从空目录一路走到验收。
> 内容必须足够完整，才能通过 req-confirm 的"像不像一份需求基准"检查。

## 一、项目目标

验证 DevFlow 在临时目录中能完成：需求导入 → 需求确认 → 计划导入 → 依赖检查 →
计划激活 → 节点执行 → 证据同步 → 验收 → 变更 → 新版计划 → 回归 → 交付 → 用户验收。
整个过程不读写任何真实项目文件，结束后临时目录自动清理。

## 二、功能需求

- REQ-001 生成一份可读的输出文件 a.txt，内容非空且稳定可重复。

## 三、业务规则

- 输出内容只由节点实现决定，不依赖系统时间、随机数或外部网络。
- 同一节点重复执行应得到相同结果，便于回归比对。

## 四、非功能需求

- 不引入第三方依赖，只使用 Python 标准库。
- 所有文件写入使用原子写，异常时不留下半截文件。

## 五、验收标准

- [ ] `a.txt` 存在且内容非空
- [ ] 相关命令退出码为 0
- [ ] 全量回归报告的必需检查项全部 passed

## 六、明确不做

- 不实现真实业务逻辑，本文件仅服务于流水线自检。
- 不做性能压测、不做多进程并发验证（那由单元测试覆盖）。
"""

SELF_TEST_CHECKS = (
    "build", "unit_test", "integration", "core_flow", "consistency", "requirement_acceptance",
)


class _SelfTest:
    """一个自包含的端到端验收：从空目录初始化到 accepted。"""

    def __init__(self, keep: bool = False):
        self.keep = keep
        if keep:
            # mkdtemp 不会在解释器退出时自动删除，方便排查
            self._tmp = None
            base = Path(tempfile.mkdtemp(prefix="devflow-self-test-"))
        else:
            self._tmp = tempfile.TemporaryDirectory()
            base = Path(self._tmp.name)
        self.root = base / "demo"
        self.root.mkdir(parents=True, exist_ok=True)
        self.steps: list[dict] = []

    def close(self):
        if self._tmp is not None:
            self._tmp.cleanup()

    # -- 基础设施 ---------------------------------------------------------
    def run(self, step: str, argv: list[str], allow: tuple[int, ...] = (0,),
            root: Path | None = None) -> bool:
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            try:
                code = main([*argv, "--project", str(root or self.root)])
            except DevFlowError as exc:  # 提前失败也要记进步骤
                code = 2
                buf_err.write(str(exc))
        detail = (buf_out.getvalue() + buf_err.getvalue()).strip()
        ok = code in allow
        self.steps.append({
            "step": step,
            "status": "passed" if ok else "failed",
            "exit_code": code,
            "detail": detail.splitlines()[-1] if detail else "",
        })
        return ok

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def payload(self, rel: str, data: dict) -> str:
        self.write(rel, json.dumps(data, ensure_ascii=False, indent=2))
        return str(self.root / rel)

    # -- 数据 -------------------------------------------------------------
    def plan_v1(self):
        return {
            "requirement_version": 1,
            "nodes": [
                {
                    "node_id": "DF-001", "node_name": "生成核心产物", "phase": "Foundation",
                    "type": "business", "priority": "P0", "description": "写一个 a.txt",
                    "requirement_ids": ["REQ-001"], "dependencies": [], "parallelizable": False,
                    "parallel_group": None, "acceptance_criteria": ["a.txt 存在且内容非空"],
                    "inputs": [], "outputs": ["a.txt"], "files_scope": ["a.txt"],
                    "test_strategy": ["读取 a.txt 断言非空"], "status": "pending",
                    "prompt_file": ".devflow/nodes/DF-001/prompt.md",
                },
                {
                    "node_id": "DF-002", "node_name": "在核心产物上追加", "phase": "Foundation",
                    "type": "business", "priority": "P1", "description": "给 a.txt 追加一行",
                    "requirement_ids": [], "dependencies": ["DF-001"], "parallelizable": False,
                    "parallel_group": None, "acceptance_criteria": ["a.txt 有两行"],
                    "inputs": ["a.txt"], "outputs": ["a.txt"], "files_scope": ["a.txt"],
                    "test_strategy": ["统计行数"], "status": "pending",
                    "prompt_file": ".devflow/nodes/DF-002/prompt.md",
                },
            ],
        }

    def plan_v2(self):
        plan = self.plan_v1()
        plan["plan_version"] = 2
        plan["based_on"] = {"change_id": "CR-001", "plan_version": 1}
        # 已完成的节点保持 completed：变更只新增，不返工
        for item in plan["nodes"]:
            item["status"] = "completed"
            item["attempts"] = 1
        plan["nodes"].append({
            "node_id": "DF-003", "node_name": "补充说明文档", "phase": "Foundation",
            "type": "release", "priority": "P1", "description": "写 b.md",
            "requirement_ids": [], "dependencies": ["DF-002"], "parallelizable": False,
            "parallel_group": None, "acceptance_criteria": ["b.md 存在"],
            "inputs": [], "outputs": ["b.md"], "files_scope": ["b.md"],
            "test_strategy": ["文件存在即可"], "status": "pending",
            "prompt_file": ".devflow/nodes/DF-003/prompt.md",
            "created_in_plan": 2,
        })
        return plan

    def regression_payload(self, plan_version: int) -> dict:
        now = core.now_iso()
        return {
            "schema_version": "1.0",
            "plan_version": plan_version,
            "requirement_version": 1,
            "status": "passed",
            "executed_at": now,
            "executed_by": "self-test",
            "checks": [
                {
                    "check_id": cid, "name": cid, "required": True, "status": "passed",
                    "command": "self-test simulated", "exit_code": 0, "executed_at": now,
                    "evidence": "self-test 模拟执行", "notes": "",
                }
                for cid in SELF_TEST_CHECKS
            ],
            "notes": "由 df.py self-test 生成，仅用于验证流水线本身。",
        }

    def node_evidence(self, node_id: str, name: str, criteria: list[str], plan_version: int,
                      modified: list[str], root: Path | None = None,
                      result: str = "success", test_status: str = "passed",
                      test_failed: int = 0) -> None:
        root = Path(root or self.root)
        plan = core.load_json(core.Paths(root).backlog(plan_version))
        item = next(n for n in plan["nodes"] if n["node_id"] == node_id)
        attempt = item.get("current_attempt_id")
        trace = core.load_traceability(core.Paths(root)) or {}
        by_req = {r.get("req_id"): r for r in trace.get("requirements", [])}
        requirement_acceptance = []
        for req_id in item.get("requirement_ids") or []:
            for entry in core.acceptance_items(by_req.get(req_id) or {}):
                requirement_acceptance.append({
                    "req_id": req_id,
                    "criterion": entry["text"],
                    "met": True,
                    "evidence": f"self-test: {name}",
                })
        evidence = {
            "node_id": node_id,
            "attempt_id": attempt,
            "plan_version": plan_version,
            "requirement_version": plan["requirement_version"],
            "result": result,
            "modified_files": modified,
            "acceptance": [
                {"criterion": c, "met": True, "evidence": f"self-test: {name}"} for c in criteria
            ],
            "requirement_acceptance": requirement_acceptance,
            "assumptions": [],
            "leftovers": [],
        }
        failed = int(test_failed)
        passed = 1 - failed if failed in (0, 1) else 0
        report = {
            "node_id": node_id,
            "attempt_id": attempt,
            "plan_version": plan_version,
            "requirement_version": plan["requirement_version"],
            "status": test_status,
            "executed_at": core.now_iso(),
            "commands": [{"cmd": "self-test simulated", "exit_code": 0 if not failed else 1}],
            "summary": {"total": 1, "passed": passed, "failed": failed, "skipped": 0},
            "cases": [{"name": name, "result": "pass" if not failed else "fail"}],
        }
        node_dir = Path(root) / ".devflow" / "nodes" / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "execution-report.md").write_text(
            "# 执行报告\n\n```devflow-execution\n"
            + json.dumps(evidence, ensure_ascii=False, indent=2)
            + "\n```\n",
            encoding="utf-8",
        )
        (node_dir / "test-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def complete(self, node_id: str, name: str, criteria: list[str], plan_version: int,
                 modified: list[str], produce: dict[str, str] | None = None) -> bool:
        if not self.run("start", ["start", node_id]):
            return False
        for rel, text in (produce or {}).items():
            self.write(rel, text)
        self.node_evidence(node_id, name, criteria, plan_version, modified)
        if not self.run("sync", ["sync", node_id]):
            return False
        return self.run("accept", ["accept", node_id])

    # -- 子场景 -----------------------------------------------------------
    def _write_root(self, root: Path, rel: str, text: str) -> None:
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _node(self, node_id: str, scope: str, group: str | None = None) -> dict:
        return {
            "node_id": node_id, "node_name": f"节点 {node_id}", "phase": "Foundation",
            "type": "business", "priority": "P1", "description": f"写 {scope}",
            "requirement_ids": [], "dependencies": [], "parallelizable": bool(group),
            "parallel_group": group, "acceptance_criteria": [f"{scope} 存在"],
            "inputs": [], "outputs": [scope], "files_scope": [scope],
            "test_strategy": ["文件存在"], "status": "pending",
            "prompt_file": f".devflow/nodes/{node_id}/prompt.md",
        }

    def _basic_project(self, name: str, nodes: list[dict], tag: str) -> Path | None:
        root = self.root.parent / name
        root.mkdir(parents=True, exist_ok=True)
        if not self.run(f"{tag}:init", ["init", "--name", name, "--id", name], root=root):
            return None
        self._write_root(root, ".devflow/requirements/confirmed-requirements-v1.md",
                         SELF_TEST_REQUIREMENTS)
        trace = root / "trace.json"
        trace.write_text(json.dumps({
            "schema_version": "1.0", "requirement_version": 1,
            "requirements": [{
                "req_id": "REQ-001", "title": "占位", "category": "functional",
                "status": "active", "acceptance": ["命令退出码为 0"],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        ok = (
            self.run(f"{tag}:req-import", ["req-import", "--file", str(trace), "--version", "1"], root=root)
            and self.run(f"{tag}:req-confirm", ["req-confirm", "--version", "1"], root=root)
        )
        trace.unlink(missing_ok=True)
        if not ok:
            return None
        plan = root / "plan.json"
        plan.write_text(json.dumps({"requirement_version": 1, "nodes": nodes}, ensure_ascii=False),
                        encoding="utf-8")
        ok = (
            self.run(f"{tag}:plan-import", ["plan-import", "--file", str(plan), "--version", "1"], root=root)
            and self.run(f"{tag}:prompt-init", ["prompt-init", "--all"], root=root)
            and self.run(f"{tag}:activate", ["plan-activate", "--version", "1", "--by", "self-test"], root=root)
        )
        return root if ok else None

    def parallel_scenario(self) -> bool:
        """两个无依赖节点同组并行：两个进程同时 start，不能丢状态。"""
        nodes = [
            self._node("DF-101", "p1/a.py", group="A"),
            self._node("DF-102", "p2/b.py", group="A"),
        ]
        root = self._basic_project("parallel", nodes, "parallel")
        if root is None:
            return False

        procs = [
            subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "start", n["node_id"],
                 "--project", str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            for n in nodes
        ]
        codes = []
        for proc in procs:
            out, _ = proc.communicate(timeout=120)
            codes.append((proc.returncode, out.decode("utf-8", "replace")))
        started = all(code == 0 for code, _ in codes)
        self.steps.append({
            "step": "parallel:start",
            "status": "passed" if started else "failed",
            "exit_code": 0 if started else 1,
            "detail": "两个进程同时 start" if started else codes[0][1][:120],
        })
        if not started:
            return False

        ok = True
        for n in nodes:
            nid = n["node_id"]
            scope = n["files_scope"][0]
            self._write_root(root, scope, "x\n")
            self.node_evidence(nid, f"写 {scope}", n["acceptance_criteria"], 1, [scope], root=root)
            ok = self.run(f"parallel:sync-{nid}", ["sync", nid], root=root) and ok
            ok = self.run(f"parallel:accept-{nid}", ["accept", nid], root=root) and ok
        if not ok:
            return False

        plan = core.load_json(core.Paths(root).backlog(1))
        statuses = {n["node_id"]: n["status"] for n in plan["nodes"]}
        attempts = {n.get("current_attempt_id") for n in plan["nodes"]}
        violations = [v for n in plan["nodes"] for v in (n.get("scope_violations") or [])]
        board = (core.Paths(root).board(1))
        board_text = board.read_text(encoding="utf-8") if board.exists() else ""
        checks = {
            "状态全部 completed": all(v == "completed" for v in statuses.values()),
            "attempt_id 互不相同": len(attempts) == len(nodes) and None not in attempts,
            "没有越界记录": not violations,
            "看板与 JSON 一致": ("100.0%" in board_text and "completed" in board_text
                                 and board_text.count("| DF-10") >= len(nodes)),
        }
        passed = all(checks.values())
        self.steps.append({
            "step": "parallel:verify",
            "status": "passed" if passed else "failed",
            "exit_code": 0 if passed else 1,
            "detail": "；".join(f"{k}={'ok' if v else 'fail'}" for k, v in checks.items()),
        })
        return passed

    def external_scenario(self) -> bool:
        """外部 Agent 报告导入：正常路径 + 五类错误路径。"""
        nodes = [
            self._node("DF-201", "ext/a.py"),
            self._node("DF-202", "ext/b.py"),
            self._node("DF-203", "ext/c.py"),
            self._node("DF-204", "ext/d.py"),
            self._node("DF-205", "ext/e.py"),
            self._node("DF-206", "ext/f.py"),
        ]
        NODE_SCOPE = {n["node_id"]: n["files_scope"][0] for n in nodes}
        root = self._basic_project("external", nodes, "external")
        if root is None:
            return False

        def external_dir(name: str, attempt: str, *, with_test=True, with_block=True,
                         modified=None, node_id="DF-201", test_status="passed",
                         test_failed=0, criterion=None) -> Path:
            base = root.parent / f"ext-{name}"
            base.mkdir(parents=True, exist_ok=True)
            evidence = {
                "node_id": node_id, "attempt_id": attempt, "plan_version": 1,
                "requirement_version": 1, "result": "success" if not test_failed else "failed",
                "modified_files": modified if modified is not None else [NODE_SCOPE[node_id]],
                "acceptance": [{
                    "criterion": criterion or f"{NODE_SCOPE[node_id]} 存在",
                    "met": True, "evidence": "t",
                }],
            }
            text = "# 执行报告\n"
            if with_block:
                text += "\n```devflow-execution\n" + json.dumps(evidence, ensure_ascii=False) + "\n```\n"
            (base / "execution-report.md").write_text(text, encoding="utf-8")
            if with_test:
                (base / "test-report.json").write_text(json.dumps({
                    "node_id": node_id, "attempt_id": attempt, "plan_version": 1,
                    "requirement_version": 1, "status": test_status,
                    "executed_at": core.now_iso(),
                    "commands": [{"cmd": "sim", "exit_code": 0 if not test_failed else 1}],
                    "summary": {"total": 1, "passed": 1 - test_failed, "failed": test_failed, "skipped": 0},
                    "cases": [{"name": "c", "result": "pass" if not test_failed else "fail"}],
                }, ensure_ascii=False), encoding="utf-8")
            return base

        def attempt_of(nid: str) -> str:
            plan = core.load_json(core.Paths(root).backlog(1))
            return next(n for n in plan["nodes"] if n["node_id"] == nid)["current_attempt_id"]

        ok = self.run("external:start", ["start", "DF-201"], root=root)
        if not ok:
            return False
        aid = attempt_of("DF-201")
        good = external_dir("good", aid)
        ok = self.run("external:sync", ["sync", "DF-201", "--external", "--from", str(good)], root=root)
        ok = ok and self.run("external:accept", ["accept", "DF-201"], root=root)

        # 错误路径 1：attempt_id 不对 → 拒绝；随后用正确的仍可导入
        self.run("external:start-bad", ["start", "DF-202"], root=root)
        wrong = external_dir("wrong", "deadbeef", node_id="DF-202")
        self.run("external:bad-attempt", ["sync", "DF-202", "--external", "--from", str(wrong)],
                 allow=(2,), root=root)
        right = external_dir("right", attempt_of("DF-202"), node_id="DF-202")
        ok = self.run("external:sync-after-retry",
                      ["sync", "DF-202", "--external", "--from", str(right)], root=root) and ok

        # 错误路径 2：缺 test-report
        self.run("external:start-no-test", ["start", "DF-203"], root=root)
        no_test = external_dir("no-test", attempt_of("DF-203"), node_id="DF-203", with_test=False)
        ok = self.run("external:reject-no-test",
                      ["sync", "DF-203", "--external", "--from", str(no_test)],
                      allow=(2,), root=root) and ok

        # 错误路径 3：缺证据块
        self.run("external:start-no-block", ["start", "DF-204"], root=root)
        no_block = external_dir("no-block", attempt_of("DF-204"), node_id="DF-204", with_block=False)
        ok = self.run("external:reject-no-block",
                      ["sync", "DF-204", "--external", "--from", str(no_block)],
                      allow=(2,), root=root) and ok

        # 错误路径 4：越界
        self.run("external:start-scope", ["start", "DF-205"], root=root)
        out_scope = external_dir("scope", attempt_of("DF-205"), node_id="DF-205",
                                 modified=["ext/e.py", "ext/hack.py"])
        ok = self.run("external:reject-scope",
                      ["sync", "DF-205", "--external", "--from", str(out_scope)],
                      allow=(1,), root=root) and ok

        # 错误路径 5：测试失败
        self.run("external:start-failed", ["start", "DF-206"], root=root)
        failed = external_dir("failed", attempt_of("DF-206"), node_id="DF-206",
                              test_status="failed", test_failed=1)
        ok = self.run("external:reject-failed-test",
                      ["sync", "DF-206", "--external", "--from", str(failed)],
                      allow=(1,), root=root) and ok
        return ok

    # -- 主流程 -----------------------------------------------------------
    def execute(self) -> bool:
        ok = self.run("init", ["init", "--name", "Self Test", "--id", "self-test",
                              "--type", "greenfield"])
        if not ok:
            return False

        req_doc = ".devflow/requirements/confirmed-requirements-v1.md"
        self.write(req_doc, SELF_TEST_REQUIREMENTS)
        trace = self.payload(".devflow/requirements/traceability-v1.json", {
            "schema_version": "1.0",
            "requirement_version": 1,
            "requirements": [{
                "req_id": "REQ-001", "title": "生成输出文件", "category": "functional",
                "priority": "must", "status": "active", "statement": "生成 a.txt",
                "source": "self-test", "acceptance": ["命令退出码为 0"],
            }],
        })
        ok = self.run("req-import", ["req-import", "--file", trace, "--version", "1"]) and \
            self.run("req-confirm", ["req-confirm", "--version", "1"])
        if not ok:
            return False

        v1 = self.payload("plan-v1.json", self.plan_v1())
        ok = self.run("plan-import", ["plan-import", "--file", v1, "--version", "1"]) and \
            self.run("validate", ["validate", "--plan", "1"]) and \
            self.run("prompt-init", ["prompt-init", "--all"]) and \
            self.run("activate", ["plan-activate", "--version", "1", "--by", "self-test"]) and \
            self.run("graph", ["graph"]) and \
            self.run("validate-active", ["validate"])
        if not ok:
            return False

        if not self.complete(
            "DF-001", "生成核心产物", ["a.txt 存在且内容非空"], 1, ["a.txt"],
            produce={"a.txt": "hello\n"},
        ):
            return False
        if not self.complete(
            "DF-002", "在核心产物上追加", ["a.txt 有两行"], 1, ["a.txt"],
            produce={"a.txt": "hello\nworld\n"},
        ):
            return False

        # 变更：新增 DF-003 → 新版计划 → 事务应用
        ok = self.run("change-new", ["change-new", "--title", "补文档", "--type", "add"])
        if not ok:
            return False
        cr_path = core.Paths(self.root).change_dir("CR-001") / "change-request.json"
        cr = core.load_json(cr_path)
        cr.update({
            "status": "confirmed",
            "base_plan_version": 1,
            "base_requirement_version": 1,
            "target_plan_version": 2,
            "target_requirement_version": 1,
            "impacts": [{"node_id": "DF-003", "action": "add", "reason": "补交付说明"}],
        })
        core.atomic_write_json(cr_path, cr)

        v2 = self.payload("plan-v2.json", self.plan_v2())
        ok = self.run("plan-import-v2",
                      ["plan-import", "--file", v2, "--version", "2"]) and \
            self.run("prompt-init-v2", ["prompt-init", "--all", "--plan", "2"]) and \
            self.run("change-apply", ["change-apply", "--change", "CR-001"])
        if not ok:
            return False

        if not self.complete(
            "DF-003", "补充说明文档", ["b.md 存在"], 2, ["b.md"], produce={"b.md": "# 说明\n"},
        ):
            return False

        ok = self.run("doctor", ["doctor"])
        if not ok:
            return False

        if not self.run("regression-check", ["regression-check"]):
            return False
        reg_path = self.root.parent / "regression.json"
        reg_path.write_text(
            json.dumps(self.regression_payload(2), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        reg = str(reg_path)
        ok = self.run("regression-import", ["regression-import", "--file", reg]) and \
            self.run("release", ["release"]) and \
            self.run("release-ready", ["release-ready"]) and \
            self.run("release-accept", ["release-accept", "--by", "self-test"])
        if not ok:
            return False

        project = core.load_json(core.Paths(self.root).project)
        self.final_status = core.PROJECT_STATUS_MIGRATIONS.get(
            project.get("status"), project.get("status")
        )
        accepted = self.final_status == "accepted"

        # 两条支线：并行执行、外部 Agent 报告导入
        parallel_ok = self.parallel_scenario()
        external_ok = self.external_scenario()
        return accepted and parallel_ok and external_ok

    def report(self) -> dict:
        failed = [s for s in self.steps if s["status"] != "passed"]
        return {
            "status": "passed" if (self.steps and not failed and
                                   getattr(self, "final_status", None) == "accepted") else "failed",
            "steps": self.steps,
            "final_status": getattr(self, "final_status", None),
            "temp_dir": str(self.root),
        }


def _run_layer(name: str, cwd: Path, pattern: str = "test_*.py") -> dict:
    """跑一层测试，解析 unittest 的 'Ran N tests' / 'OK|FAILED'。

    子进程带 `DEVFLOW_SELF_TEST_LAYERS=1`：万一测试里又调 self-test，
    它不会再跑分层，避免无限递归。
    """
    env = dict(os.environ, DEVFLOW_SELF_TEST_LAYERS="1")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern],
            cwd=str(cwd), capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace", timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"name": name, "status": "not_run", "detail": f"无法执行：{exc}"}
    text = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"Ran (\d+) tests?", text)
    count = int(match.group(1)) if match else 0
    status = "passed" if proc.returncode == 0 else "failed"
    detail = f"{count} 条测试，{'OK' if status == 'passed' else 'FAILED'}"
    return {"name": name, "status": status, "detail": detail, "tests": count}


def cmd_self_test(args) -> int:
    """在临时目录跑完整生命周期；可选再跑单元/集成两层。"""
    started = time.time()
    harness = _SelfTest(keep=getattr(args, "keep", False))
    try:
        ok = harness.execute()
        report = harness.report()
    finally:
        harness.close()

    layers = {}
    nested = os.environ.get("DEVFLOW_SELF_TEST_LAYERS") == "1"
    if not getattr(args, "fast", False) and not nested:
        layers["unit"] = _run_layer("单元测试（引擎）", SKILL_DIR)
        layers["integration"] = _run_layer(
            "集成测试（示例项目）", SKILL_DIR / "examples" / "sample-project"
        )
    elif nested:
        print("（检测到嵌套调用，跳过分层，避免递归）")
    report["layers"] = layers
    report["duration_ms"] = int((time.time() - started) * 1000)
    if layers and any(l["status"] != "passed" for l in layers.values()):
        report["status"] = "failed"
        ok = False

    if getattr(args, "out", None):
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        core.atomic_write_json(out_path, report)
    else:
        out_path = None

    if getattr(args, "json", False) or getattr(args, "out", None):
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        print("== DevFlow self-test ==")
        for step in report["steps"]:
            mark = "✓" if step["status"] == "passed" else "✗"
            print(f"{mark} {step['step']:<20} {step['detail']}")
        print("")
        if layers:
            print("分层结果：")
            for layer in layers.values():
                print(f"  {'✓' if layer['status'] == 'passed' else '✗'} {layer['name']}：{layer['detail']}")
        else:
            print("分层结果：跳过（--fast）")
        print(f"  {'✓' if report['status'] == 'passed' else '✗'} 端到端工作流："
              f"{len(report['steps'])} 步 → {report['final_status']}")
        print("")
        print(f"结论：{'通过' if report['status'] == 'passed' else '失败'}（{report['duration_ms']} ms）")
        if out_path:
            print(f"机器可读报告：{out_path}")
    return 0 if (ok and report["status"] == "passed") else 1


def cmd_migrate(args) -> int:
    """把旧版 project.json 升级到当前 schema（不删字段、不重置状态）。"""
    _, paths, project = ctx(args)
    changes = core.migrate_project_data(project.data)
    if not project.data.get("quality_profile"):
        project.data["quality_profile"] = getattr(args, "profile", None) or "development"
        changes.append(f"quality_profile: （未声明）→ {project.data['quality_profile']}")
    if getattr(args, "legacy_release", False):
        project.data["legacy_release_shortcut"] = True
        changes.append("legacy_release_shortcut: 已开启（允许 regression 直接 release_ready，仅供旧项目）")
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

    # 质量档位
    profile, declared = core.project_profile(paths.root)
    if not declared:
        print("! 项目没有声明 quality_profile（按 development 处理）；"
              "生产项目请运行 `df.py migrate --profile production`")
    else:
        print(f"✓ 质量档位：{profile}")
    if project.data.get("legacy_release_shortcut"):
        print("! 已开启 legacy_release_shortcut（旧项目兼容路径，允许跳过交付报告）")

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
    p.add_argument("--profile", choices=["development", "production"], default=None,
                   help="质量门槛档位（默认 development）")
    p.add_argument("--force", action="store_true")

    p = add("project-set", cmd_project_set, "更新 project.json（受限字段）或质量档位")
    p.add_argument("--patch", default=None, help="只含允许字段的 JSON 文件")
    p.add_argument("--profile", choices=["development", "production"], default=None,
                   help="受控切换质量档位（需 --reason 与 --by）")
    p.add_argument("--reason", default=None, help="切换档位的原因")
    p.add_argument("--by", default=None, help="谁做的这次变更")
    p.add_argument("--confirm-downgrade", action="store_true",
                   help="确认把 production 降级为 development")

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
    p.add_argument("--ack-context", action="store_true",
                   help="确认依赖产物变更不影响本节点（需同时给 --reason）")
    p.add_argument("--reason", default=None, help="上下文变更确认理由（必填）")

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
    p = add("migrate", cmd_migrate, "升级旧版项目档案")
    p.add_argument("--profile", choices=["development", "production"], default=None,
                   help="升级时同时指定质量档位")
    p.add_argument("--legacy-release", action="store_true",
                   help="旧项目兼容：允许跳过 release 直接 release_ready（会记录在项目档案）")

    p = add("doctor", cmd_doctor, "全量体检")
    p.add_argument("--plan", type=int, default=None)

    p = add("self-test", cmd_self_test, "在临时目录跑完整生命周期（不碰真实项目）")
    p.add_argument("--json", action="store_true", help="输出机器可读结果")
    p.add_argument("--fast", action="store_true", help="只跑端到端，不跑单元/集成两层")
    p.add_argument("--keep", action="store_true", help="保留临时目录用于排查")
    p.add_argument("--out", default=None, help="把结果写成 JSON 文件")

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
                if status == "accepted":
                    denial = accepted_denial(args.cmd)
                    if denial:
                        raise DevFlowError(denial)
                return args.func(args)
        return args.func(args)
    except DevFlowError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
