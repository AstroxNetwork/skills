"""Independent backend source changes must remain visible to the contract gate."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.material_contract import SOURCE_FILES, SERVER, validate_material_contract, validate_audited_material_pair
from unittest.mock import patch
import hashlib

ROOT = Path(__file__).resolve().parents[1]


class MaterialDriftTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in SOURCE_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        self.manifest = json.loads((ROOT / "holycrab/references/capabilities.json").read_text())

    def errors(self):
        return validate_material_contract(self.root, ROOT, self.manifest)

    def test_backend_duration_boundary_mutation_is_detected(self):
        path = self.root / (SERVER + "service/MulerouterGenerationRequestValidator.java")
        path.write_text("request.getDuration() < 2 || request.getDuration() > 30")
        self.assertNotIn("Material contract drift: Berry duration", self.errors())
        path.write_text("request.getDuration() < 2 || request.getDuration() > 31")
        self.assertIn("Material contract drift: Berry duration", self.errors())

    def test_backend_missing_error_detail_field_is_detected(self):
        path = self.root / (SERVER + "api/AssetValidationError.java")
        path.write_text("record Error(Object actual, String requestPath, String field) {}")
        self.assertNotIn("Material contract drift: error field actual", self.errors())
        path.write_text("record Error(String requestPath, String field) {}")
        self.assertIn("Material contract drift: error field actual", self.errors())

    def test_local_material_limit_reintroduction_is_detected(self):
        self.manifest["requestSchemas"]["imageGeneration"]["properties"]["imageAssetIds"]["maxItems"] = 10
        self.assertTrue(any("local material limit" in item for item in self.errors()))

    def test_removed_audio_id_support_and_old_inline_sources_are_detected(self):
        fields = self.manifest["requestSchemas"]["audioGeneration"]["properties"]["references"]["items"]["properties"]
        for change in (lambda: fields.pop("assetId"), lambda: fields.update(audio_data={"type": "string"})):
            original = copy.deepcopy(fields)
            change()
            self.assertIn("Material contract drift: CLI audio reference shapes", self.errors())
            fields.clear()
            fields.update(original)


class AuditedPairTests(unittest.TestCase):
    def test_every_rule_source_change_requires_reaudit(self):
        # The bound source rejects all changed facts, including values not parsed by a regex.
        cases = (("sizeBytes", "20 MB", "21 MB"), ("width", "240", "241"),
                 ("formats", "png,jpg", "png,jpg,gif"), ("frameRate", "23.976", "24"),
                 ("videoCodec", "h264,hevc", "h264"))
        manifest = json.loads((ROOT / "holycrab/references/capabilities.json").read_text())
        for field, before, after in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                name = "material-rule.txt"
                (root / name).write_text(before)
                expected = hashlib.sha256((root / name).read_bytes()).hexdigest()
                with patch("tools.material_contract.AUDITED_RULE_SOURCES", {name: expected}):
                    self.assertEqual(validate_audited_material_pair(root, manifest), [])
                    (root / name).write_text(after)
                    self.assertTrue(any("rule source changed" in e for e in validate_audited_material_pair(root, manifest)))

    def test_any_cli_material_description_change_requires_reaudit(self):
        with tempfile.TemporaryDirectory() as temporary, patch("tools.material_contract.AUDITED_RULE_SOURCES", {}):
            root = Path(temporary)
            manifest = json.loads((ROOT / "holycrab/references/capabilities.json").read_text())
            self.assertEqual(validate_audited_material_pair(root, manifest), [])
            manifest["videoModels"][0]["materialRules"]["image"]["width"]["min"] += 1
            self.assertIn("Material contract drift: audited CLI material descriptions changed", validate_audited_material_pair(root, manifest))
