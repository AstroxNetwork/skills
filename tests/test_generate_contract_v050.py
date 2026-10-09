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
            lambda m: m["requestSchemas"]["agnesImage"]["properties"]["imageAssetIds"].update(maxItems=15),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "agnes-video-2.5")["rules"].update(size1KRequiresSquareRatio=False),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "agnes-video-2.5")["rules"].update(framesAndReferencesMutuallyExclusive=False),
            lambda m: m["requestSchemas"]["agnesVideo"]["properties"]["audioAssetIds"].update(maxItems=4),
            lambda m: m["requestSchemas"]["agnesImage"]["properties"].update(outputFormat={"type": "string"}),
            lambda m: m["requestSchemas"]["agnesVideo"]["properties"]["seconds"].update(type="number"),
            lambda m: next(r for r in m["videoModels"] if r["id"] == "MiniMax-H3-Max")["rules"].update(audioOnlyReference=True),
        )
        for mutate in mutations:
            manifest = copy.deepcopy(self.manifest)
            mutate(manifest)
            with self.subTest(mutate=mutate):
                self.assertTrue(contract.validate_new_model_snapshot(manifest))
