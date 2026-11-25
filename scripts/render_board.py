#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 backlog JSON 渲染 Markdown 看板。

看板永远由 JSON 生成，不手工维护——这是状态一致性的前提。

用法：
    python render_board.py --project .                 # 渲染当前生效计划
    python render_board.py --project . --plan 2 --out out.md
    python render_board.py --project . --no-time       # 输出可做快照对比
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core
from dfcore import DevFlowError

STATUS_LABEL = {
    "pending": "pending",
    "ready": "ready",
    "in_progress": "in_progress",
    "needs_review": "needs_review",
    "completed": "completed",
    "blocked": "blocked",
    "failed": "failed",
    "superseded": "superseded",
    "cancelled": "cancelled",
}

EXEC_MODE = {
    "pending": "等待依赖",
    "ready": "单独执行",
    "in_progress": "执行中",
    "needs_review": "待验收",
    "completed": "—",
    "blocked": "阻塞",
    "failed": "失败待修",
    "superseded": "已废弃",
    "cancelled": "已取消",
}


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [core.dwidth(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], core.dwidth(cell))
    lines = []
    lines.append("| " + " | ".join(core.pad(h, widths[i]) for i, h in enumerate(headers)) + " |")
    lines.append("| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |")
    for row in rows:
        lines.append("| " + " | ".join(core.pad(c, widths[i]) for i, c in enumerate(row)) + " |")
    return lines


def _bar(percent: float, width: int = 16) -> str:
    filled = int(round(percent / 100.0 * width))
    return "`" + "█" * filled + "░" * (width - filled) + f"` {percent:.1f}%"


def _exec_mode(node: dict) -> str:
    status = node.get("status", "pending")
    if status == "ready" and node.get("parallel_group"):
        return f"并行组 {node['parallel_group']}"
    return EXEC_MODE.get(status, status)


