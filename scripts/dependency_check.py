#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务依赖检查：环检测、缺失依赖、分层并行度、关键路径。

用法：
    python dependency_check.py --project .              # 检查并打印
    python dependency_check.py --project . --write      # 同时写 dependency-graph-vN.md
    python dependency_check.py --project . --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core
from dfcore import DevFlowError


def analyse(plan: dict) -> dict:
    nodes = plan["nodes"]
    by_id = {n["node_id"]: n for n in nodes}
    problems: list[str] = []
    warnings: list[str] = []

    missing = {}
    for node in nodes:
        lost = [d for d in node.get("dependencies", []) if d not in by_id]
        if lost:
            missing[node["node_id"]] = lost
            problems.append(f"{node['node_id']} 依赖了不存在的节点：{', '.join(lost)}")

    cycles = core.find_cycles(nodes)
    for cycle in cycles:
        problems.append("依赖循环：" + " → ".join(cycle))

    levels: dict[str, int] = {}
    if not cycles and not missing:
        # 最长路径分层：同层节点之间必然没有依赖，可以作为并行候选
        for nid in core.topological_order(nodes):
            node = by_id[nid]
            levels[nid] = 1 + max(
                [levels.get(d, 0) for d in node.get("dependencies", [])] or [0]
            )
    else:
        for nid in by_id:
            levels[nid] = 0

    layers: dict[int, list[str]] = {}
    for nid, level in levels.items():
        layers.setdefault(level, []).append(nid)
    for level in layers:
        layers[level].sort()

    # 关键路径（按节点数最长链）
    longest: dict[str, list[str]] = {}

    def path_to(nid: str) -> list[str]:
        if nid in longest:
            return longest[nid]
        deps = [d for d in by_id[nid].get("dependencies", []) if d in by_id]
        if not deps:
            longest[nid] = [nid]
        else:
            best = max((path_to(d) for d in deps), key=len)
            longest[nid] = best + [nid]
        return longest[nid]

    critical: list[str] = []
    if not cycles:
        for nid in by_id:
            candidate = path_to(nid)
            if len(candidate) > len(critical):
                critical = candidate

    # 永远不会 ready 的节点
    unreachable = []
    for node in nodes:
        if node.get("status") in ("completed", "superseded", "cancelled"):
            continue
        for dep in node.get("dependencies", []):
            target = by_id.get(dep)
            if target and target.get("status") in ("superseded", "cancelled"):
                unreachable.append(
                    f"{node['node_id']} 依赖 {dep}，而 {dep} 已 {target.get('status')}"
                )
                break

    if unreachable:
        problems.extend(unreachable)

    pending_with_reason = []
    for node in nodes:
        if node.get("status") != "pending":
            continue
        unmet = core.unmet_dependencies(node, plan)
        if unmet:
            pending_with_reason.append({"node_id": node["node_id"], "waiting_for": unmet})

    return {
        "plan_version": plan["plan_version"],
        "total": len(nodes),
        "problems": problems,
        "warnings": warnings,
        "cycles": cycles,
        "missing": missing,
        "levels": layers,
        "critical_path": critical,
        "ready": [n["node_id"] for n in core.ready_nodes(plan)],
        "waiting": pending_with_reason,
    }


def render_graph(plan: dict, result: dict) -> str:
    nodes = plan["nodes"]
    by_id = {n["node_id"]: n for n in nodes}
    lines = [f"# 依赖图 · 计划 v{plan['plan_version']}", ""]
    lines.append(f"- 节点数：{result['total']}")
    lines.append(f"- 关键路径：{' → '.join(result['critical_path']) or '-'}")
    lines.append("")
    lines.append("## Mermaid")
    lines.append("")
    lines.append("```mermaid")
    lines.append("graph TD")
    for node in nodes:
        nid = node["node_id"]
        label = f"{nid} {node['node_name']}"
        if node.get("status") == "superseded":
            label += " (superseded)"
        lines.append(f'  {nid.replace("-", "_")}["{label}"]')
    for node in nodes:
        for dep in node.get("dependencies", []):
            if dep in by_id:
                lines.append(f"  {dep.replace('-', '_')} --> {node['node_id'].replace('-', '_')}")
    lines.append("```")
    lines.append("")
    lines.append("## 分层（同层无依赖，可作并行候选）")
    lines.append("")
    for level in sorted(result["levels"]):
        members = result["levels"][level]
        if level == 0:
            lines.append(f"- L0（分层失败）：{', '.join(members)}")
        else:
            lines.append(f"- L{level}：{', '.join(members)}")
    lines.append("")
    lines.append("## 依赖明细")
    lines.append("")
    for node in nodes:
        deps = node.get("dependencies") or []
        text = ", ".join(deps) if deps else "无"
        lines.append(f"- `{node['node_id']}` ← {text}（{node.get('status')}）")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DevFlow 依赖检查")
    parser.add_argument("--project", default=".")
    parser.add_argument("--plan", type=int, default=None)
    parser.add_argument("--write", action="store_true", help="写入 dependency-graph-vN.md")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        root = core.find_root(Path(args.project))
        paths = core.Paths(root)
        project = core.load_json(paths.project)
        version = args.plan or project.get("current_plan_version")
        if not version:
            raise DevFlowError("没有可检查的计划")
        plan = core.load_json(paths.backlog(version))
    except DevFlowError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    result = analyse(plan)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"== 依赖检查 · 计划 v{version} ==")
        if result["problems"]:
            print(f"✗ 问题 {len(result['problems'])}")
            for item in result["problems"]:
                print(f"  - {item}")
        else:
            print("✓ 无循环依赖，依赖节点全部存在")
        if result["critical_path"]:
            print(f"- 关键路径（{len(result['critical_path'])} 层）：{' → '.join(result['critical_path'])}")
        print("- 分层：")
        for level in sorted(result["levels"]):
            print(f"    L{level}: {', '.join(result['levels'][level])}")
        print(f"- 当前可执行：{', '.join(result['ready']) or '无'}")
        if result["waiting"]:
            print("- 等待依赖：")
            for item in result["waiting"]:
                print(f"    {item['node_id']} ← {', '.join(item['waiting_for'])}")

    if args.write:
        out = paths.graph(version)
        core.atomic_write_text(out, render_graph(plan, result))
        print(f"依赖图已写入：{out}")

    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
