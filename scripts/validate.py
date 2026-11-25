#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DevFlow 校验器：JSON Schema 结构校验 + 语义一致性校验。

用法：
    python validate.py --project .              # 校验当前生效计划
    python validate.py --project . --plan 2     # 校验指定版本
    python validate.py --project . --strict     # 警告也当错误
    python validate.py --project . --json       # 机器可读输出
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core
from dfcore import DevFlowError


# --------------------------------------------------------------------------
# JSON Schema 子集校验器
# --------------------------------------------------------------------------


class SchemaValidator:
    """支持 $ref / type / enum / const / required / properties / items /
    additionalProperties / pattern / 长度与数值范围 / allOf / anyOf / oneOf。

    够用就好：不实现 format、dependencies、if-then-else 等用不到的特性。
    """

    def __init__(self, schema_dir: Path):
        self.schema_dir = Path(schema_dir)
        self._cache: dict[Path, dict] = {}

    def load(self, name: str) -> dict:
        path = self.schema_dir / name
        if path not in self._cache:
            self._cache[path] = core.load_json(path)
        return self._cache[path]

    def validate(self, data, schema_name: str) -> list[str]:
        schema = self.load(schema_name)
        errors: list[str] = []
        self._check(data, schema, "$", self.schema_dir / schema_name, errors, set())
        return errors

    # -- 内部 --------------------------------------------------------------

    def _resolve(self, ref: str, base: Path):
        if ref.startswith("#/"):
            doc = self._cache.get(base) or core.load_json(base)
            node = doc
            for part in ref[2:].split("/"):
                node = node[part]
            return node, base
        file_part, _, pointer = ref.partition("#")
        target = (base.parent / file_part).resolve()
        doc = self._cache.get(target)
        if doc is None:
            doc = core.load_json(target)
            self._cache[target] = doc
        node = doc
        if pointer.startswith("/"):
            for part in pointer[1:].split("/"):
                node = node[part]
        return node, target

    def _check(self, value, schema: dict, path: str, base: Path, errors: list[str], seen: set):
        if not isinstance(schema, dict):
            return

        if "$ref" in schema:
            key = (str(base), schema["$ref"])
            if key in seen:
                return
            seen.add(key)
            resolved, new_base = self._resolve(schema["$ref"], base)
            self._check(value, resolved, path, new_base, errors, seen)
            return

        if "const" in schema and value != schema["const"]:
            errors.append(f"{path}: 必须等于 {schema['const']!r}")

        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} 不在允许值 {schema['enum']}")

        expected = schema.get("type")
        if expected is not None:
            types = expected if isinstance(expected, list) else [expected]
            if not any(self._type_ok(value, t) for t in types):
                errors.append(f"{path}: 类型应为 {'/'.join(types)}，实际是 {type(value).__name__}")
                return

        if isinstance(value, dict):
            required = schema.get("required", [])
            for key in required:
                if key not in value:
                    errors.append(f"{path}: 缺少必填字段 {key}")
            props = schema.get("properties", {})
            extra_ok = schema.get("additionalProperties", True)
            for key, item in value.items():
                if key in props:
                    self._check(item, props[key], f"{path}.{key}", base, errors, seen)
                elif extra_ok is False:
                    errors.append(f"{path}: 不允许的字段 {key}")
                elif isinstance(extra_ok, dict):
                    self._check(item, extra_ok, f"{path}.{key}", base, errors, seen)

        if isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(value):
                    self._check(item, item_schema, f"{path}[{idx}]", base, errors, seen)
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(f"{path}: 至少需要 {schema['minItems']} 项，实际 {len(value)}")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(f"{path}: 最多 {schema['maxItems']} 项，实际 {len(value)}")
            if schema.get("uniqueItems"):
                dumped = [json.dumps(i, sort_keys=True, ensure_ascii=False) for i in value]
                if len(set(dumped)) != len(dumped):
                    errors.append(f"{path}: 存在重复项")

        if isinstance(value, str):
            pattern = schema.get("pattern")
            if pattern and not __import__("re").search(pattern, value):
                errors.append(f"{path}: {value!r} 不匹配 {pattern}")
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(f"{path}: 长度不足 {schema['minLength']}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: 不能小于 {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: 不能大于 {schema['maximum']}")

        for sub in schema.get("allOf", []) or []:
            self._check(value, sub, path, base, errors, seen)
        for key in ("anyOf", "oneOf"):
            subs = schema.get(key) or []
            if not subs:
                continue
            ok = 0
            for sub in subs:
                tmp: list[str] = []
                self._check(value, sub, path, base, tmp, set(seen))
                if not tmp:
                    ok += 1
            if ok == 0:
                errors.append(f"{path}: 不满足 {key} 中的任一结构")
            elif key == "oneOf" and ok > 1:
                errors.append(f"{path}: 同时满足 {key} 中多个结构")

    @staticmethod
    def _type_ok(value, expected: str) -> bool:
        if expected == "object":
            return isinstance(value, dict)
        if expected == "array":
            return isinstance(value, list)
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "null":
            return value is None
        return True


