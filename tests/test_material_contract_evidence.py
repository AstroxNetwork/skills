"""Immutable source acquisition and fail-closed recorded evidence checks."""
import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tools.validate_generate_contract import immutable_source, source_paths, record_evidence
from tools.verify_release_acceptance import candidate_fingerprint, evidence_digest, validate_candidate_contract, validate

ROOT = Path(__file__).resolve().parents[1]


def evidence_for(root=ROOT):
    proof = {"status": "passed", "mode": "local-immutable-main", "verifiedMainCommit": "a" * 40,
             "verifiedAt": datetime.now(timezone.utc).isoformat(), "candidateSha256": candidate_fingerprint(root),
             "validatorSha256": hashlib.sha256((root / "tools/validate_generate_contract.py").read_bytes()).hexdigest(),
             "sourceFiles": {name: "b" * 64 for name in source_paths() if not name.endswith("/assetUploadValidation.ts")}}
    proof["evidenceSha256"] = evidence_digest(proof)
    return proof


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.proof = evidence_for()
        self.record = {"version": "v0.6.0", "candidateSha256": self.proof["candidateSha256"],
                       "generateContractCheck": self.proof, "realHumanCoreFlow": {"status": "pending"}}

    def test_offline_contract_proof_does_not_approve_real_person_or_release(self):
        validate(self.record, "v0.6.0", root=ROOT, contract_only=True)
        with self.assertRaisesRegex(ValueError, "real-person"):
            validate(self.record, "v0.6.0", root=ROOT)

    def test_tampered_status_hashes_and_commit_fail(self):
        for field, value in (("status", "failed"), ("verifiedMainCommit", "z" * 40),
                             ("sourceFiles", {}), ("candidateSha256", "0" * 64),
                             ("validatorSha256", "0" * 64)):
            changed = copy.deepcopy(self.record)
            changed["generateContractCheck"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_candidate_contract(changed, ROOT)

    def test_recomputed_digest_cannot_hide_missing_inputs_or_stale_candidate(self):
        for field, value in (("sourceFiles", {}), ("candidateSha256", "0" * 64),
                             ("validatorSha256", "0" * 64), ("verifiedMainCommit", "not-a-sha")):
            changed = copy.deepcopy(self.record)
            proof = changed["generateContractCheck"]
            proof[field] = value
            proof["evidenceSha256"] = evidence_digest(proof)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_candidate_contract(changed, ROOT)

    def test_expired_future_and_naive_time_fail_even_with_new_digest(self):
        for date in (datetime.now(timezone.utc) - timedelta(days=8),
                     datetime.now(timezone.utc) + timedelta(days=1), datetime(2026, 10, 23)):
            changed = copy.deepcopy(self.record)
            proof = changed["generateContractCheck"]
            proof["verifiedAt"] = date.isoformat()
            proof["evidenceSha256"] = evidence_digest(proof)
            with self.subTest(date=date), self.assertRaises(ValueError):
                validate_candidate_contract(changed, ROOT)

    def test_source_or_test_mutation_invalidates_evidence(self):
        with patch("tools.verify_release_acceptance.candidate_fingerprint", return_value="0" * 64):
            with self.assertRaisesRegex(ValueError, "exact candidate"):
                validate_candidate_contract(self.record, ROOT)

    def test_record_preserves_pending_gates_and_rejects_wrong_verified_main(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "acceptance.json"
            original = {"version": "v0.6.0", "realHumanCoreFlow": {"status": "pending"},
                        "publication": {"status": "awaiting-user-confirmation"}}
            record.write_text(json.dumps(original))
            before = record.read_bytes()
            with self.assertRaises(ValueError):
                record_evidence(record, ROOT, "a" * 40, "c" * 40, self.proof["sourceFiles"])
            self.assertEqual(record.read_bytes(), before)
            record_evidence(record, ROOT, "a" * 40, "a" * 40, self.proof["sourceFiles"])
            after = json.loads(record.read_text())
            self.assertEqual(after["realHumanCoreFlow"], original["realHumanCoreFlow"])
            self.assertEqual(after["publication"], original["publication"])
            validate_candidate_contract(after, ROOT)


class ImmutableSourceTests(unittest.TestCase):
    def test_dirty_worktree_and_changed_branch_are_not_used_or_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.STDOUT)
            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "Offline fixture")
            name = "fixture/source.txt"
            path = repo / name
            path.parent.mkdir()
            path.write_text("committed main")
            git("add", "--", name)
            git("commit", "-qm", "fixture")
            commit = git("rev-parse", "HEAD").decode().strip()
            git("branch", "-M", "fixture-main")
            path.write_text("user dirty work")
            status = git("status", "--porcelain=v1", "-z")
            with patch("tools.validate_generate_contract.source_paths", return_value=(name,)):
                with immutable_source(repo, "fixture-main") as (root, actual, hashes):
                    snapshot = root
                    self.assertEqual(actual, commit)
                    self.assertEqual((root / name).read_text(), "committed main")
                    self.assertEqual(hashes[name], hashlib.sha256(b"committed main").hexdigest())
                self.assertFalse(snapshot.exists())
            self.assertEqual(path.read_text(), "user dirty work")
            self.assertEqual(git("status", "--porcelain=v1", "-z"), status)
            self.assertEqual(git("branch", "--show-current").strip(), b"fixture-main")

    def test_required_missing_source_does_not_fall_back_to_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            # Git errors must propagate instead of validating an arbitrary directory.
            with self.assertRaises(subprocess.CalledProcessError):
                with immutable_source(Path(temporary), "main"):
                    self.fail("Non-repository source was accepted")
