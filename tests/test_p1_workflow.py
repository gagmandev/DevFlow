"""P1 工作流正确性测试。

覆盖：需求覆盖阻断、回归 JSON、外部报告导入、Git 变更核对、
并行冲突、提示词版本绑定、变更节点迁移。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core  # noqa: E402
import df  # noqa: E402
from test_p0_hardening import Project, node, quiet  # noqa: E402

REQUIRED_CHECKS = ["build", "unit_test", "integration", "core_flow", "consistency", "requirement_acceptance"]


def regression_payload(plan_version=1, requirement_version=1, status="passed", overrides=None):
    checks = []
    for cid in REQUIRED_CHECKS:
        checks.append({
            "check_id": cid,
            "name": cid,
            "required": True,
            "status": "passed",
            "command": "python -m unittest",
            "exit_code": 0,
            "executed_at": "2026-11-25T15:00:00+08:00",
            "evidence": "Ran 1 test OK",
            "notes": "",
        })
    data = {
        "schema_version": "1.0",
        "plan_version": plan_version,
        "requirement_version": requirement_version,
        "status": status,
        "executed_at": "2026-11-25T15:00:00+08:00",
        "executed_by": "tester",
        "checks": checks,
    }
    if overrides:
        overrides(data)
    return data


def write_traceability(p, requirements):
    p.paths.requirements.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": "1.0", "requirement_version": 1, "requirements": requirements}
    path = p.paths.requirements / "traceability-v1.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return quiet(df.main, ["req-import", "--file", str(path), "--version", "1", "--project", str(p.root)])


def complete_node(p, node_id="DF-001", version=1):
    p.run("start", node_id)
    p.write_evidence(node_id, version=version)
    p.run("sync", node_id)
    return p.run("accept", node_id)


class RegressionCoverageTest(unittest.TestCase):
    """P1-1：需求没覆盖就不能通过回归前置检查。"""

    def _project(self, requirements):
        p = Project()
        p.import_plan([node("DF-001", requirement_ids=["REQ-001"])])
        p.activate()
        p.run("start", "DF-001")
        p.write_evidence(evidence={
            "requirement_acceptance": [
                {"req_id": "REQ-001", "criterion": "ok", "met": True, "evidence": "test_x"},
            ],
        })
        p.run("sync", "DF-001")
        code, out = p.run("accept", "DF-001")
        self.assertEqual(code, 0, out)
        # 需求表在节点完成后再写：带非法状态的追踪表本来就不该通过计划导入
        code, out = write_traceability(p, requirements)
        self.assertEqual(code, 0, out)
        return p

    def test_uncovered_requirement_blocks(self):
        p = self._project([
            {"req_id": "REQ-001", "title": "已覆盖", "category": "functional", "status": "active",
             "acceptance": ["ok"]},
            {"req_id": "REQ-002", "title": "没覆盖", "category": "functional", "status": "active",
             "acceptance": ["ok"]},
        ])
        try:
            code, out = p.run("regression-check")
            self.assertEqual(code, 1)
            self.assertIn("REQ-002 没有对应节点", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "in_progress")
        finally:
            p.close()

    def test_deferred_requires_reason_and_confirmation(self):
        p = self._project([
            {"req_id": "REQ-001", "title": "已覆盖", "category": "functional", "status": "active",
             "acceptance": ["ok"]},
            {"req_id": "REQ-002", "title": "本期不做", "category": "functional",
             "status": "deferred", "acceptance": ["ok"]},
        ])
        try:
            code, out = p.run("regression-check")
            self.assertEqual(code, 1)
            self.assertIn("缺少 reason / confirmed_by", out)
        finally:
            p.close()

    def test_deferred_with_reason_passes(self):
        p = self._project([
            {"req_id": "REQ-001", "title": "已覆盖", "category": "functional", "status": "active",
             "acceptance": ["ok"]},
            {"req_id": "REQ-002", "title": "本期不做", "category": "functional",
             "status": "deferred", "acceptance": ["ok"],
             "reason": "业务方确认延后", "confirmed_by": "产品经理"},
        ])
        try:
            code, out = p.run("regression-check")
            self.assertEqual(code, 0, out)
        finally:
            p.close()

    def test_changed_requires_superseded_by(self):
        """把需求改成 changed 也不是逃生门：必须指向真正的替代需求。"""
        p = Project()
        try:
            p.import_plan([node("DF-001", requirement_ids=["REQ-002"])])
            p.activate()
            p.run("start", "DF-001")
            p.write_evidence(evidence={"requirement_acceptance": [
                {"req_id": "REQ-002", "criterion": "ok", "met": True, "evidence": "test_x"},
            ]})
            p.run("sync", "DF-001")
            code, out = p.run("accept", "DF-001")
            self.assertEqual(code, 0, out)
            code, out = write_traceability(p, [
                {"req_id": "REQ-001", "title": "旧需求", "category": "functional",
                 "status": "changed", "acceptance": ["旧"]},
                {"req_id": "REQ-002", "title": "新需求", "category": "functional",
                 "status": "active", "acceptance": ["ok"]},
            ])
            self.assertEqual(code, 0, out)
            code, out = p.run("regression-check")
            self.assertEqual(code, 1)
            self.assertIn("superseded_by", out)
        finally:
            p.close()

    def test_changed_with_active_replacement_passes(self):
        p = Project()
        try:
            p.import_plan([node("DF-001", requirement_ids=["REQ-002"])])
            p.activate()
            p.run("start", "DF-001")
            p.write_evidence(evidence={"requirement_acceptance": [
                {"req_id": "REQ-002", "criterion": "ok", "met": True, "evidence": "test_x"},
            ]})
            p.run("sync", "DF-001")
            code, out = p.run("accept", "DF-001")
            self.assertEqual(code, 0, out)
            code, out = write_traceability(p, [
                {"req_id": "REQ-001", "title": "旧需求", "category": "functional",
                 "status": "changed", "acceptance": ["旧"], "superseded_by": "REQ-002"},
                {"req_id": "REQ-002", "title": "新需求", "category": "functional",
                 "status": "active", "acceptance": ["ok"]},
            ])
            self.assertEqual(code, 0, out)
            code, out = p.run("regression-check")
            self.assertEqual(code, 0, out)
        finally:
            p.close()

    def test_completed_without_evidence_blocks(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            # 直接把节点标完成，但没有证据
            plan = p.plan()
            plan["nodes"][0]["status"] = "completed"
            core.atomic_write_json(p.paths.backlog(1), plan)
            code, out = p.run("regression-check")
            self.assertEqual(code, 1)
            self.assertIn("缺少执行报告", out)
        finally:
            p.close()

    def test_requirement_acceptance_without_evidence_blocks(self):
        """节点"完成"了，但报告里没有任何 met=true 的验收项 —— 不算验证过需求。"""
        p = Project()
        try:
            p.import_plan([node("DF-001", requirement_ids=["REQ-001"])])
            p.activate()
            p.run("start", "DF-001")
            # 证据里不写验收项（绕过 sync 直接改状态，模拟报告不完整/被手工篡改）
            p.write_evidence(evidence={"acceptance": []})
            plan = p.plan()
            plan["nodes"][0]["status"] = "completed"
            core.atomic_write_json(p.paths.backlog(1), plan)

            code, out = write_traceability(p, [
                {"req_id": "REQ-001", "title": "有节点", "category": "functional",
                 "status": "active", "acceptance": ["命令退出码为 0"]},
            ])
            self.assertEqual(code, 0, out)
            code, out = p.run("regression-check")
            self.assertEqual(code, 1)
            self.assertIn("验收标准没有对应验证证据", out)
        finally:
            p.close()


class RegressionJsonTest(unittest.TestCase):
    """P1-2：JSON 是事实来源，状态必须自洽。"""

    def setUp(self):
        self.p = Project()
        self.p.import_plan([node("DF-001")])
        self.p.activate()
        # 回归报告必须绑定前置快照：先跑一次 regression-check（这里只关心快照）
        self.p.run("regression-check")

    def tearDown(self):
        self.p.close()

    def _import(self, payload):
        path = Path(tempfile.mkdtemp()) / "reg.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.p.run("regression-import", "--file", str(path))

    def test_valid_report_imports_and_renders_md(self):
        code, out = self._import(regression_payload())
        self.assertEqual(code, 0, out)
        self.assertTrue((self.p.paths.regression / "final-report.json").exists())
        md = (self.p.paths.regression / "final-report.md").read_text(encoding="utf-8")
        self.assertIn("结论：**passed**", md)
        self.assertIn("unit_test", md)

    def test_status_mismatch_rejected(self):
        payload = regression_payload(status="passed")
        payload["checks"][0]["status"] = "failed"
        payload["checks"][0]["exit_code"] = 1
        code, out = self._import(payload)
        self.assertEqual(code, 2)
        self.assertIn("不一致", out)

    def test_missing_required_check_rejected(self):
        payload = regression_payload()
        payload["checks"] = [c for c in payload["checks"] if c["check_id"] != "core_flow"]
        code, out = self._import(payload)
        self.assertEqual(code, 2)
        self.assertIn("core_flow", out)

    def test_passed_with_nonzero_exit_rejected(self):
        payload = regression_payload()
        payload["checks"][1]["exit_code"] = 1
        code, out = self._import(payload)
        self.assertEqual(code, 2)
        self.assertIn("exit_code", out)

    def test_not_run_without_reason_rejected(self):
        payload = regression_payload(status="not_run")
        payload["checks"][2]["status"] = "not_run"
        payload["checks"][2]["command"] = None
        payload["checks"][2]["exit_code"] = None
        code, out = self._import(payload)
        self.assertEqual(code, 2)
        self.assertIn("没有写原因", out)

    def test_failed_report_can_be_archived(self):
        payload = regression_payload(status="failed")
        payload["checks"][0]["status"] = "failed"
        payload["checks"][0]["exit_code"] = 1
        payload["checks"][0]["notes"] = "构建失败"
        code, out = self._import(payload)
        self.assertEqual(code, 1)
        self.assertIn("结论不是 passed", out)
        self.assertTrue((self.p.paths.regression / "final-report.json").exists())

    def test_previous_report_kept_in_history(self):
        """导入新报告不能覆盖旧结论，旧版本要进 history/。"""
        code, out = self._import(regression_payload(status="failed", overrides=self._fail_build))
        self.assertEqual(code, 1, out)
        code, out = self._import(regression_payload())
        self.assertEqual(code, 0, out)
        self.assertIn("已归档", out)
        hist = list((self.p.paths.regression / "history").glob("final-report.*"))
        self.assertTrue(hist, "上一轮报告应该被归档")
        archived = [p for p in hist if p.suffix == ".json"]
        self.assertTrue(archived)
        self.assertEqual(
            core.load_json(archived[0])["status"], "failed",
            "归档的应该是上一轮那份 failed 报告",
        )

    @staticmethod
    def _fail_build(data):
        data["checks"][0].update({"status": "failed", "exit_code": 1, "notes": "构建失败"})

    # ---- P0-01：必需检查项不能由报告自己降级 ----

    def _without(self, data, check_id):
        data["checks"] = [c for c in data["checks"] if c["check_id"] != check_id]

    def test_required_false_downgrade_rejected(self):
        payload = regression_payload()
        for item in payload["checks"]:
            if item["check_id"] == "build":
                item["required"] = False
        code, out = self._import(payload)
        self.assertEqual(code, 2, out)
        self.assertIn("不能降级", out)

    def test_unit_test_required_false_rejected(self):
        payload = regression_payload()
        for item in payload["checks"]:
            if item["check_id"] == "unit_test":
                item["required"] = False
        code, out = self._import(payload)
        self.assertEqual(code, 2, out)
        self.assertIn("unit_test", out)

    def test_missing_required_check_rejected_again(self):
        payload = regression_payload()
        self._without(payload, "build")
        code, out = self._import(payload)
        self.assertEqual(code, 2, out)
        self.assertIn("缺少必需检查项 build", out)

    def test_duplicate_check_id_rejected(self):
        payload = regression_payload()
        payload["checks"].append(dict(payload["checks"][0]))
        code, out = self._import(payload)
        self.assertEqual(code, 2, out)
        self.assertIn("重复", out)

    def test_unknown_required_check_rejected(self):
        payload = regression_payload()
        payload["checks"].append({
            "check_id": "security_scan", "name": "安全扫描", "required": True,
            "status": "passed", "command": "scan", "exit_code": 0,
            "executed_at": "2026-11-25T15:00:00+08:00", "evidence": "ok", "notes": "",
        })
        code, out = self._import(payload)
        self.assertEqual(code, 2, out)
        self.assertIn("不在质量配置", out)

    def test_required_skipped_cannot_pass(self):
        """必需项 skipped 的报告可以留档，但不能进交付。"""
        payload = regression_payload(status="not_run")
        for item in payload["checks"]:
            if item["check_id"] == "integration":
                item.update({"status": "skipped", "command": None, "exit_code": None,
                             "evidence": None, "notes": "环境不满足"})
        code, out = self._import(payload)
        self.assertEqual(code, 1, out)          # 留档可以，但不能宣称通过
        self.assertIn("not_run", out)
        code, out = self.p.run("release")
        self.assertEqual(code, 1, out)
        self.assertIn("回归", out)

    def test_optional_failed_does_not_block(self):
        """可选检查项失败默认只记录，不影响整体结论（配置可改）。"""
        payload = regression_payload()
        payload["checks"].append({
            "check_id": "permission", "name": "权限", "required": False,
            "status": "failed", "command": "pytest tests/permission", "exit_code": 1,
            "executed_at": "2026-11-25T15:00:00+08:00", "evidence": "1 failed", "notes": "本期无鉴权",
        })
        code, out = self._import(payload)
        self.assertEqual(code, 0, out)


class ExternalReportTest(unittest.TestCase):
    """P1-3：外部报告必须走同一条校验链。"""

    def setUp(self):
        self.p = Project()
        self.p.import_plan([node("DF-001")])
        self.p.activate()
        self.p.run("start", "DF-001")
        self.plan = self.p.plan()
        self.aid = self.plan["nodes"][0]["current_attempt_id"]

    def tearDown(self):
        self.p.close()

    def _external_dir(self, attempt_id=None):
        d = Path(tempfile.mkdtemp())
        ev = {
            "node_id": "DF-001",
            "attempt_id": attempt_id or self.aid,
            "plan_version": 1,
            "requirement_version": 1,
            "result": "success",
            "modified_files": ["a.py"],
            "acceptance": [{"criterion": "命令退出码为 0", "met": True, "evidence": "test_x"}],
        }
        (d / "execution-report.md").write_text(
            "# 报告\n\n```devflow-execution\n" + json.dumps(ev, ensure_ascii=False) + "\n```\n",
            encoding="utf-8",
        )
        (d / "test-report.json").write_text(json.dumps({
            "node_id": "DF-001",
            "attempt_id": attempt_id or self.aid,
            "plan_version": 1,
            "requirement_version": 1,
            "status": "passed",
            "executed_at": "2026-11-25T15:00:00+08:00",
            "commands": [{"cmd": "python -m unittest", "exit_code": 0}],
            "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0},
            "cases": [{"name": "c", "result": "pass"}],
        }, ensure_ascii=False), encoding="utf-8")
        return d

    def test_import_valid_external_report(self):
        d = self._external_dir()
        code, out = self.p.run("sync", "DF-001", "--external", "--from", str(d))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.p.node()["status"], "needs_review")

    def test_reject_wrong_attempt(self):
        d = self._external_dir(attempt_id="wrong-attempt")
        code, out = self.p.run("sync", "DF-001", "--external", "--from", str(d))
        self.assertEqual(code, 2)
        self.assertIn("attempt_id", out)
        self.assertEqual(self.p.node()["status"], "in_progress")

    def test_external_still_needs_start(self):
        p = Project()
        try:
            p.import_plan([node("DF-002")])
            p.activate()
            code, out = p.run("sync", "DF-002", "--external", "--from", str(self._external_dir()))
            self.assertEqual(code, 2)
            self.assertIn("只有 in_progress", out)
        finally:
            p.close()


class GitVerificationTest(unittest.TestCase):
    """P1-4：执行前已有的未提交修改不能算到节点头上。"""

    def _git(self, p, *args):
        return subprocess.run(
            ["git", "-C", str(p.root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def test_preexisting_changes_are_excluded(self):
        p = Project()
        try:
            self._git(p, "init", "-q")
            self._git(p, "config", "user.name", "t")
            self._git(p, "config", "user.email", "t@t")
            (p.root / "a.py").write_text("v1", encoding="utf-8")
            self._git(p, "add", "a.py")
            self._git(p, "commit", "-q", "-m", "base")

            # 执行前就存在的未提交修改
            (p.root / "pre_existing.py").write_text("dirty", encoding="utf-8")

            p.import_plan([node("DF-001", files_scope=["a.py"])])
            p.activate()
            p.run("start", "DF-001")

            (p.root / "a.py").write_text("v2", encoding="utf-8")
            (p.root / "pre_existing.py").write_text("dirty2", encoding="utf-8")

            p.write_evidence(evidence={"modified_files": ["a.py"]})
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 0, out)
            item = p.node()
            self.assertIn("a.py", item["git_actual_changes"])
            self.assertNotIn("pre_existing.py", item["git_actual_changes"])
            self.assertEqual(item["git_discrepancies"], [])
        finally:
            p.close()

    def test_reported_but_unchanged_is_flagged(self):
        p = Project()
        try:
            self._git(p, "init", "-q")
            self._git(p, "config", "user.name", "t")
            self._git(p, "config", "user.email", "t@t")
            (p.root / "a.py").write_text("v1", encoding="utf-8")
            self._git(p, "add", "a.py")
            self._git(p, "commit", "-q", "-m", "base")

            p.import_plan([node("DF-001", files_scope=["a.py", "b.py"])])
            p.activate()
            p.run("start", "DF-001")
            (p.root / "a.py").write_text("v2", encoding="utf-8")

            p.write_evidence(evidence={"modified_files": ["a.py", "b.py"]})
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 0, out)
            joined = " ".join(p.node()["git_discrepancies"])
            self.assertIn("b.py", joined)
            self.assertIn("未检测到改动", joined)
        finally:
            p.close()


class GitScopeTest(unittest.TestCase):
    """P0-02：Git 实际变更必须一起过 files_scope。"""

    def _git(self, p, *args):
        return subprocess.run(
            ["git", "-C", str(p.root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def _project(self, scope=("a.py",), profile=None):
        p = Project()
        self._git(p, "init", "-q")
        self._git(p, "config", "user.name", "t")
        self._git(p, "config", "user.email", "t@t")
        (p.root / "a.py").write_text("v1", encoding="utf-8")
        self._git(p, "add", "a.py")
        self._git(p, "commit", "-q", "-m", "base")
        if profile:
            data = core.load_json(p.paths.project)
            data["quality_profile"] = profile
            core.atomic_write_json(p.paths.project, data)
        p.import_plan([node("DF-001", files_scope=list(scope))])
        p.activate()
        p.run("start", "DF-001")
        return p

    def test_unreported_out_of_scope_file_blocks(self):
        p = self._project()
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            (p.root / "b.py").write_text("new", encoding="utf-8")
            p.write_evidence(evidence={"modified_files": ["a.py"]})
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 1, out)
            item = p.node()
            self.assertTrue(any("b.py" in v for v in item["scope_violations"]), item["scope_violations"])
            self.assertTrue(any("b.py" in d for d in item["git_discrepancies"]))
            code, out = p.run("accept", "DF-001")
            self.assertEqual(code, 1, out)
            self.assertIn("越界", out)
        finally:
            p.close()

    def test_reported_but_unchanged_kept_as_discrepancy(self):
        p = self._project(scope=("a.py", "c.py"))
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            p.write_evidence(evidence={"modified_files": ["a.py", "c.py"]})
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 0, out)
            item = p.node()
            self.assertEqual(item["scope_violations"], [])
            self.assertTrue(any("c.py" in d for d in item["git_discrepancies"]))
        finally:
            p.close()

    def test_missing_modified_files_rejected(self):
        p = self._project()
        try:
            p.write_evidence(evidence={"modified_files": None})
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 1, out)
            self.assertIn("缺少 modified_files", out)
            self.assertEqual(p.node()["status"], "failed")
        finally:
            p.close()

    def test_git_required_blocks_when_unavailable(self):
        p = Project()  # 不是 git 仓库
        try:
            data = core.load_json(p.paths.project)
            data["quality_profile"] = "production"
            core.atomic_write_json(p.paths.project, data)
            p.import_plan([node("DF-001")])
            p.activate()
            p.run("start", "DF-001")
            p.write_evidence()
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 1, out)
            self.assertIn("Git 变更检测不可用", out)
            self.assertTrue(p.node().get("git_unavailable"))
        finally:
            p.close()

    def test_ack_changes_cannot_bypass_scope(self):
        p = self._project()
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            (p.root / "b.py").write_text("new", encoding="utf-8")
            p.write_evidence(evidence={"modified_files": ["a.py"]})
            p.run("sync", "DF-001")
            code, out = p.run("accept", "DF-001", "--ack-changes", "--reason", "只是确认差异")
            self.assertEqual(code, 1, out)
            self.assertIn("越界", out)
        finally:
            p.close()

    def test_allow_scope_requires_reason(self):
        p = self._project()
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            (p.root / "b.py").write_text("new", encoding="utf-8")
            p.write_evidence(evidence={"modified_files": ["a.py"]})
            p.run("sync", "DF-001")
            code, out = p.run("accept", "DF-001", "--allow-scope")
            self.assertEqual(code, 1, out)
            self.assertIn("越界", out)
            code, out = p.run("accept", "DF-001", "--allow-scope", "--reason", "必须改的共享模块")
            self.assertEqual(code, 0, out)
            self.assertEqual(p.node()["status"], "completed")
            self.assertEqual(len(p.node()["scope_waivers"]), 1)
        finally:
            p.close()


class ParallelSafetyTest(unittest.TestCase):
    """P1-6：同组节点有间接依赖或资源冲突就不能激活。"""

    def _import(self, nodes, **extra):
        p = Project()
        code, out = p.import_plan(nodes, **extra)
        return p, code, out
    def test_indirect_dependency_rejected(self):
        p, code, out = self._import([
            node("DF-001", parallelizable=True, parallel_group="A"),
            node("DF-002", dependencies=["DF-001"]),
            node("DF-003", dependencies=["DF-002"], parallelizable=True, parallel_group="A"),
        ])
        try:
            self.assertEqual(code, 2)
            self.assertIn("间接依赖", out)
        finally:
            p.close()

    def test_scope_overlap_rejected(self):
        p, code, out = self._import([
            node("DF-001", files_scope=["src/service"], parallelizable=True, parallel_group="A"),
            node("DF-002", files_scope=["src/service/api.py"], parallelizable=True, parallel_group="A"),
        ])
        try:
            self.assertEqual(code, 2)
            self.assertIn("文件范围重叠", out)
        finally:
            p.close()

    def test_shared_db_object_rejected(self):
        p, code, out = self._import([
            node("DF-001", db_objects=["accounts"], parallelizable=True, parallel_group="A"),
            node("DF-002", db_objects=["accounts"], parallelizable=True, parallel_group="A"),
        ])
        try:
            self.assertEqual(code, 2)
            self.assertIn("数据库对象", out)
        finally:
            p.close()

    def test_exception_allows_with_record(self):
        p, code, out = self._import(
            [
                node("DF-001", files_scope=["src/service"], parallelizable=True, parallel_group="A"),
                node("DF-002", files_scope=["src/service/api.py"], parallelizable=True, parallel_group="A"),
            ],
            parallel_exceptions=[{
                "group": "A", "nodes": ["DF-001", "DF-002"],
                "reason": "两块代码互不影响，已评审", "approved_by": "架构师",
            }],
        )
        try:
            self.assertEqual(code, 0, out)
            self.assertIn("已记录例外", out)
        finally:
            p.close()


class PromptBindingTest(unittest.TestCase):
    """P1-7：计划变了，旧提示词不能继续用。"""

    def test_stale_prompt_blocks_activation_then_fixed(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=1)
            p.activate(1)

            code, out = p.import_plan([node("DF-001", status="pending")], version=2)
            self.assertEqual(code, 0, out)
            code, out = p.run("plan-activate", "--version", "2")
            self.assertEqual(code, 1)
            self.assertIn("提示词过期", out)

            code, out = p.run("prompt-init", "DF-001", "--plan", "2", "--force")
            self.assertEqual(code, 0, out)
            code, out = p.run("plan-activate", "--version", "2")
            self.assertEqual(code, 0, out)
            code, out = p.run("start", "DF-001")
            self.assertEqual(code, 0, out)
            self.assertIn("attempt_id", out)
        finally:
            p.close()

    def test_prompt_meta_contains_binding(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=1)
            p.run("prompt-init", "DF-001")
            text = (p.root / ".devflow/nodes/DF-001/prompt.md").read_text(encoding="utf-8")
            meta = core.parse_prompt_meta(text)
            self.assertEqual(meta["node_id"], "DF-001")
            self.assertEqual(meta["plan_version"], "1")
            self.assertEqual(meta["project_id"], "t")
            self.assertEqual(meta["prompt_version"], "1")
        finally:
            p.close()

    def test_regenerate_keeps_old_prompt(self):
        """--force 覆盖必须把旧提示词按版本号归档，不能直接丢弃。"""
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=1)
            p.run("prompt-init", "DF-001")
            code, out = p.run("prompt-init", "DF-001", "--force")
            self.assertEqual(code, 0, out)
            hist = p.paths.node_dir("DF-001") / "history"
            keep = sorted(hist.glob("prompt.v*.md"))
            self.assertEqual(len(keep), 1, f"应保留 1 份旧提示词，实际 {keep}")
            self.assertIn("v1", keep[0].name)
            meta = core.parse_prompt_meta(
                (p.root / ".devflow/nodes/DF-001/prompt.md").read_text(encoding="utf-8")
            )
            self.assertEqual(meta["prompt_version"], "2")
        finally:
            p.close()


class MigrationTest(unittest.TestCase):
    """P1-5：变更后的节点迁移必须合理。"""

    def _plan(self, version, nodes):
        return {"plan_version": version, "nodes": nodes}

    def test_missing_node(self):
        old = self._plan(1, [node("DF-001", status="completed"), node("DF-002", status="completed")])
        new = self._plan(2, [node("DF-001", status="completed")])
        problems = core.migration_problems(old, new, "CR-001")
        self.assertTrue(any("DF-002" in p and "消失" in p for p in problems))

    def test_completed_reworked_without_remark(self):
        old = self._plan(1, [node("DF-001", status="completed")])
        new = self._plan(2, [node("DF-001", status="ready")])
        problems = core.migration_problems(old, new, "CR-001")
        self.assertTrue(any("返工" in p for p in problems))

    def test_completed_reworked_with_remark_passes(self):
        old = self._plan(1, [node("DF-001", status="completed")])
        new = self._plan(2, [node("DF-001", status="ready", remark="CR-001 触发返工")])
        self.assertEqual(core.migration_problems(old, new, "CR-001"), [])

    def test_new_completed_node_rejected(self):
        old = self._plan(1, [node("DF-001", status="completed")])
        new = self._plan(2, [node("DF-001", status="completed"), node("DF-002", status="completed")])
        problems = core.migration_problems(old, new, "CR-001")
        self.assertTrue(any("DF-002" in p for p in problems))


class DependencyCheckParallelTest(unittest.TestCase):
    """P1-6：dependency_check 必须和 validate 给出同一个结论。"""

    def _plan(self, nodes, **extra):
        plan = {"plan_version": 1, "requirement_version": 1, "status": "draft", "nodes": nodes}
        plan.update(extra)
        return plan

    def test_scope_conflict_is_problem(self):
        import dependency_check

        plan = self._plan([
            node("DF-001", files_scope=["src/service"], parallelizable=True, parallel_group="A"),
            node("DF-002", files_scope=["src/service/api.py"], parallelizable=True, parallel_group="A"),
        ])
        result = dependency_check.analyse(plan)
        self.assertTrue(any("文件范围重叠" in p for p in result["problems"]))
        self.assertEqual(result["parallel_groups"], {"A": ["DF-001", "DF-002"]})

    def test_indirect_dependency_is_problem(self):
        import dependency_check

        plan = self._plan([
            node("DF-001", parallelizable=True, parallel_group="A"),
            node("DF-002", dependencies=["DF-001"]),
            node("DF-003", dependencies=["DF-002"], parallelizable=True, parallel_group="A"),
        ])
        result = dependency_check.analyse(plan)
        self.assertTrue(any("间接依赖" in p for p in result["problems"]))

    def test_waived_conflict_becomes_warning(self):
        import dependency_check

        plan = self._plan(
            [
                node("DF-001", db_objects=["accounts"], parallelizable=True, parallel_group="A"),
                node("DF-002", db_objects=["accounts"], parallelizable=True, parallel_group="A"),
            ],
            parallel_exceptions=[{
                "group": "A", "nodes": ["DF-001", "DF-002"],
                "reason": "只读字段，已和 DBA 确认", "approved_by": "架构师",
            }],
        )
        result = dependency_check.analyse(plan)
        self.assertEqual([p for p in result["problems"] if "数据库对象" in p], [])
        self.assertTrue(any("已记录例外" in w for w in result["warnings"]))

    def test_graph_md_lists_parallel_conflict(self):
        import dependency_check

        plan = self._plan([
            node("DF-001", files_scope=["src/a.py"], parallelizable=True, parallel_group="A"),
            node("DF-002", files_scope=["src/a.py"], parallelizable=True, parallel_group="A"),
        ])
        result = dependency_check.analyse(plan)
        md = dependency_check.render_graph(plan, result)
        self.assertIn("## 并行组", md)
        self.assertIn("仅作并行候选", md)
        self.assertIn("文件范围重叠", md)

    def test_doctor_catches_conflict_added_by_hand(self):
        """绕过 plan-import 手改 backlog，doctor 也要能发现并行冲突。"""
        p = Project()
        try:
            p.import_plan([node("DF-001"), node("DF-002")])
            p.activate()
            plan = p.plan()
            for item in plan["nodes"]:
                item.update({"parallelizable": True, "parallel_group": "A", "files_scope": ["a.py"]})
            plan["status"] = "active"
            core.atomic_write_json(p.paths.backlog(1), plan)

            code, out = p.run("doctor")
            self.assertEqual(code, 1)
            self.assertIn("并行组 A", out)
        finally:
            p.close()


class ChangeBaseVersionTest(unittest.TestCase):
    """P1-5：基准版本过期或目标计划来路不明，必须拒绝应用。"""

    def _prepare(self):
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
            based_on={"change_id": "CR-001", "plan_version": 1},
        )
        p.run("prompt-init", "--all", "--plan", "2")
        code, out = p.run("change-new", "--title", "加一个节点", "--type", "add")
        assert code == 0, out
        self.cr_path = p.paths.change_dir("CR-001") / "change-request.json"
        cr = core.load_json(self.cr_path)
        cr.update({
            "status": "confirmed",
            "base_plan_version": 1,
            "base_requirement_version": 1,
            "target_plan_version": 2,
            "target_requirement_version": 1,
            "impacts": [{"node_id": "DF-002", "action": "add", "reason": "新增"}],
        })
        core.atomic_write_json(self.cr_path, cr)
        return p

    def _patch_cr(self, **fields):
        cr = core.load_json(self.cr_path)
        cr.update(fields)
        core.atomic_write_json(self.cr_path, cr)

    def test_stale_base_plan_rejected(self):
        p = self._prepare()
        try:
            # 计划已经推进到 v3，CR 还停在 v1
            p.import_plan([node("DF-001", status="completed"), node("DF-002")], version=3)
            plan = p.plan(3)
            plan["status"] = "active"
            core.atomic_write_json(p.paths.backlog(3), plan)
            project = core.load_json(p.paths.project)
            project["current_plan_version"] = 3
            core.atomic_write_json(p.paths.project, project)

            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2)
            self.assertIn("基准已过期", out)
            self.assertEqual(core.load_json(self.cr_path)["status"], "confirmed")
            self.assertEqual(p.plan(1)["status"], "active")
        finally:
            p.close()

    def test_stale_base_requirement_rejected(self):
        p = self._prepare()
        try:
            self._patch_cr(base_requirement_version=0)
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2)
            self.assertIn("基准已过期", out)
        finally:
            p.close()

    def test_plan_not_created_by_change_rejected(self):
        p = self._prepare()
        try:
            plan = p.plan(2)
            plan["based_on"] = {"change_id": "CR-999", "plan_version": 1}
            core.atomic_write_json(p.paths.backlog(2), plan)
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2)
            self.assertIn("CR-999", out)
        finally:
            p.close()

    def test_target_requirement_version_mismatch_rejected(self):
        p = self._prepare()
        try:
            self._patch_cr(target_requirement_version=2)
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2)
            self.assertIn("需求", out)
        finally:
            p.close()

    def test_unconfirmed_change_rejected(self):
        p = self._prepare()
        try:
            self._patch_cr(status="draft")
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 2)
            self.assertIn("confirmed", out)
        finally:
            p.close()


class AcceptedTerminalTest(unittest.TestCase):
    """P1-5 延伸：accepted 是终态，不能靠重跑命令悄悄回退。"""

    def _accepted_project(self):
        p = Project()
        p.import_plan([node("DF-001")])
        p.activate()
        code, out = complete_node(p)
        assert code == 0, out
        code, out = p.run("regression-check")
        assert code == 0, out
        path = Path(tempfile.mkdtemp()) / "reg.json"
        path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
        code, out = p.run("regression-import", "--file", str(path))
        assert code == 0, out
        code, out = p.run("release")
        assert code == 0, out
        code, out = p.run("release-ready")
        assert code == 0, out
        code, out = p.run("release-accept", "--by", "tester")
        assert code == 0, out
        return p

    def test_accepted_blocks_further_work(self):
        p = self._accepted_project()
        try:
            self.assertEqual(core.load_json(p.paths.project)["status"], "accepted")
            for argv in (
                ("regression-check",),
                ("start", "DF-001"),
                ("retry", "DF-001"),
                ("release",),
                ("release-ready",),
            ):
                code, out = p.run(*argv)
                self.assertEqual(code, 2, f"{argv} 应该被拒绝：{out}")
                self.assertIn("accepted", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "accepted")
        finally:
            p.close()

    def test_status_cannot_regress_silently(self):
        """已验收项目跑 regression-check 不能把状态改回 regression。"""
        p = self._accepted_project()
        try:
            p.run("regression-check")
            self.assertEqual(core.load_json(p.paths.project)["status"], "accepted")
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
