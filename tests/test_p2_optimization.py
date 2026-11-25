"""P2 优化项测试。

覆盖：交付物指纹与下游上下文变更检测、self-test 集成验收。
"""

import json
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dfcore as core  # noqa: E402
import df  # noqa: E402
from test_p0_hardening import Project, node, quiet  # noqa: E402


def complete(p, node_id="DF-001"):
    p.run("start", node_id)
    p.write_evidence(node_id, evidence={"modified_files": ["a.py"]})
    p.run("sync", node_id)
    return p.run("accept", node_id)


class DeliverableContextTest(unittest.TestCase):
    """P2-2：依赖产物变了，下游不能装作没发生。"""

    def _project(self, inputs=None):
        p = Project()
        (p.root / "a.py").write_text("v1", encoding="utf-8")
        p.import_plan([
            node("DF-001", outputs=["a.py"], files_scope=["a.py"]),
            node("DF-002", dependencies=["DF-001"], inputs=inputs if inputs is not None else ["a.py"],
                 files_scope=["b.py"], outputs=["b.py"]),
        ])
        p.activate()
        code, out = complete(p, "DF-001")
        assert code == 0, out
        return p

    def test_accept_records_deliverables(self):
        p = self._project()
        try:
            files = p.node("DF-001")["deliverables"]["files"]
            self.assertIn("a.py", files)
            self.assertEqual(files["a.py"]["sha256"], core.file_fingerprint(p.root / "a.py")["sha256"])
        finally:
            p.close()

    def test_changed_dependency_blocks_start(self):
        p = self._project()
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            code, out = p.run("start", "DF-002")
            self.assertEqual(code, 2, out)
            self.assertIn("被修改过", out)
            self.assertIn(p.node("DF-002")["status"], ("pending", "ready"))
        finally:
            p.close()

    def test_ack_allows_start_with_record(self):
        p = self._project()
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            code, out = p.run("start", "DF-002", "--ack-context", "--reason", "只改了注释")
            self.assertEqual(code, 0, out)
            acks = p.node("DF-002")["context_acks"]
            self.assertEqual(len(acks), 1)
            self.assertIn("只改了注释", acks[0]["reason"])
            self.assertEqual(p.node("DF-002")["status"], "in_progress")
        finally:
            p.close()

    def test_ack_requires_reason(self):
        p = self._project()
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            code, out = p.run("start", "DF-002", "--ack-context")
            self.assertEqual(code, 2)
            self.assertIn("--reason", out)
        finally:
            p.close()

    def test_undeclared_dependency_does_not_block(self):
        """没有声明 inputs 就不猜影响范围，避免文档改一行全体返工。"""
        p = self._project(inputs=[])
        try:
            (p.root / "a.py").write_text("v2", encoding="utf-8")
            code, out = p.run("start", "DF-002")
            self.assertEqual(code, 0, out)
        finally:
            p.close()

    def test_unrelated_file_change_does_not_block(self):
        p = self._project()
        try:
            (p.root / "README.md").write_text("doc", encoding="utf-8")
            code, out = p.run("start", "DF-002")
            self.assertEqual(code, 0, out)
        finally:
            p.close()


class SelfTestCommandTest(unittest.TestCase):
    """P2-3：self-test 在临时目录跑完整生命周期，不碰真实项目。"""

    def test_self_test_runs_end_to_end(self):
        code, out = quiet(df.main, ["self-test", "--json"])
        self.assertEqual(code, 0, out)
        payload = json.loads(out[out.index("{"):])
        self.assertEqual(payload["status"], "passed", out)
        steps = {s["step"]: s for s in payload["steps"]}
        for name in (
            "init", "req-import", "req-confirm", "plan-import", "validate",
            "graph", "activate", "start", "sync", "accept", "change-new",
            "plan-import-v2", "change-apply", "regression-check",
            "regression-import", "release", "release-ready", "release-accept",
        ):
            self.assertIn(name, steps, f"缺少步骤 {name}")
            self.assertEqual(steps[name]["status"], "passed", f"{name}: {steps[name]}")
        self.assertEqual(payload["final_status"], "accepted")

    def test_self_test_leaves_no_residue(self):
        before = set(SKILL_DIR.glob(".devflow*"))
        code, out = quiet(df.main, ["self-test"])
        self.assertEqual(code, 0, out)
        self.assertEqual(set(SKILL_DIR.glob(".devflow*")), before)


if __name__ == "__main__":
    unittest.main()
