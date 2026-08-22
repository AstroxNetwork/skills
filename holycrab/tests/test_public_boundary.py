from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parents[2]
PUBLIC_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "SECURITY.md",
    REPO_ROOT / "HolyCrab CLI 使用指南.md",
    REPO_ROOT / "install.sh",
    REPO_ROOT / "bin" / "holycrab",
    REPO_ROOT / "holycrab" / "SKILL.md",
    REPO_ROOT / "holycrab" / "agents" / "openai.yaml",
    REPO_ROOT / "holycrab" / "references" / "api.md",
    REPO_ROOT / "holycrab" / "references" / "capabilities.json",
    REPO_ROOT / "holycrab" / "scripts" / "holycrab_cli.py",
    REPO_ROOT / "holycrab" / "scripts" / "holycrab_api.py",
)


class PublicBoundaryTests(unittest.TestCase):
    def test_public_files_contain_no_internal_source_metadata(self) -> None:
        joined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_FILES)
        self.assertNotIn("sd2_generate", joined)
        self.assertNotIn("sourceFiles", joined)
        self.assertNotRegex(joined, r"/Users/[^/]+/")
        self.assertNotRegex(joined, r"\b[0-9a-f]{40}\b")

    def test_public_files_contain_no_secret_like_values(self) -> None:
        joined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_FILES)
        self.assertNotRegex(joined, r"(?i)(api[_-]?key|token)\s*[:=]\s*['\"][A-Za-z0-9_-]{20,}")
        self.assertNotRegex(joined, r"(?i)X-Amz-(Credential|Signature)=[^\s&]+")

    def test_public_experience_uses_key_configuration_not_login_commands(self) -> None:
        joined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_FILES)
        self.assertNotIn("holycrab auth login", joined)
        self.assertNotIn("holycrab auth logout", joined)
        self.assertNotIn('"loggedIn"', joined)

    def test_readme_distinguishes_test_safety_from_real_paid_generation(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("本仓库不会自动提交真实付费生成任务", readme)
        self.assertIn("测试和开发验证不会调用正式生成接口或产生费用", readme)
        self.assertIn("用户确认后会提交真实付费任务", readme)

    def test_public_docs_use_the_approved_privacy_wording_and_links(self) -> None:
        joined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_FILES)
        approved = "HolyCrab CLI 不额外收集遥测数据；服务使用遵循 HolyCrab 隐私政策和服务条款。"
        self.assertIn(approved, joined)
        self.assertIn("https://holycrab.ai/privacy/", joined)
        self.assertIn("https://holycrab.ai/terms/", joined)
        self.assertIn("cs@holycrab.ai", joined)
        self.assertNotIn("提示词和素材会上传", joined)

    def test_public_docs_describe_a_versioned_snapshot_not_live_capabilities(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        skill = (REPO_ROOT / "holycrab" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("2026-08-22", readme)
        self.assertIn("versioned capability snapshot", skill)
        self.assertNotIn("Treat the capability response as current", skill)

    def test_skill_explains_the_seed_audio_model_field_exception(self) -> None:
        skill = (REPO_ROOT / "holycrab" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("omit `model` from the Seed Audio request body", skill)


if __name__ == "__main__":
    unittest.main()
