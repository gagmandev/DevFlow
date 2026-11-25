"""P0 工程安全加固的验收测试。

覆盖：证据硬校验、files_scope 拦截、跨进程锁、变更事务、交付状态机。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import df  # noqa: E402
import dfcore as core  # noqa: E402

DF_SCRIPT = str(SCRIPTS / "df.py")


def quiet(func, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = func(argv)
    return code, out.getvalue() + err.getvalue()


def node(node_id="DF-001", **overrides):
    data = {
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
    data.update(overrides)
    return data


class Project:
    """临时 DevFlow 项目，直接走 CLI，保证和真实使用一致。"""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = core.Paths(self.root)
        code, out = quiet(df.main, ["init", "--project", str(self.root), "--name", "T", "--id", "t"])
        assert code == 0, out
        self.write_requirement_doc()

    def close(self):
        self._tmp.cleanup()

    def write_requirement_doc(self, version=1):
        self.paths.requirements.mkdir(parents=True, exist_ok=True)
        self.paths.confirmed_requirements(version).write_text(
            "# 需求确认说明书\n\n## 功能需求\n\n- REQ-001 做一件事\n\n"
            "## 业务规则\n\n- 无\n\n## 验收标准\n\n- [ ] 命令退出码为 0\n" + "内容。" * 40,
            encoding="utf-8",
        )

    def import_plan(self, nodes, version=1):
        path = self.paths.plans / f".import-{version}.json"
        self.paths.plans.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"requirement_version": 1, "nodes": nodes}, ensure_ascii=False),
            encoding="utf-8",
        )
        return quiet(df.main, [
            "plan-import", "--file", str(path), "--version", str(version),
            "--project", str(self.root),
        ])

    def activate(self, version=1):
        quiet(df.main, ["prompt-init", "--all", "--project", str(self.root)])
        return quiet(df.main, [
            "plan-activate", "--version", str(version), "--project", str(self.root),
        ])

    def run(self, *argv):
        return quiet(df.main, [*argv, "--project", str(self.root)])

    def plan(self, version=1):
        return core.load_json(self.paths.backlog(version))

    def node(self, node_id="DF-001", version=1):
        return next(n for n in self.plan(version)["nodes"] if n["node_id"] == node_id)

    def write_evidence(self, node_id="DF-001", evidence=None, report=None, version=1):
        plan = self.plan(version)
        item = next(n for n in plan["nodes"] if n["node_id"] == node_id)
        aid = item.get("current_attempt_id")
        base_evidence = {
            "node_id": node_id,
            "result": "success",
            "modified_files": ["a.py"],
            "acceptance": [
                {"criterion": c, "met": True, "evidence": "test_x"}
                for c in item.get("acceptance_criteria", [])
            ],
            "assumptions": [],
            "leftovers": [],
            "plan_version": plan["plan_version"],
            "requirement_version": plan["requirement_version"],
        }
        if aid:
            base_evidence["attempt_id"] = aid
        if evidence:
            base_evidence.update(evidence)
            if evidence.get("attempt_id") is None and "attempt_id" in evidence:
                base_evidence.pop("attempt_id", None)

        base_report = {
            "node_id": node_id,
            "status": "passed",
            "executed_at": "2026-11-25T10:00:00+08:00",
            "commands": [{"cmd": "python -m unittest", "exit_code": 0}],
            "summary": {"total": 2, "passed": 2, "failed": 0, "skipped": 0},
            "cases": [
                {"name": "case_1", "result": "pass"},
                {"name": "case_2", "result": "pass"},
            ],
            "plan_version": plan["plan_version"],
            "requirement_version": plan["requirement_version"],
        }
        if aid:
            base_report["attempt_id"] = aid
        if report:
            base_report.update(report)

        nd = self.paths.nodes / node_id
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "execution-report.md").write_text(
            "# 报告\n\n```devflow-execution\n"
            + json.dumps(base_evidence, ensure_ascii=False)
            + "\n```\n",
            encoding="utf-8",
        )
        (nd / "test-report.json").write_text(
            json.dumps(base_report, ensure_ascii=False), encoding="utf-8"
        )
        return base_evidence, base_report


class EvidenceStrictTest(unittest.TestCase):
    """P0-1：一条命令失败就不能算通过。"""

    def setUp(self):
        self.p = Project()
        self.p.import_plan([node()])
        self.p.activate()
        self.p.run("start", "DF-001")

    def tearDown(self):
        self.p.close()

    def problems(self):
        return core.evidence_problems(
            self.p.paths, self.p.node(), {"gates": {"node_accept": {}}}, self.p.plan()
        )

    def test_one_command_fails(self):
        self.p.write_evidence(report={"commands": [
            {"cmd": "build", "exit_code": 0},
            {"cmd": "test", "exit_code": 1},
        ]})
        self.assertTrue(any("退出码 1" in p for p in self.problems()))

    def test_all_commands_pass(self):
        self.p.write_evidence(report={"commands": [
            {"cmd": "build", "exit_code": 0},
            {"cmd": "test", "exit_code": 0},
        ]})
        self.assertEqual(self.problems(), [])

    def test_missing_exit_code(self):
        self.p.write_evidence(report={"commands": [{"cmd": "test"}]})
        self.assertTrue(any("没有明确的整数退出码" in p for p in self.problems()))

    def test_empty_commands(self):
        self.p.write_evidence(report={"commands": []})
        self.assertTrue(any("没有记录任何实际执行的命令" in p for p in self.problems()))

    def test_summary_inconsistent(self):
        self.p.write_evidence(report={
            "summary": {"total": 5, "passed": 3, "failed": 0, "skipped": 0},
            "cases": [{"name": "c", "result": "pass"}] * 5,
        })
        self.assertTrue(any("统计不一致" in p for p in self.problems()))

    def test_cases_mismatch(self):
        self.p.write_evidence(report={
            "summary": {"total": 2, "passed": 2, "failed": 0, "skipped": 0},
            "cases": [{"name": "c", "result": "pass"}],
        })
        self.assertTrue(any("cases 数量" in p for p in self.problems()))

    def test_passed_status_with_failed_case(self):
        self.p.write_evidence(report={
            "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0},
            "cases": [{"name": "c", "result": "fail"}],
        })
        joined = " ".join(self.problems())
        self.assertIn("failed=1", joined)

    def test_zero_total(self):
        self.p.write_evidence(report={
            "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
            "cases": [],
        })
        self.assertTrue(any("总数为 0" in p for p in self.problems()))

    def test_skipped_not_allowed(self):
        self.p.write_evidence(report={
            "summary": {"total": 2, "passed": 1, "failed": 0, "skipped": 1},
            "cases": [{"name": "a", "result": "pass"}, {"name": "b", "result": "skip"}],
        })
        self.assertTrue(any("被跳过" in p for p in self.problems()))

    def test_execution_result_failed(self):
        self.p.write_evidence(evidence={"result": "failed"})
        self.assertTrue(any("只有 success" in p for p in self.problems()))

    def test_acceptance_not_met(self):
        self.p.write_evidence(evidence={"acceptance": [
            {"criterion": "命令退出码为 0", "met": False, "evidence": "x"},
        ]})
        self.assertTrue(any("met 不为 true" in p for p in self.problems()))

    def test_acceptance_missing(self):
        self.p.write_evidence(evidence={"acceptance": []})
        self.assertTrue(any("未在报告中逐条对应" in p for p in self.problems()))

    def test_acceptance_without_evidence(self):
        self.p.write_evidence(evidence={"acceptance": [
            {"criterion": "命令退出码为 0", "met": True, "evidence": ""},
        ]})
        self.assertTrue(any("缺少证据" in p for p in self.problems()))

    def test_node_id_mismatch(self):
        self.p.write_evidence(report={"node_id": "DF-999"})
        self.assertTrue(any("node_id" in p for p in self.problems()))

    def test_old_attempt_rejected(self):
        self.p.write_evidence()
        # 手动推进一轮新的执行标识，模拟返工后旧报告被再次提交
        plan = self.p.plan()
        plan["nodes"][0]["current_attempt_id"] = "brand-new-attempt"
        core.atomic_write_json(self.p.paths.backlog(1), plan)
        self.assertTrue(any("attempt_id" in p for p in self.problems()))

    def test_attempt_missing(self):
        self.p.write_evidence(evidence={"attempt_id": None})
        self.assertTrue(any("缺少 attempt_id" in p for p in self.problems()))

    def test_plan_version_mismatch(self):
        self.p.write_evidence(report={"plan_version": 99})
        self.assertTrue(any("plan_version" in p for p in self.problems()))

    def test_sync_sets_failed_when_evidence_bad(self):
        self.p.write_evidence(report={"commands": [{"cmd": "test", "exit_code": 1}]})
        code, out = self.p.run("sync", "DF-001")
        self.assertEqual(code, 1)
        self.assertIn("退出码 1", out)
        self.assertEqual(self.p.node()["status"], "failed")


class ScopeTest(unittest.TestCase):
    """P0-2：越界修改必须拦截，路径不能靠前缀绕过。"""

    def test_exact_file(self):
        n = node(files_scope=["src/service.py"])
        self.assertEqual(core.scope_violations(n, ["src/service.py"], Path(".")), [])

    def test_directory_prefix_cannot_escape(self):
        n = node(files_scope=["src/service"])
        problems = core.scope_violations(n, ["src/service-backup/x.py"], Path("."))
        self.assertEqual(len(problems), 1)

    def test_directory_allows_children(self):
        n = node(files_scope=["src/service"])
        self.assertEqual(core.scope_violations(n, ["src/service/a/b.py"], Path(".")), [])

    def test_double_star_glob(self):
        n = node(files_scope=["src/**/*.py"])
        self.assertEqual(core.scope_violations(n, ["src/a/b/c.py"], Path(".")), [])
        self.assertEqual(len(core.scope_violations(n, ["src/a/b.txt"], Path("."))), 1)

    def test_single_star_does_not_cross_directory(self):
        n = node(files_scope=["src/*.py"])
        self.assertEqual(core.scope_violations(n, ["src/a.py"], Path(".")), [])
        self.assertEqual(len(core.scope_violations(n, ["src/a/b.py"], Path("."))), 1)

    def test_absolute_path_rejected(self):
        n = node(files_scope=["src"])
        self.assertTrue(core.scope_violations(n, ["/etc/passwd"], Path(".")))

    def test_parent_traversal_rejected(self):
        n = node(files_scope=["src"])
        self.assertTrue(core.scope_violations(n, ["src/../../etc/passwd"], Path(".")))

    def test_windows_separator_normalized(self):
        n = node(files_scope=["src/a.py"])
        self.assertEqual(core.scope_violations(n, ["src\\a.py"], Path(".")), [])

    def test_empty_scope_deny_policy(self):
        n = node(files_scope=[])
        self.assertTrue(core.scope_violations(n, ["a.py"], Path("."), empty_policy="deny"))
        self.assertEqual(core.scope_violations(n, ["a.py"], Path("."), empty_policy="allow"), [])

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "src").mkdir()
            target = Path(outside) / "secret.py"
            target.write_text("x", encoding="utf-8")
            link = root / "src" / "link.py"
            try:
                os.symlink(target, link)
            except (OSError, NotImplementedError):
                self.skipTest("当前环境不支持创建符号链接")
            n = node(files_scope=["src"])
            self.assertTrue(core.scope_violations(n, ["src/link.py"], root))

    def test_cli_blocks_accept_until_waived(self):
        p = Project()
        try:
            p.import_plan([node(files_scope=["a.py"])])
            p.activate()
            p.run("start", "DF-001")
            p.write_evidence(evidence={"modified_files": ["a.py", "b.py"]})
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 1)
            self.assertIn("越界", out)
            self.assertEqual(p.node()["status"], "needs_review")
            self.assertTrue(p.node()["scope_violations"])

            code, out = p.run("accept", "DF-001")
            self.assertEqual(code, 1)
            self.assertIn("越界", out)
            self.assertEqual(p.node()["status"], "needs_review")

            code, _ = p.run("accept", "DF-001", "--allow-scope", "--reason", "接口必须同步调整")
            self.assertEqual(code, 0)
            self.assertEqual(p.node()["status"], "completed")
            self.assertEqual(len(p.node()["scope_waivers"]), 1)
        finally:
            p.close()


class LockTest(unittest.TestCase):
    """P0-3：跨进程排他 + 进程内可重入 + 不误删锁。"""

    def setUp(self):
        # Windows 上刚释放的锁文件可能被杀毒/索引短暂占用，清理失败不算测试失败
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_holder_times_out(self):
        path = Path(self.tmp) / ".lock"
        first = core.ProjectLock(path, timeout=0.2)
        first.acquire()
        try:
            second = core.ProjectLock(path, timeout=0.2)
            with self.assertRaises(core.LockTimeout):
                second.acquire()
        finally:
            first.release()
        self.assertTrue(path.exists(), "锁文件不应被删除")

    def test_reentrant_in_same_process(self):
        with core.project_lock(Path(self.tmp), timeout=0.5):
            with core.project_lock(Path(self.tmp), timeout=0.5):
                pass

    def test_stale_lock_file_does_not_block(self):
        path = Path(self.tmp) / ".lock"
        path.write_text("garbage from a crashed process", encoding="utf-8")
        lock = core.ProjectLock(path, timeout=0.5)
        lock.acquire()
        lock.release()

    def test_token_written(self):
        path = Path(self.tmp) / ".lock"
        lock = core.ProjectLock(path, timeout=0.5)
        lock.acquire()
        data = lock.metadata()
        self.assertEqual(data["token"], lock.token)
        self.assertEqual(data["pid"], os.getpid())
        lock.release()

    def test_concurrent_start_no_lost_update(self):
        """多个进程同时 start 不同节点，不能互相覆盖。"""
        p = Project()
        try:
            nodes = [node(f"DF-{i:03d}", files_scope=[f"f{i}.py"]) for i in range(1, 7)]
            p.import_plan(nodes)
            p.activate()

            procs = [
                subprocess.Popen(
                    [sys.executable, DF_SCRIPT, "start", f"DF-{i:03d}", "--project", str(p.root)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                for i in range(1, 7)
            ]
            outputs = []
            for proc in procs:
                out, _ = proc.communicate(timeout=60)
                outputs.append((proc.returncode, out.decode("utf-8", "replace")))

            for code, out in outputs:
                self.assertEqual(code, 0, out)

            statuses = {n["node_id"]: n["status"] for n in p.plan()["nodes"]}
            for i in range(1, 7):
                self.assertEqual(
                    statuses[f"DF-{i:03d}"], "in_progress",
                    f"DF-{i:03d} 状态被覆盖：{statuses}",
                )
            self.assertEqual(len({n["current_attempt_id"] for n in p.plan()["nodes"]}), 6)
        finally:
            p.close()


class TransactionTest(unittest.TestCase):
    """P0-4：change-apply 中途失败必须回滚，不留半提交状态。"""

    def _prepare_change(self, p):
        p.import_plan([node("DF-001")], version=1)
        p.activate(1)
        p.run("start", "DF-001")
        p.write_evidence()
        p.run("sync", "DF-001")
        p.run("accept", "DF-001")

        p.import_plan([node("DF-001", status="completed"), node("DF-002", dependencies=["DF-001"])], version=2)
        code, out = p.run("change-new", "--title", "加一个节点", "--type", "add")
        self.assertEqual(code, 0, out)
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
        return cr_path

    def test_success_leaves_single_active_plan(self):
        p = Project()
        try:
            self._prepare_change(p)
            code, out = p.run("change-apply", "--change", "CR-001")
            self.assertEqual(code, 0, out)
            self.assertEqual(p.plan(1)["status"], "superseded")
            self.assertEqual(p.plan(2)["status"], "active")
            self.assertEqual(core.load_json(p.paths.project)["current_plan_version"], 2)
            self.assertEqual(core.pending_transactions(p.paths), [])
        finally:
            p.close()

    def test_failure_rolls_back(self):
        p = Project()
        try:
            cr_path = self._prepare_change(p)
            original = core.atomic_write_json

            def failing(path, data):
                if Path(path).name == "project.json":
                    raise OSError("磁盘写入失败（模拟）")
                return original(path, data)

            core.atomic_write_json = failing
            try:
                code, out = quiet(df.main, [
                    "change-apply", "--change", "CR-001", "--project", str(p.root)
                ])
            finally:
                core.atomic_write_json = original

            self.assertEqual(code, 2)
            self.assertIn("回滚", out)
            self.assertEqual(p.plan(1)["status"], "active")
            self.assertEqual(p.plan(2)["status"], "draft")
            self.assertEqual(core.load_json(p.paths.project)["current_plan_version"], 1)
            self.assertEqual(core.load_json(cr_path)["status"], "confirmed")

            pending = core.pending_transactions(p.paths)
            self.assertEqual(pending, [], "回滚后不应留下待处理事务")
            journals = list((p.paths.df / ".txn").glob("*/journal.json"))
            self.assertEqual(len(journals), 1)
            self.assertEqual(core.load_json(journals[0])["status"], "rolled_back")
        finally:
            p.close()

    def test_recover_pending_transaction(self):
        p = Project()
        try:
            self._prepare_change(p)
            txn = core.Transaction(
                p.paths, "test", [p.paths.project, p.paths.backlog(2)],
            )
            txn.begin()
            txn.phase("write_new_plan")
            p.paths.backlog(2).write_text("{broken", encoding="utf-8")

            code, out = p.run("recover")
            self.assertEqual(code, 1)
            self.assertIn("未完成事务", out)

            code, out = p.run("recover", "--apply")
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(p.paths.backlog(2).read_text(encoding="utf-8"))["status"], "draft")
            self.assertEqual(core.pending_transactions(p.paths), [])
        finally:
            p.close()


class ReleaseStateTest(unittest.TestCase):
    """P0-5：生成报告 ≠ 交付条件满足 ≠ 用户验收。"""

    def _completed_project(self):
        p = Project()
        p.import_plan([node("DF-001")])
        p.activate()
        p.run("start", "DF-001")
        p.write_evidence()
        p.run("sync", "DF-001")
        p.run("accept", "DF-001")
        return p

    def _write_regression(self, p, conclusion="passed"):
        p.paths.regression.mkdir(parents=True, exist_ok=True)
        (p.paths.regression / "final-report.md").write_text(
            "# 全量回归报告\n\n## 2. 执行项与结果\n\n| 项 | 结果 |\n| --- | --- |\n| 测试 | passed |\n\n"
            f"## 6. 回归结论\n\n结论：**{conclusion}**\n\n" + "说明。" * 40,
            encoding="utf-8",
        )

    def test_release_does_not_mean_accepted(self):
        p = self._completed_project()
        try:
            self._write_regression(p)
            code, out = p.run("release")
            self.assertEqual(code, 0, out)
            status = core.load_json(p.paths.project)["status"]
            self.assertEqual(status, "release_candidate")
            self.assertNotEqual(status, "accepted")
        finally:
            p.close()

    def test_release_ready_and_accept(self):
        p = self._completed_project()
        try:
            self._write_regression(p)
            self.assertEqual(p.run("release")[0], 0)
            code, out = p.run("release-ready")
            self.assertEqual(code, 0, out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_ready")

            code, out = p.run("release-accept")
            self.assertEqual(code, 2)
            self.assertIn("--by", out)

            code, out = p.run("release-accept", "--by", "验收人")
            self.assertEqual(code, 0, out)
            data = core.load_json(p.paths.project)
            self.assertEqual(data["status"], "accepted")
            self.assertEqual(data["accepted_by"], "验收人")
        finally:
            p.close()

    def test_failed_regression_blocks(self):
        p = self._completed_project()
        try:
            self._write_regression(p, conclusion="failed")
            code, out = p.run("release")
            self.assertEqual(code, 1)
            self.assertIn("回归结论是 failed", out)
            self.assertEqual(core.load_json(p.paths.project)["status"], "release_candidate")
            code, _ = p.run("release-ready")
            self.assertEqual(code, 1)
        finally:
            p.close()

    def test_missing_conclusion_blocks(self):
        p = self._completed_project()
        try:
            p.paths.regression.mkdir(parents=True, exist_ok=True)
            (p.paths.regression / "final-report.md").write_text("# 报告\n\n" + "内容。" * 100, encoding="utf-8")
            code, out = p.run("release")
            self.assertEqual(code, 1)
            self.assertIn("结论", out)
        finally:
            p.close()

    def test_unfinished_node_blocks(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            self._write_regression(p)
            code, out = p.run("release")
            self.assertEqual(code, 1)
            self.assertIn("尚未完成", out)
        finally:
            p.close()


class AttemptLifecycleTest(unittest.TestCase):
    def test_retry_generates_new_attempt(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            p.run("start", "DF-001")
            first = p.node()["current_attempt_id"]
            self.assertTrue(first)
            p.run("fail", "DF-001", "--reason", "测试挂了")
            p.run("retry", "DF-001")
            second = p.node()["current_attempt_id"]
            self.assertNotEqual(first, second)
            self.assertEqual(p.node()["attempt_number"], 2)
        finally:
            p.close()

    def test_sync_requires_in_progress(self):
        p = Project()
        try:
            p.import_plan([node("DF-001")])
            p.activate()
            p.write_evidence()
            code, out = p.run("sync", "DF-001")
            self.assertEqual(code, 2)
            self.assertIn("只有 in_progress", out)
        finally:
            p.close()


if __name__ == "__main__":
    unittest.main()
