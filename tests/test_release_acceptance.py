"""The release stays blocked until real manual acceptance is recorded."""
import unittest

from tools.verify_release_acceptance import validate


class ReleaseAcceptanceTests(unittest.TestCase):
    def test_pending_or_wrong_version_stays_blocked(self):
        for record in ({}, {"version": "v0.4.2"},
                       {"version": "v0.4.3", "realHumanCoreFlow": {"status": "pending"}}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate(record, "v0.4.3")

    def test_passed_requires_complete_valid_operator_evidence(self):
        fixture = {"status": "passed", "operator": "fixture operator",
                   "testedAt": "2026-09-16T00:00:00Z", "notes": "Fixture only; not real acceptance"}
        for field in ("operator", "testedAt", "notes"):
            flow = {**fixture, field: ""}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate({"version": "v0.4.3", "realHumanCoreFlow": flow}, "v0.4.3")
        with self.assertRaises(ValueError):
            validate({"version": "v0.4.3", "realHumanCoreFlow": {**fixture, "testedAt": "invalid"}}, "v0.4.3")

    def test_complete_fixture_is_accepted_without_any_network(self):
        validate({"version": "v0.4.3", "realHumanCoreFlow": {
            "status": "passed", "operator": "fixture operator",
            "testedAt": "2026-09-16T00:00:00Z", "notes": "Fixture only; not real acceptance",
        }}, "v0.4.3")
