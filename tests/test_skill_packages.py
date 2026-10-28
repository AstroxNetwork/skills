import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.build_skills import build, ROOT
from tests.test_cli import holycrab
from holycrab_runtime import lifecycle, settings, workflows


class SkillPackageTests(unittest.TestCase):
    def test_bundles_are_deterministic_complete_and_match_source(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "first", Path(directory) / "second"
            first.mkdir(); second.mkdir()
            manifest = build(first); other = build(second)
            self.assertEqual(manifest, other)
            self.assertEqual(len(manifest["packages"]), 6)
            for row in manifest["packages"]:
                path = first / Path(row["download"]).name
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"])
                with zipfile.ZipFile(path) as archive:
                    prefix = row["name"] + "/"
                    self.assertTrue(all(name.startswith(prefix) and ".." not in Path(name).parts for name in archive.namelist()))
                    self.assertFalse(any("/scripts/" in name for name in archive.namelist()))
                    package = json.loads(archive.read(prefix + "package.json"))
                    for name, digest in package["files"].items():
                        self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), digest)
                        if name.startswith("references/"):
                            self.assertEqual(archive.read(prefix + name), (ROOT / "holycrab" / name).read_bytes())
                    skill = archive.read(prefix + "SKILL.md").decode()
                    for link in re.findall(r"\]\((references/[^)]+)\)", skill):
                        self.assertIn(prefix + link, archive.namelist())
                    self.assertEqual(package["minCliVersion"], "0.7.0")

    def test_customized_resource_blocks_upgrade_without_changing_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)):
            root = Path(directory) / ".agents/skills/holycrab/references/creative"
            root.mkdir(parents=True)
            file = root / "video.md"; file.write_text("my custom guide")
            with self.assertRaisesRegex(ValueError, "Customized Skill file kept"):
                lifecycle.check_skill_conflicts({}, "codex", ROOT / "runtime-files.json")
            self.assertEqual(file.read_text(), "my custom guide")
            previous = {"coreFiles": [{"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}]}
            lifecycle.check_skill_conflicts(previous, "codex", ROOT / "runtime-files.json")

    def test_purge_removes_known_workflow_records_and_keeps_media(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(holycrab._owner("config_dir"), "config_dir", return_value=Path(directory)):
            path = Path(directory) / "film.mp4"; path.write_bytes(b"user video")
            result = workflows.prepare({"title": "local", "steps": [{"id": "edit", "kind": "compose", "request": {}}]})
            self.assertTrue(workflows.path_for(result["workflowId"]).exists())
            lifecycle.purge_known_local_state()
            self.assertFalse(workflows.path_for(result["workflowId"]).exists())
            self.assertEqual(path.read_bytes(), b"user video")


if __name__ == "__main__": unittest.main()
