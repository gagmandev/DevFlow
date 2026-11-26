"""第一批核心不变量测试：D-01 (INV-008)、D-06 (INV-015)、INV-029/005、init --force。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import df  # noqa: E402
import dfcore as core  # noqa: E402
from test_p0_hardening import Project, node, quiet  # noqa: E402
from test_p1_workflow import complete_node, regression_payload  # noqa: E402


def _make_trace_file(root: Path, requirements: list[dict], version: int = 1) -> Path:
    path = root / f"trace-input-v{version}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "requirement_version": version,
                "requirements": requirements,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_valid_req_doc(p: Project, version: int = 1) -> None:
    p.paths.requirements.mkdir(parents=True, exist_ok=True)
    p.paths.confirmed_requirements(version).write_text(
        "# 需求确认说明书\n\n## 功能需求\n\n- REQ-001 实现核心命令并确保退出码为 0。\n\n"
        "## 业务规则\n\n- 严格校验状态机与交付门禁。\n\n## 验收标准\n\n- [ ] 命令退出码为 0\n\n"
        + ("补充说明文本，确保需求确认说明书正文长度满足最低阈值要求。" * 10),
        encoding="utf-8",
    )


def _confirm_cr(p: Project, cr_id: str = "CR-001", base_plan: int = 1, target_plan: int = 2,
                base_req: int = 1, target_req: int = 1) -> None:
    code, out = p.run("change-new", "--title", "变更测试", "--type", "add")
    assert code == 0, out
    cr_path = p.paths.change_dir(cr_id) / "change-request.json"
    cr = core.load_json(cr_path)
    cr.update({
        "status": "confirmed",
        "base_plan_version": base_plan,
        "base_requirement_version": base_req,
        "target_plan_version": target_plan,
        "target_requirement_version": target_req,
        "impacts": [{"node_id": "DF-002", "action": "add", "reason": "新增"}],
    })
    core.atomic_write_json(cr_path, cr)


class TestInv008PlanActivationUniqueEntry(unittest.TestCase):
    """D-01 / INV-008：v1 用 plan-activate，v2+ 只能走 change-apply。"""

    def test_inv_008_v1_plan_activate_and_idempotent(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=1)
            code, out = p.activate(1)
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["current_plan_version"], 1)
            self.assertEqual(core.load_json(p.paths.project)["status"], "planned")

            # 幂等重跑 v1 允许
            code, out = p.run("plan-activate", "--version", "1")
            self.assertEqual(code, 0, out)
            self.assertIn("已经是生效计划", out)
        finally:
            p.close()

    def test_inv_008_v2_plan_activate_rejected_even_with_based_on(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=1)
            p.activate(1)
            code, out = complete_node(p, "DF-001")
            self.assertEqual(code, 0, out)

            _confirm_cr(p)
            p.import_plan(
                [node("DF-001", status="completed"), node("DF-002", dependencies=["DF-001"])],
                version=2,
                based_on={"change_id": "CR-001", "plan_version": 1},
            )
            p.run("prompt-init", "--all", "--plan", "2")

            # 即使带了合法 based_on，plan-activate 对 v2+ 一律拒绝且指向 change-apply
            code, out = p.run("plan-activate", "--version", "2")
            self.assertEqual(code, 2, out)
            self.assertIn("change-apply", out)
            self.assertEqual(p.plan(2)["status"], "draft")
            self.assertEqual(core.load_json(p.paths.project)["current_plan_version"], 1)

            # 唯一合法通道 change-apply 成功
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 0, out)
            self.assertEqual(p.plan(2)["status"], "active")
            self.assertEqual(core.load_json(p.paths.project)["current_plan_version"], 2)
        finally:
            p.close()

    def test_inv_008_v2_rejected_even_when_no_active_plan(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=2)
            p.run("prompt-init", "--all", "--plan", "2")
            code, out = p.run("plan-activate", "--version", "2")
            self.assertEqual(code, 2, out)
            self.assertIn("change-apply", out)
            self.assertIsNone(core.load_json(p.paths.project)["current_plan_version"])
        finally:
            p.close()

    def test_inv_008_v1_reactivate_blocked_when_v2_active(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")], version=1)
            p.activate(1)
            complete_node(p, "DF-001")
            _confirm_cr(p)
            p.import_plan(
                [node("DF-001", status="completed"), node("DF-002", dependencies=["DF-001"])],
                version=2,
                based_on={"change_id": "CR-001", "plan_version": 1},
            )
            p.run("prompt-init", "--all", "--plan", "2")
            self.assertEqual(p.run("change-apply", "--change", "CR-001")[0], 0)

            # 即使把 v1 手动改回 draft，也不允许再通过 plan-activate 切回 v1
            v1 = p.plan(1)
            v1["status"] = "draft"
            core.atomic_write_json(p.paths.backlog(1), v1)
            code, out = p.run("plan-activate", "--version", "1")
            self.assertEqual(code, 2, out)
            self.assertIn("change-apply", out)
            self.assertEqual(core.load_json(p.paths.project)["current_plan_version"], 2)
        finally:
            p.close()


class TestInv015ProjectStateMachineAndRelease(unittest.TestCase):
    """D-06 + INV-015：项目状态迁移表与 release 失败不改状态。"""

    def _ready_for_release(self) -> Project:
        p = Project()
        p.import_plan([node("DF-001")])
        p.activate()
        code, out = complete_node(p)
        assert code == 0, out
        assert p.run("regression-check")[0] == 0
        return p

    def test_inv_015_release_keeps_status_on_blockers(self):
        p = self._ready_for_release()
        try:
            # 缺少回归报告时 release 失败，项目状态必须保持 regression
            code, out = p.run("release")
            self.assertEqual(code, 1, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "regression")
            meta = core.load_json(p.paths.releases / "release-report.json")
            self.assertEqual(meta["status"], "blocked")
            self.assertTrue(meta["blockers"])
        finally:
            p.close()

    def test_inv_015_release_promotes_only_when_zero_blockers(self):
        p = self._ready_for_release()
        try:
            reg_path = Path(tempfile.mkdtemp()) / "reg.json"
            reg_path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(p.run("regression-import", "--file", str(reg_path))[0], 0)

            code, out = p.run("release")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")
            meta = core.load_json(p.paths.releases / "release-report.json")
            self.assertEqual(meta["status"], "release_candidate")
            self.assertEqual(meta["blockers"], [])

            # 幂等重跑 release
            code, out = p.run("release")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")
        finally:
            p.close()

    def test_inv_015_req_confirm_cannot_rollback_planned_or_later(self):
        p = Project()
        try:
            _write_valid_req_doc(p, 1)
            trace = _make_trace_file(p.root, [{
                "req_id": "REQ-001", "title": "需求", "category": "functional",
                "status": "active", "acceptance": ["命令退出码为 0"],
            }])
            self.assertEqual(p.run("req-import", "--file", str(trace), "--version", "1")[0], 0)
            code, out = p.run("req-confirm", "--version", "1")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "requirements_confirmed")

            p.import_plan([node("DF-001", requirement_ids=["REQ-001"])])
            self.assertEqual(p.activate()[0], 0)
            self.assertEqual(core.load_json(p.paths.project)["status"], "planned")

            # planned 下禁止 req-confirm 回退到 requirements_confirmed
            code, out = p.run("req-confirm", "--version", "1")
            self.assertEqual(code, 2, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "planned")
        finally:
            p.close()

    def test_inv_015_start_blocked_in_regression(self):
        p = self._ready_for_release()
        try:
            # 模拟在 regression 阶段强行插入 ready 节点：start 必须拒绝
            plan = p.plan(1)
            plan["nodes"].append(node("DF-002", status="ready"))
            core.atomic_write_json(p.paths.backlog(1), plan)
            p.run("prompt-init", "DF-002")

            code, out = p.run("start", "DF-002")
            self.assertEqual(code, 2, out)
            self.assertIn("regression", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "regression")
        finally:
            p.close()

    def test_inv_015_transition_project_table_rejects_illegal_jumps(self):
        p = Project()
        try:
            proj = core.Project(p.root)
            for illegal in (
                ("initialized", "release_candidate"),
                ("initialized", "accepted"),
                ("planned", "requirements_confirmed"),
                ("planned", "release_ready"),
                ("in_progress", "accepted"),
                ("regression", "requirements_confirmed"),
                ("accepted", "release_ready"),
            ):
                proj.data["status"] = illegal[0]
                with self.assertRaises(core.DevFlowError):
                    df.transition_project(proj, illegal[1])
        finally:
            p.close()

    def test_inv_015_blocked_release_report_cannot_pass_release_ready(self):
        """D-06：release_candidate 下出现 blocked 报告后，必须重跑 release 才能 release-ready。"""
        p = self._ready_for_release()
        try:
            reg_path = Path(tempfile.mkdtemp()) / "reg.json"
            reg_path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(p.run("regression-import", "--file", str(reg_path))[0], 0)
            self.assertEqual(p.run("release")[0], 0)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")

            # 破坏节点测试证据并再次运行 release -> 报告变为 blocked，项目状态保持 release_candidate
            test_report_path = p.paths.node_dir("DF-001") / "test-report.json"
            original_report = test_report_path.read_text(encoding="utf-8")
            bad_report = json.loads(original_report)
            bad_report["status"] = "failed"
            test_report_path.write_text(json.dumps(bad_report, ensure_ascii=False), encoding="utf-8")

            code, out = p.run("release")
            self.assertEqual(code, 1, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")
            meta = core.load_json(p.paths.releases / "release-report.json")
            self.assertEqual(meta["status"], "blocked")
            self.assertTrue(meta["blockers"])

            # 修复证据但不重跑 release，直接 release-ready 必须被拒绝
            test_report_path.write_text(original_report, encoding="utf-8")
            code, out = p.run("release-ready")
            self.assertEqual(code, 1, out)
            self.assertIn("release", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")

            # 重新执行 release 生成无阻塞报告后，release-ready 通过
            code, out = p.run("release")
            self.assertEqual(code, 0, out)
            meta = core.load_json(p.paths.releases / "release-report.json")
            self.assertEqual(meta["status"], "release_candidate")
            self.assertEqual(meta["blockers"], [])

            code, out = p.run("release-ready")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()

    def test_inv_015_release_ready_and_accept_gate_sources(self):
        p = self._ready_for_release()
        try:
            # regression 阶段未跑 release 不能直接 release-ready / release-accept
            code, out = p.run("release-ready")
            self.assertEqual(code, 2, out)
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 2, out)

            reg_path = Path(tempfile.mkdtemp()) / "reg.json"
            reg_path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(p.run("regression-import", "--file", str(reg_path))[0], 0)
            self.assertEqual(p.run("release")[0], 0)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")

            # release_candidate 不能直接 release-accept
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 2, out)

            # release_candidate 允许重跑 regression-check 回到 regression
            self.assertEqual(p.run("regression-check")[0], 0)
            self.assertEqual(core.load_json(p.paths.project)["status"], "regression")
        finally:
            p.close()


class TestInv029AndInv005RequirementsAndDelivery(unittest.TestCase):
    """INV-029 + INV-005：需求导入门槛与交付门禁防绕过（A6 防护）。"""

    def _ready_project_with_req(self) -> Project:
        p = Project()
        try:
            _write_valid_req_doc(p, 1)
            trace = _make_trace_file(p.root, [{
                "req_id": "REQ-001", "title": "需求一", "category": "functional",
                "status": "active", "acceptance": ["命令退出码为 0"],
            }])
            code, out = p.run("req-import", "--file", str(trace), "--version", "1")
            assert code == 0, out
            code, out = p.run("req-confirm", "--version", "1")
            assert code == 0, out
            trace.unlink()

            p.import_plan([node("DF-001", requirement_ids=["REQ-001"])])
            code, out = p.activate()
            assert code == 0, out
            code, out = p.run("start", "DF-001")
            assert code == 0, out
            p.write_evidence(evidence={
                "requirement_acceptance": [
                    {"req_id": "REQ-001", "criterion": "命令退出码为 0", "met": True, "evidence": "test_ok"},
                ],
            })
            code, out = p.run("sync", "DF-001")
            assert code == 0, out
            code, out = p.run("accept", "DF-001")
            assert code == 0, out
            code, out = p.run("regression-check")
            assert code == 0, out

            reg_path = Path(tempfile.mkdtemp()) / "reg.json"
            reg_path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            code, out = p.run("regression-import", "--file", str(reg_path))
            assert code == 0, out
            code, out = p.run("release")
            assert code == 0, out
            code, out = p.run("release-ready")
            assert code == 0, out
            assert core.load_json(p.paths.project)["status"] == "release_ready"
            return p
        except Exception:
            p.close()
            raise

    def test_inv_029_req_import_blocked_after_confirm_and_in_release_ready(self):
        p = self._ready_project_with_req()
        try:
            tamper = _make_trace_file(p.root, [{
                "req_id": "REQ-001", "title": "被移除", "category": "functional",
                "status": "removed", "reason": "偷删", "confirmed_by": "attacker",
            }])
            code, out = p.run("req-import", "--file", str(tamper), "--version", "1")
            self.assertEqual(code, 2, out)
            self.assertIn("release_ready", out)
        finally:
            p.close()

    def test_inv_029_req_import_blocked_right_after_req_confirm(self):
        p = Project()
        try:
            _write_valid_req_doc(p, 1)
            trace = _make_trace_file(p.root, [{
                "req_id": "REQ-001", "title": "需求一", "category": "functional",
                "status": "active", "acceptance": ["命令退出码为 0"],
            }])
            self.assertEqual(p.run("req-import", "--file", str(trace), "--version", "1")[0], 0)
            code, out = p.run("req-confirm", "--version", "1")
            self.assertEqual(code, 0, out)

            code, out = p.run("req-import", "--file", str(trace), "--version", "1")
            self.assertEqual(code, 2, out)
            self.assertIn("requirements_confirmed", out)
        finally:
            p.close()

    def test_inv_005_req_import_rejects_missing_retirement_credentials(self):
        p = Project()
        try:
            bad_removed = _make_trace_file(p.root, [{
                "req_id": "REQ-001", "title": "已删除", "category": "functional", "status": "removed",
            }])
            code, out = p.run("req-import", "--file", str(bad_removed), "--version", "1")
            self.assertEqual(code, 2, out)
            self.assertIn("reason / confirmed_by", out)

            bad_changed = _make_trace_file(p.root, [{
                "req_id": "REQ-001", "title": "已变更", "category": "functional",
                "status": "changed", "superseded_by": "REQ-999",
            }])
            code, out = p.run("req-import", "--file", str(bad_changed), "--version", "1")
            self.assertEqual(code, 2, out)
            self.assertIn("REQ-999", out)
        finally:
            p.close()

    def test_inv_029_tampered_retired_req_after_ready_blocks_accept(self):
        """A6 攻击：release_ready 后篡改 traceability.json 退役需求，必须被 release-accept 拦截。"""
        p = self._ready_project_with_req()
        try:
            core.atomic_write_json(p.paths.traceability, {
                "schema_version": "1.0",
                "requirement_version": 1,
                "requirements": [{
                    "req_id": "REQ-001", "title": "需求一", "category": "functional",
                    "status": "removed",
                }],
            })
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 1, out)
            self.assertIn("reason / confirmed_by", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()

    def test_inv_029_tampered_traceability_mismatch_vN_blocks_accept(self):
        """即使带了退役凭据，直接改 traceability.json 与 traceability-v1.json 不一致也必须拦截。"""
        p = self._ready_project_with_req()
        try:
            core.atomic_write_json(p.paths.traceability, {
                "schema_version": "1.0",
                "requirement_version": 1,
                "requirements": [{
                    "req_id": "REQ-001", "title": "需求一", "category": "functional",
                    "status": "removed", "reason": "事后删掉", "confirmed_by": "fake",
                }],
            })
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 1, out)
            self.assertIn("内容不一致", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()

    def test_inv_029_normal_project_with_baseline_digest_delivers(self):
        p = self._ready_project_with_req()
        try:
            proj = core.load_json(p.paths.project)
            self.assertIn("requirement_baseline", proj)
            self.assertTrue(proj["requirement_baseline"]["baseline_id"])
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "accepted")
        finally:
            p.close()

    def test_inv_029_modified_confirmed_doc_after_ready_blocks_accept(self):
        """修改需求确认 Markdown 内容（版本号不变），release-accept 必须拒绝。"""
        p = self._ready_project_with_req()
        try:
            doc = p.paths.confirmed_requirements(1)
            doc.write_text(doc.read_text(encoding="utf-8") + "\n悄悄新增一条未确认需求。\n", encoding="utf-8")
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 1, out)
            self.assertIn("digest", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()

    def test_inv_029_missing_traceability_files_block_accept(self):
        """缺失当前追踪表、版本化追踪表或两者同时缺失，均必须阻断交付。"""
        for mode in ("current_only", "versioned_only", "both"):
            p = self._ready_project_with_req()
            try:
                if mode in ("current_only", "both"):
                    p.paths.traceability.unlink()
                if mode in ("versioned_only", "both"):
                    (p.paths.requirements / "traceability-v1.json").unlink()
                code, out = p.run("release-accept", "--by", "tester")
                self.assertEqual(code, 1, f"{mode} 应阻断：{out}")
                self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
            finally:
                p.close()

    def test_inv_029_missing_confirmed_doc_blocks_accept(self):
        p = self._ready_project_with_req()
        try:
            p.paths.confirmed_requirements(1).unlink()
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 1, out)
            self.assertIn("缺少需求基准文件", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()

    def test_inv_029_plan_requirement_version_mismatch_blocks_release(self):
        p = self._ready_project_with_req()
        try:
            plan = p.plan(1)
            plan["requirement_version"] = 2
            core.atomic_write_json(p.paths.backlog(1), plan)
            code, out = p.run("release-accept", "--by", "tester")
            self.assertEqual(code, 1, out)
            self.assertIn("需求", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")
        finally:
            p.close()

    def test_inv_029_infrastructure_project_without_traceability_delivers(self):
        """没有需求追踪表的合法基础设施项目仍可正常交付。"""
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            code, out = complete_node(p)
            self.assertEqual(code, 0, out)
            self.assertEqual(p.run("regression-check")[0], 0)
            reg_path = Path(tempfile.mkdtemp()) / "reg.json"
            reg_path.write_text(json.dumps(regression_payload(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(p.run("regression-import", "--file", str(reg_path))[0], 0)
            self.assertEqual(p.run("release")[0], 0)
            self.assertEqual(p.run("release-ready")[0], 0)
            self.assertEqual(p.run("release-accept", "--by", "tester")[0], 0)
            self.assertEqual(core.load_json(p.paths.project)["status"], "accepted")
        finally:
            p.close()


class TestInitForcePersistence(unittest.TestCase):
    """init --force 修改项目档案后必须落盘，且不能覆写 accepted 项目。"""

    def test_init_force_persists_changes(self):
        p = Project()
        try:
            code, out = p.run("init", "--force", "--type", "existing", "--profile", "production")
            self.assertEqual(code, 0, out)
            data = core.load_json(p.paths.project)
            self.assertEqual(data["project_type"], "existing")
            self.assertEqual(data["quality_profile"], "production")
        finally:
            p.close()

    def test_init_force_rejected_on_accepted(self):
        p = Project()
        try:
            proj = core.load_json(p.paths.project)
            proj["status"] = "accepted"
            core.atomic_write_json(p.paths.project, proj)

            code, out = p.run("init", "--force", "--type", "existing")
            self.assertEqual(code, 2, out)
            self.assertIn("accepted", out)
            self.assertEqual(core.load_json(p.paths.project)["project_type"], "greenfield")
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
