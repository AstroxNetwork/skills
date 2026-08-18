from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).parents[1]
REPO_ROOT = SKILL_ROOT.parent
MANIFEST_PATH = SKILL_ROOT / "references" / "capabilities.json"
VALIDATOR_PATH = SKILL_ROOT / "scripts" / "validate_capabilities.py"


class CapabilityManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_records_verified_platform_revision(self) -> None:
        self.assertEqual(self.manifest["schemaVersion"], 1)
        self.assertEqual(self.manifest["source"]["name"], "HolyCrab Public Capability Registry")
        self.assertEqual(self.manifest["source"]["version"], "2026-08-14")
        self.assertEqual(self.manifest["source"]["publishedAt"], "2026-08-14")

    def test_lists_every_user_facing_video_model(self) -> None:
        models = {item["id"]: item for item in self.manifest["videoModels"]}
        self.assertEqual(
            set(models),
            {
                "seedance-2-0",
                "seedance-2-0-fast",
                "seedance-2-0-mini",
                "dreamina-seedance-2-5-260628",
                "MiniMax-H3",
            },
        )

        seedance_25 = models["dreamina-seedance-2-5-260628"]
        self.assertEqual(seedance_25["endpoints"]["create"], "/api/tasks/generation")
        self.assertEqual(seedance_25["resolutions"], ["480p", "720p"])
        self.assertEqual(seedance_25["durationSeconds"], {"min": 4, "max": 30})
        self.assertEqual(seedance_25["taskTypes"], ["reference", "frames", "edit", "extend"])
        self.assertEqual(seedance_25["referenceLimits"]["total"], 50)
        for model_id in (
            "seedance-2-0",
            "seedance-2-0-fast",
            "seedance-2-0-mini",
            "dreamina-seedance-2-5-260628",
        ):
            self.assertIs(models[model_id]["generateAudioSupported"], True)
            self.assertIs(models[model_id]["generateAudioDefault"], True)

        h3 = models["MiniMax-H3"]
        self.assertEqual(h3["endpoints"]["create"], "/api/tasks/minimax-generation")
        self.assertEqual(h3["endpoints"]["freezeCredit"], "/api/tasks/minimax-generation/freeze-credit")
        self.assertEqual(h3["resolutions"], ["768P", "2K"])
        self.assertEqual(h3["durationSeconds"], {"min": 4, "max": 15})
        self.assertIs(h3["rules"]["generateAudioSupported"], False)

    def test_lists_current_image_and_audio_models(self) -> None:
        self.assertEqual(
            {item["id"] for item in self.manifest["imageModels"]},
            {"seedream-5-0-pro-260628", "seedream-5-0-lite-260128", "seedream-4-5-251128"},
        )
        self.assertEqual(
            {item["id"] for item in self.manifest["audioModels"]},
            {"seed-audio-1.0"},
        )


class CapabilityConsistencyTests(unittest.TestCase):
    def test_repository_docs_match_manifest(self) -> None:
        spec = importlib.util.spec_from_file_location("validate_capabilities", VALIDATOR_PATH)
        assert spec and spec.loader
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        self.assertEqual(validator.validate_repository(REPO_ROOT), [])


if __name__ == "__main__":
    unittest.main()