# --------------------------------------------------------------------------
# 语义校验
# --------------------------------------------------------------------------


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_config(skill_dir: Path) -> dict:
    config = {}
    for name in ("defaults", "task-rules", "quality-gates"):
        json_path = skill_dir / "config" / f"{name}.json"
        yaml_path = skill_dir / "config" / f"{name}.yaml"
        if json_path.exists():
            config[name] = core.load_config_file(json_path)
        elif yaml_path.exists():
            config[name] = core.load_config_file(yaml_path)
    return config


def validate_all(root: Path, skill_dir: Path, plan_version: int | None = None, strict: bool = False) -> Report:
    report = Report()
    paths = core.Paths(root)
    sv = SchemaValidator(skill_dir / "schemas")
    config = load_config(skill_dir)
    rules = config.get("task-rules", {})
    gates = config.get("quality-gates", {})

    if not paths.project.exists():
        report.error(f"未找到 {paths.project}，请先运行 df.py init")
        return report

    # 1. project.json
    project = core.load_json(paths.project)
    for err in sv.validate(project, "project.schema.json"):
        report.error(f"project.json {err}")

    # 2. 计划文件与版本一致性
    versions = []
    if paths.plans.exists():
        for p in sorted(paths.plans.glob("backlog-v*.json")):
            name = p.name
            if not name.startswith("backlog-v"):
                continue
            try:
                ver = int(name[len("backlog-v") : -len(".json")])
            except ValueError:
                report.error(f"计划文件名不合法：{name}")
                continue
            versions.append(ver)
            plan = core.load_json(p)
            for err in sv.validate(plan, "backlog.schema.json"):
                report.error(f"{name} {err}")
            if plan.get("plan_version") != ver:
                report.error(f"{name}: plan_version={plan.get('plan_version')} 与文件名不一致")
            if plan.get("project_id") != project.get("project_id"):
                report.error(f"{name}: project_id 与 project.json 不一致")
            if plan.get("status") == "active" and ver != project.get("current_plan_version"):
                report.error(
                    f"{name}: 标记为 active，但 project.current_plan_version="
                    f"{project.get('current_plan_version')}；同一时间只能有一个生效计划"
                )

    active = [v for v in versions if core.load_json(paths.backlog(v)).get("status") == "active"]
    if len(active) > 1:
        report.error(f"存在多个 active 计划：{active}")
    if project.get("current_plan_version") and project["current_plan_version"] not in versions:
        report.error(f"project.current_plan_version={project['current_plan_version']} 没有对应文件")

    # 3. 需求追踪
    traceability = core.load_traceability(paths)
    req_ids: set[str] = set()
    if traceability:
        for err in sv.validate(traceability, "requirements.schema.json"):
            report.error(f"traceability.json {err}")
        req_ids = {r["req_id"] for r in traceability.get("requirements", [])}
        for req in traceability.get("requirements", []):
            if req.get("status") == "active" and not req.get("acceptance"):
                report.warn(f"{req['req_id']} 没有验收标准（acceptance）")
    elif project.get("current_requirement_version"):
        report.warn("project 记录了需求版本，但 requirements/traceability.json 不存在")

    rv = project.get("current_requirement_version")
    if rv and not paths.confirmed_requirements(rv).exists():
        report.error(f"缺少需求基准文件 {paths.rel(paths.confirmed_requirements(rv))}")

    # 4. 选定计划做深度校验
    target_version = plan_version or project.get("current_plan_version")
    if not target_version:
        report.warn("当前没有生效计划（current_plan_version 为空），跳过计划校验")
        return _finish(report, strict)

    if target_version not in versions:
        report.error(f"计划 v{target_version} 不存在")
        return _finish(report, strict)

    plan = core.load_json(paths.backlog(target_version))
    _validate_plan(report, paths, plan, project, req_ids, rules, gates, sv)

    # 5. 变更记录
    if paths.changes.exists():
        for cr_dir in sorted(paths.changes.iterdir()):
            if not cr_dir.is_dir():
                continue
            cr_json = cr_dir / "change-request.json"
            if not cr_json.exists():
                report.warn(f"{cr_dir.name} 缺少 change-request.json")
                continue
            cr = core.load_json(cr_json)
            for err in sv.validate(cr, "change.schema.json"):
                report.error(f"{cr_dir.name}/change-request.json {err}")
            if cr.get("change_id") != cr_dir.name:
                report.error(f"{cr_dir.name}: change_id={cr.get('change_id')} 与目录名不一致")

    return _finish(report, strict)