def render(plan: dict, project: dict | None = None, include_time: bool = True) -> str:
    project = project or {}
    st = core.stats(plan)
    counts = st["counts"]
    nodes = plan["nodes"]
    lines: list[str] = []

    lines.append(f"# 任务看板 · v{plan['plan_version']}")
    lines.append("")
    lines.append(f"- 项目：{project.get('project_name', plan.get('project_id', '-'))}（{plan.get('project_id', '-')}）")
    lines.append(f"- 需求基准：v{plan.get('requirement_version', '-')}")
    lines.append(f"- 计划状态：**{plan.get('status', '-')}**")
    if plan.get("based_on"):
        based = plan["based_on"]
        note = f"- 基于计划：v{based.get('plan_version')}"
        if based.get("change_id"):
            note += f"（变更 {based['change_id']}）"
        lines.append(note)
    if include_time:
        lines.append(f"- 生成时间：{core.now_iso()}")
    lines.append("- 数据源：`.devflow/plans/backlog-v%d.json`" % plan["plan_version"])
    lines.append("")

    if plan.get("status") == "draft":
        lines.append("> ⚠️ 这是**草稿计划**，尚未生效。确认后运行 `df.py plan-activate` 才会进入执行。")
        lines.append("")

    lines.append("## 进度")
    lines.append("")
    rows = [
        ["总任务", str(st["total"])],
        ["有效任务（排除废弃/取消）", str(st["effective"])],
        ["已完成", str(counts.get("completed", 0))],
        ["可执行", str(counts.get("ready", 0))],
        ["执行中", str(counts.get("in_progress", 0))],
        ["待验收", str(counts.get("needs_review", 0))],
        ["待执行（依赖未满足）", str(counts.get("pending", 0))],
        ["阻塞", str(counts.get("blocked", 0))],
        ["失败", str(counts.get("failed", 0))],
        ["已废弃", str(counts.get("superseded", 0))],
        ["已取消", str(counts.get("cancelled", 0))],
        ["完成度", f"{st['percent']:.1f}%"],
    ]
    lines.extend(_table(["指标", "数值"], rows))
    lines.append("")
    lines.append(_bar(st["percent"]))
    lines.append("")

    lines.append("## 任务清单")
    lines.append("")
    order = {p: i for i, p in enumerate(core.PHASES)}
    ordered = sorted(
        nodes,
        key=lambda n: (order.get(n.get("phase"), 99), n.get("node_id", "")),
    )
    rows = []
    for node in ordered:
        deps = node.get("dependencies") or []
        rows.append([
            node.get("node_id", "-"),
            _cell(node.get("node_name", "-")),
            node.get("phase", "-"),
            node.get("priority", "-"),
            ", ".join(deps) if deps else "无",
            STATUS_LABEL.get(node.get("status"), node.get("status", "-")),
            _exec_mode(node),
        ])
    lines.extend(_table(["任务编号", "任务名称", "阶段", "优先级", "依赖", "状态", "可执行方式"], rows))
    lines.append("")

    ready = core.ready_nodes(plan)
    lines.append("## 当前可执行")
    lines.append("")
    if ready:
        solo = [n for n in ready if not n.get("parallel_group")]
        grouped: dict[str, list[dict]] = {}
        for node in ready:
            if node.get("parallel_group"):
                grouped.setdefault(node["parallel_group"], []).append(node)
        for node in solo:
            lines.append(f"- `{node['node_id']}` {node['node_name']}（单独执行，{node['priority']}）")
        for group, members in sorted(grouped.items()):
            names = "、".join(f"`{m['node_id']}` {m['node_name']}" for m in members)
            lines.append(f"- 并行组 **{group}**：{names}")
    else:
        lines.append("（暂无）")
    lines.append("")

    stuck = [n for n in nodes if n.get("status") in ("blocked", "failed")]
    lines.append("## 阻塞与失败")
    lines.append("")
    if stuck:
        for node in stuck:
            reason = node.get("blocked_reason") or node.get("remark") or "未填写原因"
            lines.append(f"- `{node['node_id']}` **{node.get('status')}**：{reason}")
    else:
        lines.append("（无）")
    lines.append("")

    groups = core.parallel_groups(plan)
    if groups:
        lines.append("## 并行组")
        lines.append("")
        rows = []
        for name, ids in groups.items():
            members = [n for n in nodes if n["node_id"] in ids]
            statuses = sorted({n.get("status", "-") for n in members})
            rows.append([name, "、".join(ids), "/".join(statuses)])
        lines.extend(_table(["组", "成员", "状态"], rows))
        lines.append("")

    lines.append("## 阶段统计")
    lines.append("")
    rows = []
    for phase in core.PHASES:
        members = [n for n in nodes if n.get("phase") == phase]
        if not members:
            continue
        done = len([n for n in members if n.get("status") == "completed"])
        rows.append([phase, str(len(members)), str(done)])
    lines.extend(_table(["阶段", "节点数", "已完成"], rows))
    lines.append("")

    if plan.get("nodes"):
        lines.append("## 状态图例")
        lines.append("")
        lines.append(
            "`pending` 待执行 · `ready` 依赖已满足 · `in_progress` 执行中 · "
            "`needs_review` 待人工验收 · `completed` 已验收 · `blocked` 阻塞 · "
            "`failed` 失败 · `superseded` 已被新版取代 · `cancelled` 已取消"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DevFlow 看板渲染")
    parser.add_argument("--project", default=".")
    parser.add_argument("--plan", type=int, default=None)
    parser.add_argument("--out", default=None, help="输出路径，默认写入 .devflow/plans/board-vN.md")
    parser.add_argument("--no-time", action="store_true", help="不写生成时间（便于快照对比）")
    parser.add_argument("--stdout", action="store_true", help="只打印，不写文件")
    args = parser.parse_args(argv)

    try:
        root = core.find_root(Path(args.project))
        paths = core.Paths(root)
        project = core.load_json(paths.project)
        version = args.plan or project.get("current_plan_version")
        if not version:
            raise DevFlowError("没有可渲染的计划：先 plan-import 生成计划，或指定 --plan N")
        plan = core.load_json(paths.backlog(version))
    except DevFlowError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    text = render(plan, project, include_time=not args.no_time)
    if args.stdout:
        sys.stdout.write(text)
        return 0

    out = Path(args.out) if args.out else paths.board(version)
    core.atomic_write_text(out, text)
    print(f"看板已生成：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
