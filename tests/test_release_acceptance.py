"""The release stays blocked until real manual acceptance is recorded."""
import copy
import hashlib
import json
import shutil
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


class ModelOnlyReuseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]
        self.record = json.loads((self.root / "docs/release-acceptance/v0.5.0.json").read_text())

    def test_historical_acceptance_does_not_approve_rewritten_core(self):
        with self.assertRaises(ValueError):
            validate(self.record, "v0.5.0", root=self.root)

    def test_missing_proof_confirmation_or_wrong_version_fails(self):
        for field in ("unchangedCore", "confirmedAt", "retestWaived"):
            record = copy.deepcopy(self.record)
            record["realHumanCoreFlow"].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(record, "v0.5.0", root=self.root)
        record = copy.deepcopy(self.record)
        record["version"] = "v0.5.1"
        with self.assertRaises(ValueError):
            validate(record, "v0.5.1", root=self.root)
        record = copy.deepcopy(self.record)
        record.pop("generateMainContract")
        with self.assertRaises(ValueError):
            validate(record, "v0.5.0", root=self.root, contract_only=True)

    def test_upload_core_change_invalidates_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "holycrab/scripts/holycrab_cli.py"
            for relative in CONTRACT_FILES:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((self.root / relative).read_bytes())
            source = file.read_text()
            file.write_text(source.replace('def create_authorization(name: str)', 'def create_authorization(name: bytes)'))
            record = copy.deepcopy(self.record)
            record["generateMainContract"]["files"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in CONTRACT_FILES}
            with self.assertRaisesRegex(ValueError, "core code changed"):
                validate(record, "v0.5.0", root=root)


class V050LocalContractTests(LocalContractEvidenceTests):
    def setUp(self):
        super().setUp()
        self.record["version"] = "v0.5.0"

    def test_valid_evidence_verifies_real_input_files_offline(self):
        validate(self.record, "v0.5.0", root=self.root, contract_only=True)

    def test_future_versions_cannot_use_local_mode(self):
        record = copy.deepcopy(self.record)
        record["version"] = "v0.5.1"
        with self.assertRaises(ValueError):
            validate(record, "v0.5.1", root=self.root, contract_only=True)


class CandidateAcceptanceTests(unittest.TestCase):
    def test_new_candidate_requires_fresh_bound_acceptance(self):
        from tests.test_material_contract_evidence import evidence_for
        root = Path(__file__).parents[1]
        tag = 'v0.7.5'
        proof = evidence_for(root)
        record = {'version': tag, 'candidateSha256': proof['candidateSha256'],
                  'generateContractCheck': proof, 'realHumanCoreFlow': {
            'status': 'passed', 'operator': 'offline fixture only', 'testedAt': '2026-11-19T00:00:00Z',
            'notes': 'Not actual acceptance'}}
        validate(record, tag, root=root, contract_only=True)
        validate(record, tag, root=root)
        pending = {**record, 'realHumanCoreFlow': {'status': 'pending'}}
        with self.assertRaisesRegex(ValueError, 'real-person'):
            validate(pending, tag, root=root)
        record['candidateSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'exact candidate'):
            validate(record, tag, root=root)

    def test_module_change_invalidates_candidate_fingerprint(self):
        from tools.verify_release_acceptance import candidate_fingerprint
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'candidate'
            shutil.copytree(Path(__file__).parents[1], root, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            before = candidate_fingerprint(root)
            for relative in ('holycrab/scripts/holycrab_runtime/uploads.py', 'tools/run_checks.py', 'tests/test_mcp.py'):
                with self.subTest(changed=relative):
                    path = root / relative
                    original = path.read_text()
                    path.write_text(original + '\n# fixture mutation\n')
                    self.assertNotEqual(candidate_fingerprint(root), before)
                    path.write_text(original)


class Candidate075ReuseTests(unittest.TestCase):
    tag = "v0.7.5"
    baseline_commit = "c5838ae836fb1f8482951dbc404a8329d98e5cf5"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "candidate"
        shutil.copytree(Path(__file__).parents[1], self.root,
                        ignore=shutil.ignore_patterns(".git", "build", "__pycache__", "*.pyc"))
        self.flow = {
            "status": "previously-accepted",
            "retestWaived": True,
            "operator": "fixture operator",
            "confirmedAt": "2026-11-17T12:00:00+08:00",
            "notes": "Fixture only. Reuses the tagged historical real-person upload check.",
            "baselineVersion": "v0.7.4",
            "baselineCommit": self.baseline_commit,
        }

    def record(self, *, tag=None, flow=None):
        from tests.test_material_contract_evidence import evidence_for

        proof = evidence_for(self.root)
        return {"version": tag or self.tag, "candidateSha256": proof["candidateSha256"],
                "generateContractCheck": proof, "realHumanCoreFlow": self.flow if flow is None else flow}

    def test_pinned_historical_acceptance_and_matching_candidate_are_valid(self):
        validate(self.record(), self.tag, root=self.root)

    def test_changed_historical_document_is_rejected(self):
        baseline = self.root / "docs/release-acceptance/v0.7.4.json"
        altered = json.loads(baseline.read_bytes())
        altered["realHumanCoreFlow"]["notes"] += " Altered."
        baseline.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "baseline|historical|v0[.]7[.]4"):
            validate(self.record(), self.tag, root=self.root)

    def test_core_and_reviewed_shared_changes_reject_reuse_even_with_fresh_candidate_proof(self):
        scripts = self.root / "holycrab/scripts"
        paths = [scripts / "holycrab_runtime" / name for name in (
            "uploads.py", "real_human.py", "downloads.py", "settings.py",
            "capabilities.py", "generation.py", "__init__.py", "exports.py",
        )]
        paths.append(scripts / "vendor/segno-1.6.6-py3-none-any.whl")
        for path in paths:
            original = path.read_bytes()
            with self.subTest(file=path.name):
                try:
                    path.write_bytes(original + b"\n# fixture changed candidate\n")
                    record = self.record()
                    validate(record, self.tag, root=self.root, contract_only=True)
                    with self.assertRaisesRegex(ValueError, "core|shared|reviewed"):
                        validate(record, self.tag, root=self.root)
                finally:
                    path.write_bytes(original)

    def test_reuse_requires_exact_baseline_and_explicit_aware_confirmation(self):
        for name, value in (("baselineCommit", "0" * 40), ("baselineVersion", "v0.7.3"),
                            ("retestWaived", False), ("retestWaived", 1), ("operator", " "),
                            ("notes", ""), ("confirmedAt", "2026-11-19T22:30:00"),
                            ("confirmedAt", "2026-11-15T22:30:00+08:00"),
                            ("confirmedAt", "2099-01-01T00:00:00+08:00"), ("confirmedAt", "not-a-date")):
            with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                validate(self.record(flow={**self.flow, name: value}), self.tag, root=self.root)

    def test_following_candidate_cannot_inherit_the_retest_waiver(self):
        manifest_path = self.root / "runtime-files.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_path.write_text(json.dumps({**manifest, "version": "0.7.6"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "real-person"):
            validate(self.record(tag="v0.7.6"), "v0.7.6", root=self.root)
