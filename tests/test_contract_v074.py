"""Keep the branch overlay scoped and distinguish contract evidence from release approval."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_material_contract_evidence import ROOT, evidence_for
from tools.validate_generate_contract import SEEDANCE_4K_FILES, immutable_sources, source_paths, record_evidence
from tools.verify_release_acceptance import evidence_digest, validate, validate_candidate_contract


class Contract074Tests(unittest.TestCase):
    def setUp(self):
        proof = evidence_for()
        proof.update(mode='local-immutable-main-with-seedance-4k', overlays={
            'seedance4k': {'commit': 'c' * 40, 'sourceFiles': {name: 'd' * 64 for name in SEEDANCE_4K_FILES}}})
        proof['evidenceSha256'] = evidence_digest(proof)
        self.record = {'version': 'v0.7.4', 'candidateSha256': proof['candidateSha256'],
            'generateContractCheck': proof, 'realHumanCoreFlow': {'status': 'pending'}}

    def test_valid_double_source_proof_still_does_not_approve_release(self):
        validate(self.record, 'v0.7.4', root=ROOT, contract_only=True)
        with self.assertRaisesRegex(ValueError, 'real-person'):
            validate(self.record, 'v0.7.4', root=ROOT)

    def test_missing_overlay_or_unrelated_files_fail_even_after_rehash(self):
        for mutation in ('missing', 'extra', 'commit', 'hash', 'mode'):
            record = copy.deepcopy(self.record)
            proof = record['generateContractCheck']
            overlay = proof['overlays']['seedance4k']
            if mutation == 'missing': proof.pop('overlays')
            if mutation == 'extra': overlay['sourceFiles']['unrelated.java'] = 'a' * 64
            if mutation == 'commit': overlay['commit'] = 'main'
            if mutation == 'hash': overlay['sourceFiles'][SEEDANCE_4K_FILES[0]] = 'not-a-hash'
            if mutation == 'mode': proof['mode'] = 'local-immutable-main'
            proof['evidenceSha256'] = evidence_digest(proof)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_candidate_contract(record, ROOT)

    def test_wrong_remote_4k_cannot_write_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'acceptance.json'
            path.write_text(json.dumps({'version': 'v0.7.4', 'realHumanCoreFlow': {'status': 'pending'}}))
            before = path.read_bytes()
            proof = self.record['generateContractCheck']
            with self.assertRaisesRegex(ValueError, '4K'):
                record_evidence(path, ROOT, 'a' * 40, 'a' * 40, proof['sourceFiles'],
                    overlays=proof['overlays'], expected_4k='b' * 40)
            self.assertEqual(path.read_bytes(), before)
            record_evidence(path, ROOT, 'a' * 40, 'a' * 40, proof['sourceFiles'],
                overlays=proof['overlays'], expected_4k='c' * 40)
            validate_candidate_contract(json.loads(path.read_text()), ROOT)

    def test_overlay_reads_only_allowlisted_committed_files_and_preserves_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), '-c', 'user.name=Fixture',
                    '-c', 'user.email=fixture@example.invalid', *args], stderr=subprocess.DEVNULL, text=True).strip()
            git('init')
            for name in source_paths():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('main')
            git('add', '.'); git('commit', '-m', 'main fixture')
            main = git('rev-parse', 'HEAD')
            unrelated = 'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetValidator.java'
            for name in (*SEEDANCE_4K_FILES, unrelated): (root / name).write_text('branch')
            git('add', '.'); git('commit', '-m', '4k fixture')
            branch = git('rev-parse', 'HEAD')
            (root / SEEDANCE_4K_FILES[0]).write_text('user edits')
            with immutable_sources(root, main, branch) as (snapshot, commit, inputs, overlays):
                self.assertEqual(commit, main)
                self.assertEqual((snapshot / unrelated).read_text(), 'main')
                self.assertEqual((snapshot / SEEDANCE_4K_FILES[0]).read_text(), 'branch')
                self.assertEqual(set(overlays['seedance4k']['sourceFiles']), set(SEEDANCE_4K_FILES))
                self.assertNotEqual(inputs[SEEDANCE_4K_FILES[0]], overlays['seedance4k']['sourceFiles'][SEEDANCE_4K_FILES[0]])
            self.assertEqual((root / SEEDANCE_4K_FILES[0]).read_text(), 'user edits')
            self.assertEqual(git('rev-parse', 'HEAD'), branch)
