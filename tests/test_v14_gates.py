"""V1.4 生产质量门禁的验收测试。

覆盖：写命令必须加锁、regression-import 终态保护与并发、release 必须先生成报告、
必需检查项不可降级、源码快照、CR 严格绑定、prompt_version、通配符并行冲突。
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core  # noqa: E402
import df  # noqa: E402
from test_p0_hardening import Project, node, quiet  # noqa: E402
from test_p1_workflow import regression_payload, complete_node  # noqa: E402

DF_SCRIPT = str(SCRIPTS / "df.py")


def run_proc(*argv, timeout=120):
    proc = subprocess.run(
        [sys.executable, DF_SCRIPT, *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class WriteCommandLockTest(unittest.TestCase):
    """P0-04：凡是写 .devflow 的命令都必须在锁集合里。"""

    READ_ONLY = {"init", "status", "next", "task", "validate", "doctor", "self-test"}

    def test_all_commands_classified(self):
        source = (SCRIPTS / "df.py").read_text(encoding="utf-8")
        commands = set(re.findall(r'add\("([a-z][a-z-]+)",', source))
        missing = sorted(commands - self.READ_ONLY - df.MUTATING_COMMANDS)
        self.assertEqual(missing, [], f"这些命令会写状态但不在 MUTATING_COMMANDS：{missing}")

    def test_import_is_mutating_and_frozen(self):
        self.assertIn("regression-import", df.MUTATING_COMMANDS)
        self.assertIn("regression-import", df.FROZEN_ON_ACCEPTED)

    def test_accepted_project_rejects_import(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            code, out = complete_node(p)
            self.assertEqual(code, 0, out)
            self.assertEqual(p.run("regression-check")[0], 0)
            path = Path(tempfile.mkdtemp()) / "reg.json"   # 不能放项目里，否则代码快照会变
            path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(p.run("regression-import", "--file", str(path))[0], 0)
            self.assertEqual(p.run("release")[0], 0)
            self.assertEqual(p.run("release-ready")[0], 0)
            self.assertEqual(p.run("release-accept", "--by", "t")[0], 0)

            code, out = p.run("regression-import", "--file", str(path))
            self.assertEqual(code, 2, out)
            self.assertIn("accepted", out)
        finally:
            p.close()

    def test_concurrent_import_no_corruption(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            p.run("regression-check")       # 生成源码快照
            payloads = []
            out_dir = Path(tempfile.mkdtemp())
            for idx in (1, 2):
                data = regression_payload()
                data["executed_by"] = f"runner-{idx}"
                path = out_dir / f"reg-{idx}.json"
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                payloads.append(path)

            procs = [
                subprocess.Popen(
                    [sys.executable, DF_SCRIPT, "regression-import", "--file", str(path),
                     "--project", str(p.root)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                for path in payloads
            ]
            for proc in procs:
                out, _ = proc.communicate(timeout=90)
                self.assertEqual(proc.returncode, 0, out.decode("utf-8", "replace"))

            final = core.load_json(p.paths.regression / "final-report.json")
            self.assertIn(final["executed_by"], ("runner-1", "runner-2"))
            self.assertEqual(final["status"], "passed")
            self.assertEqual(final["checks"][0]["exit_code"], 0)
        finally:
            p.close()


class ReleaseGateTest(unittest.TestCase):
    """P0-05：没有完整交付报告不能 release_ready。"""

    def _ready_project(self):
        p = Project()
        p.import_plan([node("DF-001")])
        p.activate()
        code, out = complete_node(p)
        self.assertEqual(code, 0, out)
        self.assertEqual(p.run("regression-check")[0], 0)
        path = Path(tempfile.mkdtemp()) / "reg.json"
        path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(p.run("regression-import", "--file", str(path))[0], 0)
        return p

    def test_cannot_skip_release_report(self):
        p = self._ready_project()
        try:
            code, out = p.run("release-ready")
            self.assertNotEqual(code, 0, out)
            self.assertIn("release", out.lower())
            self.assertEqual(core.load_json(p.paths.project)["status"], "regression")
        finally:
            p.close()

    def test_empty_release_report_rejected(self):
        p = self._ready_project()
        try:
            self.assertEqual(p.run("release")[0], 0)
            report = p.paths.releases / "release-report.md"
            report.write_text("# 交付报告\n\n待补充\n", encoding="utf-8")
            code, out = p.run("release-ready")
            self.assertNotEqual(code, 0, out)
            self.assertTrue("占位" in out or "章节" in out, out)
        finally:
            p.close()

    def test_placeholder_rejected(self):
        p = self._ready_project()
        try:
            self.assertEqual(p.run("release")[0], 0)
            report = p.paths.releases / "release-report.md"
            text = report.read_text(encoding="utf-8")
            report.write_text(text.replace("## 6. 部署说明", "## 6. 部署说明\n\nTBD"), encoding="utf-8")
            code, out = p.run("release-ready")
            self.assertNotEqual(code, 0, out)
            self.assertIn("占位", out)
        finally:
            p.close()

    def test_version_mismatch_rejected(self):
        p = self._ready_project()
        try:
            self.assertEqual(p.run("release")[0], 0)
            meta_path = p.paths.releases / "release-report.json"
            meta = core.load_json(meta_path)
            meta["plan_version"] = 99
            core.atomic_write_json(meta_path, meta)
            code, out = p.run("release-ready")
            self.assertNotEqual(code, 0, out)
            self.assertIn("对应计划", out)
        finally:
            p.close()

    def test_complete_report_allows_ready(self):
        p = self._ready_project()
        try:
            self.assertEqual(p.run("release")[0], 0)
            code, out = p.run("release-ready")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()


class RequiredCheckTest(unittest.TestCase):
    """P0-01 的配置级校验：必需集合只认配置。"""

    def test_derive_uses_config_not_report(self):
        checks = [
            {"check_id": "build", "required": False, "status": "failed"},
            {"check_id": "unit_test", "required": True, "status": "passed"},
        ]
        # 给了必需集合：按配置判定（build 即使自称 optional，失败也要整体 failed）
        self.assertEqual(df.derive_regression_status(checks, ["build", "unit_test"]), "failed")
        # 没给配置时的兜底：退回报告自述（只有单元测试这么用）
        self.assertEqual(df.derive_regression_status(checks), "passed")

    def test_missing_required_is_never_passed(self):
        checks = [{"check_id": "unit_test", "required": True, "status": "passed"}]
        self.assertEqual(df.derive_regression_status(checks, ["unit_test", "build"]), "failed")


class WildcardScopeTest(unittest.TestCase):
    """P1-06：通配符范围也要能判出冲突。"""

    def _overlap(self, a, b):
        return core.scope_overlap({"files_scope": [a]}, {"files_scope": [b]})

    def test_double_star_covers_file(self):
        self.assertTrue(self._overlap("src/**/*.cs", "src/service.cs"))
        self.assertTrue(self._overlap("src/**", "src/a/b/c.py"))

    def test_single_star_does_not_cross_directory(self):
        self.assertFalse(self._overlap("src/*", "src/deep/a.cs"))
        self.assertTrue(self._overlap("src/*.py", "src/a.py"))

    def test_disjoint_prefixes(self):
        self.assertFalse(self._overlap("api/**", "web/**"))
        self.assertFalse(self._overlap("src/**/*.cs", "tests/data.cs"))

    def test_plan_with_wildcard_conflict_rejected(self):
        p = Project()
        try:
            code, out = p.import_plan([
                node("DF-001", files_scope=["src/**/*.cs"], parallelizable=True, parallel_group="A"),
                node("DF-002", files_scope=["src/service.cs"], parallelizable=True, parallel_group="A"),
            ])
            self.assertEqual(code, 2, out)
            self.assertIn("文件范围重叠", out)
        finally:
            p.close()

    def test_group_wide_exception_does_not_waive(self):
        p = Project()
        try:
            code, out = p.import_plan(
                [
                    node("DF-001", files_scope=["src/**/*.cs"], parallelizable=True, parallel_group="A"),
                    node("DF-002", files_scope=["src/service.cs"], parallelizable=True, parallel_group="A"),
                ],
                parallel_exceptions=[{
                    "group": "A", "reason": "整组豁免", "approved_by": "架构师",
                }],
            )
            self.assertEqual(code, 2, out)
            self.assertIn("文件范围重叠", out)
        finally:
            p.close()


class PromptVersionTest(unittest.TestCase):
    """P1-05：提示词版本必须参与绑定。"""

    def test_stale_prompt_version_blocks_start(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            plan = p.plan()
            plan["nodes"][0]["prompt_version"] = 5   # 计划说提示词是 v5，文件还是 v1
            plan["status"] = "active"
            core.atomic_write_json(p.paths.backlog(1), plan)
            data = core.load_json(p.paths.project)
            data["current_plan_version"] = 1
            core.atomic_write_json(p.paths.project, data)

            code, out = p.run("start", "DF-001")
            self.assertEqual(code, 2, out)
            self.assertIn("prompt_version", out)
        finally:
            p.close()

    def test_current_prompt_version_starts(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.run("prompt-init", "DF-001")
            plan = p.plan()
            plan["status"] = "active"
            core.atomic_write_json(p.paths.backlog(1), plan)
            data = core.load_json(p.paths.project)
            data["current_plan_version"] = 1
            data["status"] = "in_progress"
            core.atomic_write_json(p.paths.project, data)
            code, out = p.run("start", "DF-001")
            self.assertEqual(code, 0, out)
        finally:
            p.close()


class ChangeBindingTest(unittest.TestCase):
    """P1-04：目标计划必须严格属于这个 CR。"""

    def _prepare(self, based_on):
        p = Project()
        p.import_plan([node("DF-001")], version=1)
        p.activate(1)
        p.run("start", "DF-001")
        p.write_evidence()
        p.run("sync", "DF-001")
        p.run("accept", "DF-001")
        p.import_plan(
            [node("DF-001", status="completed"), node("DF-002", dependencies=["DF-001"])],
            version=2,
        )
        p.run("prompt-init", "--all", "--plan", "2")
        code, out = p.run("change-new", "--title", "加节点", "--type", "add")
        assert code == 0, out
        cr_path = p.paths.change_dir("CR-001") / "change-request.json"
        cr = core.load_json(cr_path)
        cr.update({
            "status": "confirmed",
            "base_plan_version": 1,
            "base_requirement_version": 1,
            "target_plan_version": 2,
            "target_requirement_version": 1,
            "impacts": [{"node_id": "DF-002", "action": "add", "reason": "新增"}],
        })
        core.atomic_write_json(cr_path, cr)
        plan = p.plan(2)
        if based_on is not None:
            plan["based_on"] = based_on
        else:
            plan.pop("based_on", None)
        core.atomic_write_json(p.paths.backlog(2), plan)
        return p

    def test_missing_based_on_rejected(self):
        p = self._prepare(None)
        try:
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2, out)
            self.assertIn("based_on", out)
        finally:
            p.close()

    def test_null_change_id_rejected(self):
        p = self._prepare({"change_id": None, "plan_version": 1})
        try:
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2, out)
            self.assertIn("based_on", out)
        finally:
            p.close()

    def test_plan_version_mismatch_rejected(self):
        p = self._prepare({"change_id": "CR-001", "plan_version": 99})
        try:
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2, out)
            self.assertIn("plan_version", out)
        finally:
            p.close()

    def test_correct_binding_applies(self):
        p = self._prepare({"change_id": "CR-001", "plan_version": 1})
        try:
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 0, out)
            self.assertEqual(p.plan(2)["status"], "active")
        finally:
            p.close()


class SourceSnapshotTest(unittest.TestCase):
    """P1-03：回归后代码变了，旧报告失效。"""

    def _project(self):
        p = Project()
        (p.root / "app.py").write_text("v1", encoding="utf-8")
        p.import_plan([node("DF-001", files_scope=["app.py"], outputs=["app.py"])])
        p.activate()
        p.run("start", "DF-001")
        (p.root / "app.py").write_text("v2", encoding="utf-8")
        p.write_evidence(evidence={"modified_files": ["app.py"]})
        p.run("sync", "DF-001")
        p.run("accept", "DF-001")
        return p

    def test_code_change_invalidates_import(self):
        p = self._project()
        try:
            self.assertEqual(p.run("regression-check")[0], 0)
            (p.root / "app.py").write_text("v3", encoding="utf-8")
            path = Path(tempfile.mkdtemp()) / "reg.json"
            path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            code, out = p.run("regression-import", "--file", str(path))
            self.assertEqual(code, 2, out)
            self.assertIn("变化", out)
        finally:
            p.close()

    def test_code_change_invalidates_delivery(self):
        p = self._project()
        try:
            self.assertEqual(p.run("regression-check")[0], 0)
            path = Path(tempfile.mkdtemp()) / "reg.json"
            path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(p.run("regression-import", "--file", str(path))[0], 0)
            self.assertEqual(p.run("release")[0], 0)
            (p.root / "app.py").write_text("v4", encoding="utf-8")
            code, out = p.run("release-ready")
            self.assertEqual(code, 1, out)
            self.assertIn("变化", out)
        finally:
            p.close()

    def test_snapshot_excludes_devflow(self):
        snap1 = core.source_snapshot(SKILL_DIR / "examples" / "sample-project")
        self.assertGreater(snap1["file_count"], 0)
        self.assertIn(snap1["type"], ("git", "files"))
        self.assertEqual(len(snap1["snapshot_id"]), 64)


class RequirementEvidenceTest(unittest.TestCase):
    """P1-02：每条需求的每条验收标准都要有对应证据。"""

    CRITERIA = ["重复开户幂等", "新账户 balance=0"]

    def _project(self, entries, acceptance=None):
        p = Project()
        trace = {
            "schema_version": "1.0",
            "requirement_version": 1,
            "requirements": [{
                "req_id": "REQ-001", "title": "开户", "category": "functional",
                "status": "active", "acceptance": acceptance or self.CRITERIA,
            }],
        }
        trace_path = p.root / "trace.json"
        trace_path.write_text(json.dumps(trace, ensure_ascii=False), encoding="utf-8")
        code, out = p.run("req-import", "--file", str(trace_path), "--version", "1")
        assert code == 0, out
        p.run("req-confirm", "--version", "1")
        trace_path.unlink()
        p.import_plan([node("DF-001", requirement_ids=["REQ-001"])])
        p.activate()
        p.run("start", "DF-001")
        p.write_evidence(evidence={"requirement_acceptance": entries})
        code, out = p.run("sync", "DF-001")
        return p, code, out

    def test_partial_coverage_rejected(self):
        p, code, out = self._project([
            {"req_id": "REQ-001", "criterion": "重复开户幂等", "met": True, "evidence": "test_dup"},
        ])
        try:
            self.assertEqual(code, 1, out)
            self.assertIn("新账户 balance=0", out)
        finally:
            p.close()

    def test_other_requirement_evidence_rejected(self):
        p, code, out = self._project([
            {"req_id": "REQ-999", "criterion": "重复开户幂等", "met": True, "evidence": "test_dup"},
            {"req_id": "REQ-999", "criterion": "新账户 balance=0", "met": True, "evidence": "test_b"},
        ])
        try:
            self.assertEqual(code, 1, out)
            self.assertIn("缺少 REQ-001 验收项的证据", out)
        finally:
            p.close()

    def test_met_false_rejected(self):
        p, code, out = self._project([
            {"req_id": "REQ-001", "criterion": "重复开户幂等", "met": False, "evidence": "test_dup"},
            {"req_id": "REQ-001", "criterion": "新账户 balance=0", "met": True, "evidence": "test_b"},
        ])
        try:
            self.assertEqual(code, 1, out)
            self.assertIn("未满足", out)
        finally:
            p.close()

    def test_full_coverage_passes(self):
        p, code, out = self._project([
            {"req_id": "REQ-001", "criterion": "重复开户幂等", "met": True, "evidence": "test_dup"},
            {"req_id": "REQ-001", "criterion": "新账户 balance=0", "met": True, "evidence": "test_b"},
        ])
        try:
            self.assertEqual(code, 0, out)
        finally:
            p.close()

    def test_legacy_string_acceptance_supported(self):
        p, code, out = self._project([
            {"req_id": "REQ-001", "criterion": "旧的字符串验收", "met": True, "evidence": "test_x"},
        ], acceptance=["旧的字符串验收"])
        try:
            self.assertEqual(code, 0, out)
        finally:
            p.close()


class EmptyScopeTest(unittest.TestCase):
    """P1-07：空 files_scope 的语义唯一：只允许本轮新增的 outputs。"""

    def test_outputs_only_allows_new_file(self):
        n = node("DF-001", files_scope=[], outputs=["new.py"])
        self.assertEqual(core.scope_violations(n, ["new.py"], SKILL_DIR), [])

    def test_outputs_only_rejects_existing_file(self):
        n = node("DF-001", files_scope=[], outputs=["new.py"], scope_baseline=["new.py"])
        problems = core.scope_violations(n, ["new.py"], SKILL_DIR)
        self.assertTrue(problems)
        self.assertIn("files_scope", " ".join(problems))

    def test_outputs_only_rejects_unrelated_file(self):
        n = node("DF-001", files_scope=[], outputs=["new.py"])
        problems = core.scope_violations(n, ["other.py"], SKILL_DIR)
        self.assertTrue(problems)

    def test_deny_policy_rejects_everything(self):
        n = node("DF-001", files_scope=[], outputs=["new.py"])
        problems = core.scope_violations(n, ["new.py"], SKILL_DIR, "deny")
        self.assertTrue(problems)
        self.assertIn("拒绝", " ".join(problems))

    def test_allow_policy_skips(self):
        n = node("DF-001", files_scope=[], outputs=[])
        self.assertEqual(core.scope_violations(n, ["any.py"], SKILL_DIR, "allow"), [])


class LegacyMigrationTest(unittest.TestCase):
    """旧 V1.3 项目迁移：只加字段，不动历史。"""

    def test_migrate_keeps_history_and_status(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            code, out = complete_node(p)
            self.assertEqual(code, 0, out)
            before_plan = core.load_json(p.paths.backlog(1))
            before_report = (p.paths.node_dir("DF-001") / "execution-report.md").read_text(
                encoding="utf-8"
            )

            data = core.load_json(p.paths.project)
            data.pop("quality_profile", None)
            data["schema_version"] = "1.3"
            core.atomic_write_json(p.paths.project, data)

            code, out = p.run("migrate", "--profile", "production")
            self.assertEqual(code, 0, out)
            after = core.load_json(p.paths.project)
            self.assertEqual(after["schema_version"], core.SCHEMA_VERSION)
            self.assertEqual(after["quality_profile"], "production")

            after_plan = core.load_json(p.paths.backlog(1))
            self.assertEqual(
                [n["status"] for n in after_plan["nodes"]],
                [n["status"] for n in before_plan["nodes"]],
            )
            self.assertEqual(
                (p.paths.node_dir("DF-001") / "execution-report.md").read_text(encoding="utf-8"),
                before_report,
            )
        finally:
            p.close()

    def test_legacy_release_shortcut_is_recorded(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            code, out = p.run("migrate", "--legacy-release")
            self.assertEqual(code, 0, out)
            self.assertTrue(core.load_json(p.paths.project)["legacy_release_shortcut"])
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