def _validate_plan(report, paths, plan, project, req_ids, rules, gates, sv):
    nodes = plan.get("nodes", [])
    by_id = {}
    version = plan.get("plan_version")

    decomposition = rules.get("decomposition", {})
    parallel_rules = rules.get("parallel", {})
    dependency_rules = rules.get("dependency", {})
    max_nodes = decomposition.get("max_nodes", 60)
    max_files = decomposition.get("max_files_per_node", 8)
    min_ac = decomposition.get("min_acceptance_criteria", 1)

    if len(nodes) > max_nodes:
        report.warn(f"计划 v{version} 有 {len(nodes)} 个节点，超过建议上限 {max_nodes}，考虑合并")

    # 节点基本结构与唯一性
    for idx, node in enumerate(nodes):
        nid = node.get("node_id", f"#{idx}")
        if nid in by_id:
            report.error(f"{nid}: node_id 重复")
        by_id[nid] = node

        deps = node.get("dependencies", [])
        if nid in deps:
            report.error(f"{nid}: 依赖了自己")
        if len(set(deps)) != len(deps):
            report.error(f"{nid}: dependencies 有重复项")

        if len(node.get("acceptance_criteria", [])) < min_ac:
            report.error(f"{nid}: 验收标准少于 {min_ac} 条")
        if len(node.get("files_scope", [])) > max_files:
            report.warn(
                f"{nid}: files_scope 有 {len(node.get('files_scope', []))} 项，"
                f"超过建议值 {max_files}，可能拆得不够细"
            )

        if node.get("parallelizable") and not node.get("parallel_group"):
            report.error(f"{nid}: parallelizable=true 但没有 parallel_group")
        if node.get("parallel_group") and not node.get("parallelizable"):
            report.warn(f"{nid}: 有 parallel_group 但 parallelizable=false")

        if node.get("no_test") and not node.get("no_test_reason"):
            report.error(f"{nid}: no_test=true 但没有 no_test_reason")

        if node.get("status") == "superseded" and not node.get("superseded_by"):
            report.warn(f"{nid}: 状态为 superseded 但没有记录 superseded_by")
        if node.get("status") == "blocked" and not node.get("blocked_reason"):
            report.warn(f"{nid}: 状态为 blocked 但没有 blocked_reason")

        # 提示词
        prompt_ref = node.get("prompt_file")
        if prompt_ref:
            prompt_path = paths.root / prompt_ref
            if not prompt_path.exists():
                if node.get("status") not in ("superseded", "cancelled"):
                    msg = f"{nid}: 提示词文件不存在 {prompt_ref}"
                    if plan.get("status") == "draft":
                        report.warn(msg + "（草稿阶段允许，激活前必须补齐）")
                    else:
                        report.error(msg)
            else:
                _check_prompt(report, nid, prompt_path, rules)

        # 需求追踪
        for rid in node.get("requirement_ids", []):
            if req_ids and rid not in req_ids:
                report.error(f"{nid}: 引用了不存在的需求 {rid}")

        # 测试证据：文件存在就按 schema 校验；缺证据只在验收门槛里判定
        tr_path = core.report_path(paths, node, "test_report")
        if tr_path.exists():
            tr = core.load_json(tr_path)
            for err in sv.validate(tr, "test-report.schema.json"):
                report.error(f"{nid}/test-report.json {err}")
            if tr.get("node_id") != nid:
                report.error(f"{nid}/test-report.json: node_id 是 {tr.get('node_id')}，对不上")

    # 依赖完整性
    for node in nodes:
        nid = node["node_id"]
        for dep in node.get("dependencies", []):
            target = by_id.get(dep)
            if target is None:
                report.error(f"{nid}: 依赖的节点 {dep} 不在本计划中")
                continue
            if target.get("status") == "superseded":
                report.error(f"{nid}: 依赖的 {dep} 已被取代，需要改指向新节点")
            if target.get("status") == "cancelled":
                report.error(f"{nid}: 依赖的 {dep} 已取消，节点永远无法执行")
            if node.get("status") in ("ready", "in_progress", "needs_review") and \
                    target.get("status") != "completed":
                report.error(
                    f"{nid}: 状态为 {node.get('status')}，但前置依赖 {dep} 仍是 "
                    f"{target.get('status')}；依赖未完成不能执行"
                )
            if node.get("status") == "completed" and target.get("status") != "completed":
                if not node.get("remark"):
                    report.warn(
                        f"{nid}: 已完成，但依赖 {dep} 当前是 {target.get('status')}"
                        "（变更返工导致）；在 remark 里写明为什么不需要重测"
                    )

    # 环检测
    cycles = core.find_cycles(nodes)
    for cycle in cycles:
        report.error("依赖循环：" + " → ".join(cycle))
    if cycles:
        return

    # 并行组
    groups: dict[str, list[dict]] = {}
    for node in nodes:
        if node.get("parallel_group"):
            groups.setdefault(node["parallel_group"], []).append(node)

    max_group = parallel_rules.get("max_group_size", 3)
    for name, members in groups.items():
        if len(members) > max_group:
            report.warn(f"并行组 {name} 有 {len(members)} 个节点，超过建议上限 {max_group}")
        ids = [m["node_id"] for m in members]
        for member in members:
            for dep in member.get("dependencies", []):
                if dep in ids:
                    report.error(
                        f"并行组 {name} 内 {member['node_id']} 依赖 {dep}，不能并行执行"
                    )
        if parallel_rules.get("require_no_file_overlap", True):
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    overlap = _scope_overlap(members[i], members[j])
                    if overlap:
                        report.warn(
                            f"并行组 {name}: {members[i]['node_id']} 与 {members[j]['node_id']} "
                            f"的文件范围重叠（{', '.join(sorted(overlap)[:3])}），并行有覆盖风险"
                        )

    # 并行组内成员必须已 ready 或更后，否则说明规划不自洽
    for name, members in groups.items():
        if any(m.get("status") == "pending" for m in members) and \
                any(m.get("status") in ("ready", "in_progress") for m in members):
            report.warn(f"并行组 {name} 内状态不一致，检查依赖是否漏配")

    # 需求覆盖
    if req_ids:
        covered = {rid for n in nodes for rid in n.get("requirement_ids", [])}
        for rid in sorted(req_ids - covered):
            report.warn(f"需求 {rid} 没有被任何节点覆盖")

    # 完成节点必须有证据
    node_accept_gates = gates.get("gates", {}).get("node_accept", {})
    for node in nodes:
        if node.get("status") != "completed":
            continue
        problems = core.evidence_problems(paths, node, gates)
        for problem in problems:
            report.error(problem)

    # 计划状态与需求版本
    if plan.get("status") == "active":
        rv = plan.get("requirement_version")
        if rv and not paths.confirmed_requirements(rv).exists():
            report.error(f"计划 v{version} 引用的需求基准 v{rv} 不存在")


