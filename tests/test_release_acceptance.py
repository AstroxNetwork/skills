"""The release stays blocked until real manual acceptance is recorded."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.verify_release_acceptance import CONTRACT_FILES, validate, validate_contract


class ReleaseAcceptanceTests(unittest.TestCase):
    def test_pending_or_wrong_version_stays_blocked(self):
        for record in ({}, {"version": "v0.4.2"},
                       {"version": "v0.4.4", "realHumanCoreFlow": {"status": "pending"}}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate(record, "v0.4.4")

    def test_passed_requires_complete_valid_operator_evidence(self):
        fixture = {"status": "passed", "operator": "fixture operator",
                   "testedAt": "2026-09-16T00:00:00Z", "notes": "Fixture only; not real acceptance"}
        for field in ("operator", "testedAt", "notes"):
            flow = {**fixture, field: ""}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate({"version": "v0.4.4", "realHumanCoreFlow": flow}, "v0.4.4")
        with self.assertRaises(ValueError):
            validate({"version": "v0.4.4", "realHumanCoreFlow": {**fixture, "testedAt": "invalid"}}, "v0.4.4")

    def test_complete_fixture_is_accepted_without_any_network(self):
        validate({"version": "v0.4.4", "realHumanCoreFlow": {
            "status": "passed", "operator": "fixture operator",
            "testedAt": "2026-09-16T00:00:00Z", "notes": "Fixture only; not real acceptance",
        }}, "v0.4.4")

    def test_previous_acceptance_with_explicit_retest_waiver_is_not_a_new_test(self):
        validate({"version": "v0.4.4", "realHumanCoreFlow": {
            "status": "previously-accepted", "operator": "user", "retestWaived": True,
            "testedAt": None, "confirmedAt": "2026-09-16T15:56:01Z",
            "notes": "User reports prior acceptance and waives repeat testing for this release",
        }}, "v0.4.4")

    def test_previous_acceptance_without_explicit_waiver_stays_blocked(self):
        flow = {"status": "previously-accepted", "operator": "user", "testedAt": None,
                "confirmedAt": "2026-09-16T15:56:01Z", "notes": "Prior acceptance"}
        with self.assertRaises(ValueError):
            validate({"version": "v0.4.4", "realHumanCoreFlow": flow}, "v0.4.4")

    def test_future_release_does_not_inherit_v043_waiver(self):
        with self.assertRaises(ValueError):
            validate({"version": "v0.4.5", "realHumanCoreFlow": {
                "status": "previously-accepted", "operator": "user", "retestWaived": True,
                "confirmedAt": "2026-09-16T15:56:01Z", "notes": "Fixture prior acceptance",
            }}, "v0.4.5")


class LocalContractEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        hashes = {}
        for name in CONTRACT_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"isolated fixture")
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.record = {"version": "v0.4.4", "generateMainContract": {
            "status": "passed", "mode": "local-synced-main", "localMainCommit": "a" * 40,
            "remoteMainCommit": "a" * 40, "verifiedAt": "2026-09-16T15:56:01Z", "files": hashes,
        }}

    def test_valid_evidence_verifies_real_input_files_offline(self):
        validate(self.record, "v0.4.4", root=self.root, contract_only=True)

    def test_different_or_missing_main_commit_and_unverified_record_are_rejected(self):
        for patch in ({"remoteMainCommit": "b" * 40}, {"localMainCommit": "short"},
                      {"status": "pending"}, {"mode": "branch-working-tree"}):
            record = copy.deepcopy(self.record)
            record["generateMainContract"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_contract(record, self.root)

    def test_modified_or_missing_input_hash_stays_blocked(self):
        for name in CONTRACT_FILES:
            path = self.root / name
            path.write_bytes(b"changed input")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "stale"):
                validate_contract(self.record, self.root)
            path.write_bytes(b"isolated fixture")
        record = copy.deepcopy(self.record)
        record["generateMainContract"]["files"].pop(CONTRACT_FILES[0])
        with self.assertRaises(ValueError):
            validate_contract(record, self.root)

    def test_future_versions_cannot_use_local_mode(self):
        record = copy.deepcopy(self.record)
        record["version"] = "v0.4.5"
        with self.assertRaises(ValueError):
            validate(record, "v0.4.5", root=self.root, contract_only=True)

    def test_manual_confirmation_cannot_replace_missing_contract_proof(self):
        with self.assertRaises(ValueError):
            validate({"version": "v0.4.4", "realHumanCoreFlow": {
                "status": "previously-accepted", "operator": "user", "retestWaived": True,
                "confirmedAt": "2026-09-16T15:56:01Z", "notes": "Prior acceptance",
            }}, "v0.4.4", root=self.root)
