from __future__ import annotations

import copy
import json
import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).parents[1] / 'tools/validate_generate_contract.py'
spec = importlib.util.spec_from_file_location('generate_contract_v050', path)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class OptionParsingTests(unittest.TestCase):
    def test_active_imported_options_are_resolved_without_retired_constants(self):
        source = '''export const MINIMAX_H3_MAX_MODEL_VALUE = 'MiniMax-H3-Max'
export const AGNES_VIDEO_25_MODEL = 'agnes-video-2.5'
export const AGNES_VIDEO_FLASH_MODEL = 'agnes-video-2.5-flash'
export const AGNES_IMAGE_MODEL = 'agnes-image-2.5-flash' '''
        constants = contract.typescript_constants(source)
        errors = []
        ids = contract.option_model_ids("{value: MINIMAX_H3_MAX_MODEL_VALUE}, {value: AGNES_VIDEO_25_MODEL}, {value:'seedance-2-0'}", constants, errors)
        self.assertEqual(ids, {'MiniMax-H3-Max', 'agnes-video-2.5', 'seedance-2-0'})
        self.assertEqual(contract.option_model_ids('{value: AGNES_IMAGE_MODEL}', constants, errors), {'agnes-image-2.5-flash'})
        self.assertEqual(errors, [])
        self.assertNotIn('agnes-video-2.5-flash', ids)

    def test_unresolved_option_fails_instead_of_silently_disappearing(self):
        errors = []
        self.assertEqual(contract.option_model_ids('{value: NEW_MODEL}', {}, errors), set())
        self.assertEqual(errors, ['Generate contract drift: unresolved option constant: NEW_MODEL'])

    def test_new_version_cannot_reuse_old_real_human_waiver(self):
        path = Path(__file__).parents[1] / 'tools/verify_release_acceptance.py'
        spec = importlib.util.spec_from_file_location('release_acceptance_v050', path)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        for status in ('pending', 'previously-accepted'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                verifier.validate({'version':'v0.5.0','realHumanCoreFlow':{'status':status,'retestWaived':True,'operator':'test','confirmedAt':'2026-10-09T00:00:00Z','notes':'old waiver'}}, 'v0.5.0')


class SnapshotDriftTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((Path(__file__).parents[1] / "holycrab/references/capabilities.json").read_text())

    def test_current_snapshot_matches_verified_constraints(self):
        self.assertEqual(contract.validate_new_model_snapshot(self.manifest), [])

    def test_reference_limit_and_cross_field_rule_drift_fail_closed(self):
        mutations = (
            lambda m: next(r for r in m["imageModels"] if r["id"] == "agnes-image-2.5-flash")["referenceLimits"].update(images=15),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "agnes-video-2.5")["rules"].update(size1KRequiresSquareRatio=False),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "agnes-video-2.5")["rules"].update(framesAndReferencesMutuallyExclusive=False),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "agnes-video-2.5")["referenceLimits"].update(audio=4),
            lambda m: m["requestSchemas"]["agnesImage"]["properties"].update(outputFormat={"type": "string"}),
            lambda m: m["requestSchemas"]["agnesVideo"]["properties"]["seconds"].update(type="number"),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "MiniMax-H3-Max")["rules"].update(audioOnlyReference=True),
        )
        for mutate in mutations:
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            with self.subTest(mutate=mutate):
                self.assertTrue(contract.validate_new_model_snapshot(manifest))


class CentralizedRuleDriftTests(unittest.TestCase):
    RULES = '''
    int maxImages = SEEDANCE_25.equals(model) ? 30 : wan(model) ? 10
        : "agnes-image-2.5-flash".equals(model) ? 14 : agnes(model) ? 8 : 9;
    int maxVideos = SEEDANCE_25.equals(model) ? 10 : wan(model) ? 5
        : "agnes-image-2.5-flash".equals(model) ? 0 : agnes(model) ? 1 : 3;
    int maxAudios = SEEDANCE_25.equals(model) ? 10 : wan(model) ? 5
        : "agnes-image-2.5-flash".equals(model) ? 0 : 3;
    if ((first || last) && references) throw combination("fixture", "exclusive");
    range(errors, null, "imageAssetIds", "image_reference", "count", count(images), 0, maxImages, true, true);
    range(errors, null, "videoAssetIds", "video_reference", "count", count(videos), 0, maxVideos, true, true);
    range(errors, null, "audioAssetIds", "audio_reference", "count", count(audios), 0, maxAudios, true, true);
    if (SEEDANCE_25.equals(model)) range(errors, null, "references", null, "count", count(images)+count(videos)+count(audios), 0, 50, true, true);
    '''

    def test_equivalent_central_rules_are_verified(self):
        errors = []
        contract.validate_central_asset_rules(self.RULES, errors)
        self.assertEqual(errors, [])

    def test_changed_or_unused_limits_and_exclusivity_still_fail_closed(self):
        for before, after in (('? 30', '? 31'), ('? 14', '? 15'), ('? 8', '? 9'),
                              ('? 1 : 3', '? 2 : 3'), ('? 0 : 3', '? 0 : 4'),
                              ('0, 50,', '0, 51,'), ('maxImages, true', 'unusedImages, true'),
                              ('&& references', '&& other')):
            errors = []
            contract.validate_central_asset_rules(self.RULES.replace(before, after), errors)
            with self.subTest(before=before): self.assertTrue(errors)
