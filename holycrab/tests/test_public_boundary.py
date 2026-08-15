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
    REPO_ROOT / "holycrab" / "references" / "api.md",
    REPO_ROOT / "holycrab" / "references" / "capabilities.json",
    REPO_ROOT / "holycrab" / "scripts" / "holycrab_cli.py",
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


if __name__ == "__main__":
    unittest.main()