def _scope_overlap(a: dict, b: dict) -> set[str]:
    out = set()
    for x in a.get("files_scope", []):
        for y in b.get("files_scope", []):
            nx = x.replace("\\", "/").rstrip("/")
            ny = y.replace("\\", "/").rstrip("/")
            if nx == ny or nx.startswith(ny + "/") or ny.startswith(nx + "/"):
                out.add(nx)
    return out


def _check_prompt(report, node_id: str, path: Path, rules: dict):
    text = core.read_text(path)
    if len(text.strip()) < 300:
        report.error(f"{node_id}: 提示词过短（{len(text.strip())} 字符），无法独立执行")
        return
    required = rules.get("prompt", {}).get("required_sections", [])
    missing = [sec for sec in required if sec not in text]
    if missing:
        report.warn(f"{node_id}: 提示词缺少段落 {', '.join(missing)}")


def _finish(report: Report, strict: bool) -> Report:
    if strict:
        report.errors.extend(f"[strict] {w}" for w in report.warnings)
        report.warnings = []
    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def print_report(report: Report, title: str = "DevFlow 校验") -> None:
    print(f"== {title} ==")
    if report.errors:
        print(f"✗ 错误 {len(report.errors)}")
        for item in report.errors:
            print(f"  - {item}")
    if report.warnings:
        print(f"! 警告 {len(report.warnings)}")
        for item in report.warnings:
            print(f"  - {item}")
    if report.ok and not report.warnings:
        print("✓ 全部通过")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DevFlow 校验器")
    parser.add_argument("--project", default=".", help="项目根目录")
    parser.add_argument("--plan", type=int, default=None, help="校验指定计划版本")
    parser.add_argument("--strict", action="store_true", help="警告视为错误")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出结论")
    args = parser.parse_args(argv)

    try:
        root = Path(args.project).resolve()
        if not (root / core.DF_DIRNAME).exists():
            root = core.find_root(Path(args.project))
        skill_dir = Path(__file__).resolve().parent.parent
        report = validate_all(root, skill_dir, args.plan, args.strict)
    except DevFlowError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            {"ok": report.ok, "errors": report.errors, "warnings": report.warnings},
            ensure_ascii=False, indent=2,
        ))
    elif args.quiet:
        print("OK" if report.ok else f"FAIL {len(report.errors)}")
    else:
        print_report(report)

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
