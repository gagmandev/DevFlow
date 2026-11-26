"""文档与代码一致性检查。

SKILL.md / docs 是给 Agent 读的指令。它们一旦和代码脱节，Agent 就会按错规则干活。
这里把"能机器核对的部分"固定下来：命令清单、必需检查项、状态机、Schema 数量。
"""

import json
import re
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import dfcore as core  # noqa: E402


def read(rel: str) -> str:
    return (SKILL_DIR / rel).read_text(encoding="utf-8")


def config(name: str) -> dict:
    return core.load_config_file(SKILL_DIR / "config" / f"{name}.yaml")


class CommandDocsTest(unittest.TestCase):
    def test_every_cli_command_is_documented(self):
        source = read("scripts/df.py")
        commands = set(re.findall(r'add\("([a-z][a-z-]+)",', source))
        corpus = read("SKILL.md") + read("docs/usage.md")
        missing = sorted(c for c in commands if c not in corpus)
        self.assertEqual(missing, [], f"这些命令没写进文档：{missing}")

    def test_readme_points_at_self_test(self):
        self.assertIn("self-test", read("README.md"))


class SchemaDocsTest(unittest.TestCase):
    def test_schema_count_in_architecture_doc(self):
        count = len(list((SKILL_DIR / "schemas").glob("*.json")))
        text = read("docs/architecture.md")
        self.assertIn(f"{count} 个 JSON Schema", text, "架构文档里的 Schema 数量与目录不一致")

    def test_template_count_in_readme(self):
        count = len(list((SKILL_DIR / "templates").iterdir()))
        self.assertIn(f"{count} 个文档模板", read("README.md"))

    def test_required_checks_exist_in_template(self):
        required = config("quality-gates")["gates"]["regression"]["required_checks"]
        template = json.loads(read("templates/regression-report.json"))
        ids = {c["check_id"] for c in template["checks"]}
        missing = [c for c in required if c not in ids]
        self.assertEqual(missing, [], f"模板缺必需检查项：{missing}")

    def test_node_statuses_documented(self):
        text = read("docs/data-structure.md")
        missing = [s for s in core.NODE_STATUSES if s not in text]
        self.assertEqual(missing, [], f"节点状态没写进数据结构文档：{missing}")

    def test_regression_schema_matches_derived_statuses(self):
        """Schema 允许的整体状态必须与实际推导结果一致，不能留 partial 这种永远推不出来的值。"""
        import df

        schema = json.loads(read("schemas/regression-report.schema.json"))
        allowed = set(schema["properties"]["status"]["enum"])
        possible = {df.derive_regression_status([
            {"required": True, "status": s},
        ]) for s in ("passed", "failed", "not_run", "skipped")}
        self.assertTrue(possible <= allowed, f"推导值 {possible} 不在 Schema 枚举 {allowed} 里")


class WorkflowDocTest(unittest.TestCase):
    def test_release_three_stage_documented(self):
        text = read("SKILL.md") + read("references/release-rules.md")
        for word in ("release_candidate", "release_ready", "accepted"):
            self.assertIn(word, text)

    def test_accepted_terminal_documented(self):
        text = read("SKILL.md")
        self.assertIn("accepted` 是终态", text)

    def test_external_import_documented(self):
        self.assertIn("--external", read("SKILL.md"))

    def test_accepted_allowlist_documented(self):
        text = read("SKILL.md")
        self.assertIn("白名单", text)
        for cmd in ("change-new", "change-set", "change-apply"):
            self.assertIn(cmd, text)
        self.assertIn("唯一出口", text)

    def test_accept_live_git_documented(self):
        text = read("SKILL.md") + read("references/release-rules.md")
        self.assertIn("实时 Git", text)

    def test_active_requirement_acceptance_documented(self):
        text = read("SKILL.md") + read("references/testing-rules.md")
        self.assertIn("active", text)
        self.assertIn("验收标准", text)

    def test_ci_pins_python38_runner(self):
        text = read(".github/workflows/ci.yml")
        self.assertIn("ubuntu-22.04", text, "Python 3.8 必须固定到 ubuntu-22.04")
        self.assertIn('python: "3.8"', text)
        self.assertIn('python: "3.12"', text)
        self.assertNotIn("continue-on-error", text)


if __name__ == "__main__":
    unittest.main()
