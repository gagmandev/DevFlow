"""DevFlow 引擎单元测试。

只测"流程引擎"本身：结构校验、依赖分析、状态机、证据门槛、看板渲染。
示例项目的业务测试在 examples/sample-project/tests/ 下。

    python -m unittest discover -s tests -v
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import df  # noqa: E402
import dfcore as core  # noqa: E402
import dependency_check  # noqa: E402
import render_board  # noqa: E402
import validate as validator  # noqa: E402


def quiet(func, argv):
    """执行并同时捕获 stdout / stderr —— 业务错误是打到 stderr 的。"""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = func(argv)
    return code, out.getvalue() + err.getvalue()


def minimal_node(node_id="DF-001", **overrides):
    node = {
        "node_id": node_id,
        "node_name": "节点",
        "phase": "Foundation",
        "type": "infrastructure",
        "priority": "P0",
        "description": "做一件事",
        "requirement_ids": [],
        "dependencies": [],
        "parallelizable": False,
        "parallel_group": None,
        "acceptance_criteria": ["命令退出码为 0"],
        "inputs": [],
        "outputs": ["a.py"],
        "files_scope": ["a.py"],
        "test_strategy": ["跑一次"],
        "status": "pending",
        "prompt_file": f".devflow/nodes/{node_id}/prompt.md",
    }
    node.update(overrides)
    return node


class TempProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        code, out = quiet(df.main, ["init", "--project", str(self.root), "--name", "测试项目", "--id", "t"])
        self.assertEqual(code, 0, out)
        self.paths = core.Paths(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def write_plan_file(self, nodes, name=".import.json", extra=None):
        data = {"requirement_version": 1, "nodes": nodes}
        data.update(extra or {})
        path = self.paths.plans / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def write_requirement_doc(self, version=1):
        self.paths.requirements.mkdir(parents=True, exist_ok=True)
        self.paths.confirmed_requirements(version).write_text(
            f"# 需求确认说明书 v{version}\n\n## 功能需求\n\n- REQ-001 做一件事\n\n"
            "## 业务规则\n\n- 没有特殊规则\n\n## 验收标准\n\n- [ ] 命令退出码为 0\n"
            + "填充内容。" * 40,
            encoding="utf-8",
        )

    def write_reports(self, node_id, node_dir=None, test_status="passed", failed=0):
        node_dir = node_dir or (self.paths.nodes / node_id)
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "execution-report.md").write_text(
            "# 报告\n\n```devflow-execution\n"
            + json.dumps(
                {
                    "node_id": node_id,
                    "result": "success",
                    "modified_files": ["a.py"],
                    "acceptance": [{"criterion": "命令退出码为 0", "met": True, "evidence": "test_x"}],
                    "assumptions": [],
                    "leftovers": [],
                },
                ensure_ascii=False,
            )
            + "\n```\n",
            encoding="utf-8",
        )
        (node_dir / "test-report.json").write_text(
            json.dumps(
                {
                    "node_id": node_id,
                    "status": test_status,
                    "executed_at": "2026-11-25T10:00:00+08:00",
                    "commands": [{"cmd": "python -m unittest", "exit_code": 0}],
                    "summary": {"total": 1, "passed": 1 - failed, "failed": failed},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


class InitTest(TempProject):
    def test_creates_project_file(self):
        self.assertTrue(self.paths.project.exists())
        data = core.load_json(self.paths.project)
        self.assertEqual(data["project_id"], "t")
        self.assertEqual(data["project_type"], "greenfield")
        self.assertIsNone(data["current_plan_version"])

    def test_gitignore_updated(self):
        text = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".devflow/.lock", text)

    def test_init_refuses_second_time(self):
        code, _ = quiet(df.main, ["init", "--project", str(self.root), "--name", "x"])
        self.assertEqual(code, 2)


class ConfigTest(unittest.TestCase):
    def test_mini_yaml_parses_defaults(self):
        data = core.load_config_file(SKILL_DIR / "config" / "defaults.yaml")
        self.assertEqual(data["workflow"]["max_auto_fix_rounds"], 2)
        self.assertFalse(data["workflow"]["executor_can_write_board"])
        self.assertEqual(data["ids"]["node_prefix"], "DF-")

    def test_mini_yaml_parses_lists(self):
        data = core.load_config_file(SKILL_DIR / "config" / "task-rules.yaml")
        self.assertIn("Foundation", data["phases"])
        self.assertEqual(data["priority_order"][0], "P0")
        self.assertIn("角色", data["prompt"]["required_sections"])


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.sv = validator.SchemaValidator(SKILL_DIR / "schemas")

    def test_valid_node_passes(self):
        self.assertEqual(self.sv.validate(minimal_node(), "node.schema.json"), [])

    def test_missing_required_field(self):
        node = minimal_node()
        del node["acceptance_criteria"]
        errors = self.sv.validate(node, "node.schema.json")
        self.assertTrue(any("acceptance_criteria" in e for e in errors))

    def test_bad_enum(self):
        errors = self.sv.validate(minimal_node(phase="Whatever"), "node.schema.json")
        self.assertTrue(any("不在允许值" in e for e in errors))

    def test_unknown_field_rejected(self):
        errors = self.sv.validate(minimal_node(unknown_key=1), "node.schema.json")
        self.assertTrue(any("不允许的字段" in e for e in errors))

    def test_bad_node_id_pattern(self):
        errors = self.sv.validate(minimal_node(node_id="TASK-1"), "node.schema.json")
        self.assertTrue(errors)

    def test_backlog_external_ref(self):
        plan = {
            "schema_version": "1.0",
            "project_id": "t",
            "plan_version": 1,
            "requirement_version": 1,
            "status": "draft",
            "created_at": "2026-11-25T10:00:00+08:00",
            "nodes": [minimal_node()],
        }
        self.assertEqual(self.sv.validate(plan, "backlog.schema.json"), [])

    def test_backlog_requires_nodes(self):
        plan = {
            "schema_version": "1.0",
            "project_id": "t",
            "plan_version": 1,
            "requirement_version": 1,
            "status": "draft",
            "created_at": "x",
            "nodes": [],
        }
        self.assertTrue(self.sv.validate(plan, "backlog.schema.json"))


class GraphTest(unittest.TestCase):
    def test_cycle_detected(self):
        nodes = [
            minimal_node("DF-001", dependencies=["DF-003"]),
            minimal_node("DF-002", dependencies=["DF-001"]),
            minimal_node("DF-003", dependencies=["DF-002"]),
        ]
        cycles = core.find_cycles(nodes)
        self.assertEqual(len(cycles), 1)
        self.assertIn("DF-001", cycles[0])

    def test_no_cycle(self):
        nodes = [
            minimal_node("DF-001"),
            minimal_node("DF-002", dependencies=["DF-001"]),
        ]
        self.assertEqual(core.find_cycles(nodes), [])

    def test_topological_order(self):
        nodes = [
            minimal_node("DF-001"),
            minimal_node("DF-002", dependencies=["DF-001"]),
            minimal_node("DF-003", dependencies=["DF-001"]),
        ]
        self.assertEqual(core.topological_order(nodes), ["DF-001", "DF-002", "DF-003"])

    def test_topological_order_raises(self):
        nodes = [
            minimal_node("DF-001", dependencies=["DF-002"]),
            minimal_node("DF-002", dependencies=["DF-001"]),
        ]
        with self.assertRaises(core.DevFlowError):
            core.topological_order(nodes)

    def test_layering_and_critical_path(self):
        nodes = [
            minimal_node("DF-001"),
            minimal_node("DF-002", dependencies=["DF-001"]),
            minimal_node("DF-003", dependencies=["DF-001"]),
            minimal_node("DF-004", dependencies=["DF-002", "DF-003"]),
        ]
        result = dependency_check.analyse(
            {"plan_version": 1, "nodes": nodes}
        )
        self.assertEqual(result["levels"][1], ["DF-001"])
        self.assertEqual(result["levels"][3], ["DF-004"])
        self.assertEqual(len(result["critical_path"]), 3)
        self.assertEqual(result["problems"], [])


class ReadyTest(unittest.TestCase):
    def test_only_root_becomes_ready(self):
        nodes = [
            minimal_node("DF-001"),
            minimal_node("DF-002", dependencies=["DF-001"]),
            minimal_node("DF-003", dependencies=["DF-002"]),
        ]
        plan = {"plan_version": 1, "nodes": nodes}
        changes = core.recompute_ready(plan)
        self.assertEqual(changes, [("DF-001", "pending", "ready")])
        self.assertEqual([n["node_id"] for n in core.ready_nodes(plan)], ["DF-001"])

    def test_downstream_ready_after_complete(self):
        nodes = [
            minimal_node("DF-001", status="completed"),
            minimal_node("DF-002", dependencies=["DF-001"]),
        ]
        plan = {"plan_version": 1, "nodes": nodes}
        core.recompute_ready(plan)
        self.assertEqual(nodes[1]["status"], "ready")

    def test_unmet_dependency_blocks(self):
        nodes = [
            minimal_node("DF-001", status="in_progress"),
            minimal_node("DF-002", dependencies=["DF-001"]),
        ]
        plan = {"plan_version": 1, "nodes": nodes}
        self.assertEqual(core.unmet_dependencies(nodes[1], plan), ["DF-001"])


class StateMachineTest(unittest.TestCase):
    def test_completed_cannot_go_back(self):
        self.assertEqual(core.TRANSITIONS["completed"], {"superseded"})

    def test_superseded_is_terminal(self):
        self.assertEqual(core.TRANSITIONS["superseded"], set())

    def test_illegal_transition_rejected_by_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quiet(df.main, ["init", "--project", str(root), "--name", "T", "--id", "t"])
            paths = core.Paths(root)
            paths.plans.mkdir(parents=True, exist_ok=True)
            plan = {
                "schema_version": "1.0",
                "project_id": "t",
                "plan_version": 1,
                "requirement_version": 1,
                "status": "draft",
                "created_at": "x",
                "nodes": [minimal_node(status="completed", completed_at="x")],
            }
            (paths.plans / "backlog-v1.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8"
            )
            code, _ = quiet(df.main, ["start", "DF-001", "--project", str(root)])
            self.assertEqual(code, 2)


class EvidenceTest(TempProject):
    def test_missing_reports_block_acceptance(self):
        node = minimal_node(status="needs_review")
        problems = core.evidence_problems(self.paths, node)
        self.assertTrue(any("执行报告" in p for p in problems))
        self.assertTrue(any("test-report" in p for p in problems))

    def test_complete_reports_pass(self):
        node = minimal_node(status="needs_review")
        self.write_reports("DF-001")
        self.assertEqual(core.evidence_problems(self.paths, node), [])

    def test_failed_test_blocks(self):
        node = minimal_node(status="needs_review")
        self.write_reports("DF-001", test_status="failed", failed=1)
        problems = core.evidence_problems(self.paths, node)
        self.assertTrue(any("failed" in p or "passed" in p for p in problems))

    def test_no_test_requires_reason(self):
        node = minimal_node(status="needs_review", no_test=True)
        self.write_reports("DF-001")
        problems = core.evidence_problems(self.paths, node)
        self.assertTrue(any("no_test_reason" in p for p in problems))

    def test_out_of_scope_detection(self):
        node = minimal_node(files_scope=["src/a.py"])
        self.assertEqual(core.out_of_scope_files(node, ["src/a.py", "src/b.py"]), ["src/b.py"])


class PlanImportTest(TempProject):
    def test_cycle_rejected(self):
        path = self.write_plan_file([
            minimal_node("DF-001", dependencies=["DF-002"]),
            minimal_node("DF-002", dependencies=["DF-001"]),
        ])
        code, out = quiet(df.main, [
            "plan-import", "--file", str(path), "--version", "1", "--project", str(self.root)
        ])
        self.assertEqual(code, 2)
        self.assertIn("循环", out + str(self.paths.plans))
        self.assertFalse(self.paths.backlog(1).exists())

    def test_missing_dependency_rejected(self):
        path = self.write_plan_file([minimal_node("DF-001", dependencies=["DF-099"])])
        code, _ = quiet(df.main, [
            "plan-import", "--file", str(path), "--version", "1", "--project", str(self.root)
        ])
        self.assertEqual(code, 2)

    def test_draft_is_not_executable(self):
        path = self.write_plan_file([minimal_node()])
        code, _ = quiet(df.main, [
            "plan-import", "--file", str(path), "--version", "1", "--project", str(self.root)
        ])
        self.assertEqual(code, 0)
        code, out = quiet(df.main, ["next", "--project", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("没有生效的计划", out)

    def test_import_computes_ready_and_renders_board(self):
        path = self.write_plan_file([minimal_node()])
        quiet(df.main, ["plan-import", "--file", str(path), "--version", "1", "--project", str(self.root)])
        plan = core.load_json(self.paths.backlog(1))
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(plan["nodes"][0]["status"], "ready")
        self.assertTrue(self.paths.board(1).exists())


class LifecycleTest(TempProject):
    def test_full_single_node_flow(self):
        path = self.write_plan_file([minimal_node()])
        code, _ = quiet(df.main, [
            "plan-import", "--file", str(path), "--version", "1", "--project", str(self.root)
        ])
        self.assertEqual(code, 0)

        code, _ = quiet(df.main, ["prompt-init", "--all", "--project", str(self.root)])
        self.assertEqual(code, 0)
        self.assertTrue((self.root / ".devflow/nodes/DF-001/prompt.md").exists())

        self.write_requirement_doc(1)
        code, out = quiet(df.main, [
            "plan-activate", "--version", "1", "--project", str(self.root)
        ])
        self.assertEqual(code, 0, out)

        code, _ = quiet(df.main, ["start", "DF-001", "--project", str(self.root)])
        self.assertEqual(code, 0)

        # 没证据不能同步
        code, out = quiet(df.main, ["sync", "DF-001", "--project", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("缺少证据", out)

        self.write_reports("DF-001")
        code, _ = quiet(df.main, ["sync", "DF-001", "--project", str(self.root)])
        self.assertEqual(code, 0)
        node = core.load_json(self.paths.backlog(1))["nodes"][0]
        self.assertEqual(node["status"], "needs_review")

        code, _ = quiet(df.main, ["accept", "DF-001", "--project", str(self.root)])
        self.assertEqual(code, 0)
        plan = core.load_json(self.paths.backlog(1))
        self.assertEqual(plan["nodes"][0]["status"], "completed")
        self.assertEqual(plan["nodes"][0]["accepted_by"], "user")

        code, out = quiet(df.main, ["validate", "--project", str(self.root)])
        self.assertEqual(code, 0, out)

    def test_start_refuses_unmet_dependency(self):
        path = self.write_plan_file([
            minimal_node("DF-001"),
            minimal_node("DF-002", dependencies=["DF-001"]),
        ])
        quiet(df.main, ["plan-import", "--file", str(path), "--version", "1", "--project", str(self.root)])
        quiet(df.main, ["prompt-init", "--all", "--project", str(self.root)])
        self.write_requirement_doc(1)
        code, out = quiet(df.main, [
            "plan-activate", "--version", "1", "--project", str(self.root)
        ])
        self.assertEqual(code, 0, out)

        # DF-001 开始但没完成，DF-002 不该能开始
        code, _ = quiet(df.main, ["start", "DF-001", "--project", str(self.root)])
        self.assertEqual(code, 0)
        code, out = quiet(df.main, ["start", "DF-002", "--project", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("前置依赖未完成", out)


class BoardTest(unittest.TestCase):
    def test_render_contains_stats_and_rows(self):
        plan = {
            "plan_version": 2,
            "requirement_version": 2,
            "status": "active",
            "project_id": "t",
            "nodes": [
                minimal_node("DF-001", status="completed"),
                minimal_node("DF-002", dependencies=["DF-001"], status="ready"),
                minimal_node(
                    "DF-003", dependencies=["DF-001"], status="pending",
                    parallelizable=True, parallel_group="A",
                ),
            ],
        }
        text = render_board.render(plan, {"project_name": "T"}, include_time=False)
        self.assertIn("## 进度", text)
        self.assertIn("DF-001", text)
        self.assertIn("33.3%", text)          # 1/3 有效节点
        self.assertIn("## 当前可执行", text)
        self.assertIn("## 并行组", text)

    def test_render_draft_warning(self):
        plan = {
            "plan_version": 1,
            "requirement_version": 1,
            "status": "draft",
            "project_id": "t",
            "nodes": [minimal_node()],
        }
        text = render_board.render(plan, {}, include_time=False)
        self.assertIn("草稿计划", text)


class AtomicWriteTest(unittest.TestCase):
    def test_no_tmp_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            core.atomic_write_json(path, {"a": 1})
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
            self.assertEqual(core.load_json(path), {"a": 1})


if __name__ == "__main__":
    unittest.main()
