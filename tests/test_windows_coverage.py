import copy
import unittest
from tools.verify_windows_coverage import verify


class WindowsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fingerprint = {'sha256': 'candidate', 'files': {'cli.py': 'digest'}}
        self.unit = {'source': copy.deepcopy(self.fingerprint), 'status': 'passed_with_skips', 'executed': 1, 'testIds': ['test_shell.flow', 'test_portable.flow'],
                     'skipped': [{'test': 'test_shell.flow'}]}
        self.mapping = {'cases': [{'test': 'test_shell.flow', 'disposition': 'native-equivalent-required', 'requiredCase': 'native.flow'},
                                 {'test': 'test_portable.flow', 'disposition': 'run-on-windows'}]}
        self.native = {'ok': True, 'sourceFingerprint': copy.deepcopy(self.fingerprint), 'environment': {'administrator': False, 'os': 'Windows-2025'},
                       'expectedCaseIds': ['native.flow', 'native.other'],
                       'cases': [{'id': 'native.flow', 'status': 'passed'}, {'id': 'native.other', 'status': 'passed'}]}
        self.expected = ['native.flow', 'native.other']

    def test_same_candidate_successful_replacement_is_accepted(self):
        self.assertEqual(verify(self.unit, self.native, self.mapping, self.fingerprint, self.expected), [])

    def test_stale_failed_missing_duplicate_privileged_and_skipped_evidence_fail(self):
        for mutation in ('stale', 'old-unit', 'failed', 'missing', 'missing-nonskip', 'duplicate', 'admin', 'portable-skip', 'new-skip', 'disappeared'):
            with self.subTest(mutation=mutation):
                unit, native = copy.deepcopy(self.unit), copy.deepcopy(self.native)
                if mutation == 'stale': native['sourceFingerprint']['sha256'] = 'old'
                if mutation == 'old-unit': unit['source']['sha256'] = 'old'
                if mutation == 'failed': native['cases'][0]['status'] = 'failed'
                if mutation == 'missing': native['cases'] = []
                if mutation == 'missing-nonskip': native['cases'].pop()
                if mutation == 'duplicate': native['cases'] *= 2
                if mutation == 'admin': native['environment']['administrator'] = True
                if mutation == 'portable-skip': unit['skipped'].append({'test': 'test_portable.flow'})
                if mutation == 'new-skip': unit['skipped'].append({'test': 'test_new.flow'})
                if mutation == 'disappeared': unit['testIds'].remove('test_portable.flow')
                self.assertTrue(verify(unit, native, self.mapping, self.fingerprint, self.expected))
