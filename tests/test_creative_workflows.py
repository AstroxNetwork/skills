"""Production graph invariants. Transport is always mocked; no billable tasks."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_cli import holycrab
from holycrab_runtime import workflows as w, skills, media, generation, state, uploads, real_human


class CreativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": str(self.root), "HOLYCRAB_API_KEY": "creative-fixture", "HOLYCRAB_NO_UPDATE_CHECK": "1"})
        env.start(); self.addCleanup(env.stop)
        self.plan = {"title": "Product ad", "style": "keep bottle identity", "steps": [
            {"id": "shot1", "kind": "image", "request": {"model": "seedream-5-0-lite-260128", "prompt": "first", "size": "2K"}},
            {"id": "shot2", "kind": "image", "request": {"model": "seedream-5-0-lite-260128", "prompt": "second", "size": "2K"}},
            {"id": "download", "kind": "download", "request": {"taskId": {"fromStep": "shot1", "output": "taskId"}, "output": str(self.root / "image.png")}},
        ]}

    def create(self):
        return w.prepare(self.plan)["workflowId"]

    def quote(self, key):
        with patch.object(generation, "estimate_generation", return_value={"estimate": {"frozenCredit": 5}}):
            return w.estimate(key)

    def test_recent_projects_resume_by_title_and_preserve_unreadable_records(self):
        key = self.create()
        corrupt = w.directory() / "corrupt.json"; corrupt.write_text("broken")
        listing = w.list_workflows(limit=1)
        self.assertEqual(listing["workflows"][0]["workflowId"], key)
        self.assertEqual(listing["workflows"][0]["title"], "Product ad")
        self.assertEqual(listing["unreadableRecords"], ["corrupt.json"])
        self.assertEqual(corrupt.read_text(), "broken")

    def test_prepare_is_local_and_rejects_cycles_unknown_dependencies_and_nan(self):
        with patch.object(w.transport, "send") as send:
            key = self.create()
            self.assertEqual(w.get(key)["state"], "pending")
            send.assert_not_called()
        for dependencies in (["missing"], ["shot1"], ["download"]):
            plan = copy.deepcopy(self.plan); plan["steps"][0]["dependsOn"] = dependencies
            with self.assertRaises(ValueError): w.prepare(plan)
        plan = copy.deepcopy(self.plan); plan["steps"][0]["request"]["bad"] = float("nan")
        with self.assertRaises(ValueError): w.prepare(plan)

    def test_estimate_prices_only_ready_steps_and_no_creates(self):
        key = self.create()
        with patch.object(generation, "create_generation") as create:
            quote = self.quote(key)
        self.assertEqual(quote["readyCredits"], 10)
        self.assertEqual(quote["deferredSteps"], ["download"])
        create.assert_not_called()

    def test_progress_provides_the_action_for_pending_unknown_and_quota_states(self):
        key = self.create()
        self.assertEqual(w.get(key)["nextAction"]["command"], f"holycrab workflows estimate {key}")
        value = w.load(key)
        value["steps"]["shot1"].update(state="unknown", attemptId="uncertain")
        w.save(value)
        self.assertEqual(w.get(key)["nextAction"]["command"], f"holycrab workflows get {key} --refresh")
        value["steps"]["shot1"].update(state="failed", result={"errorCode": "API_TOKEN_QUOTA_EXCEEDED"})
        w.save(value)
        self.assertEqual(w.get(key)["nextAction"]["code"], "INCREASE_KEY_QUOTA")
        self.assertIsNone(w.get(key)["nextAction"]["command"])

    def test_missing_media_tools_expose_diagnostics_without_dumping_them_in_the_hint(self):
        with patch.object(media, "executable", return_value="/broken/ffmpeg"), patch.object(media, "run", side_effect=ValueError("dyld: " + "diagnostic " * 400)):
            result = media.doctor()
        self.assertFalse(result["ok"])
        self.assertIn("HOLYCRAB_FFMPEG", result["errors"][0])
        self.assertNotIn("dyld:", " ".join(result["errors"]))
        self.assertIn("dyld:", result["diagnostics"]["ffmpeg"])

    def test_confirmation_and_stale_approval_do_not_submit(self):
        key = self.create(); quote = self.quote(key)
        with patch.object(generation, "create_generation") as create:
            with self.assertRaises(ValueError): w.run_next(key, approval_id=quote["approvalId"], confirmed=False)
            w.prepare(self.plan, workflow_id=key, expected_revision=1)
            with self.assertRaises(ValueError): w.run_next(key, approval_id=quote["approvalId"], confirmed=True)
            create.assert_not_called()

    def test_ready_batch_executes_once_and_refresh_does_not_recreate(self):
        key = self.create(); quote = self.quote(key)
        with patch.object(generation, "create_generation", side_effect=[{"taskId": "task1"}, {"taskId": "task2"}]) as create:
            for _ in range(3): w.run_next(key, approval_id=quote["approvalId"], confirmed=True)
            self.assertEqual(create.call_count, 2)
            self.assertNotEqual(create.call_args_list[0].kwargs["attempt_id"], create.call_args_list[1].kwargs["attempt_id"])
            self.assertEqual(create.call_args.kwargs["approved_credits"], 5)
            with patch.object(w.transport, "send", return_value=(200, {"code": 200, "data": {"step": 2}})):
                result = w.get(key, refresh=True)
            self.assertEqual(result["steps"][0]["outputs"], {"taskId": "task1"})
            self.assertEqual(create.call_count, 2)

    def test_ledger_and_unknown_submission_prevent_duplicate(self):
        key = self.create(); quote = self.quote(key)
        def submit(kind, request, **options):
            state.reserve_attempt(options["attempt_id"], {"state": "unknown", "attemptId": options["attempt_id"]})
            raise OSError("lost response")
        with patch.object(generation, "create_generation", side_effect=submit) as create:
            result = w.run_next(key, approval_id=quote["approvalId"], confirmed=True)
            self.assertEqual(result.outcome, "failed")
            self.assertEqual(result["steps"][0]["state"], "unknown")
            with self.assertRaises(ValueError): w.run_next(key, approval_id=quote["approvalId"], confirmed=True)
            self.assertEqual(create.call_count, 1)

    def test_restart_recovers_task_id_from_attempt_ledger(self):
        key = self.create(); value = w.load(key)
        state.reserve_attempt("attempt1", {"attemptId": "attempt1", "state": "created", "taskId": "task1"})
        value["steps"]["shot1"].update(state="running", attemptId="attempt1"); w.save(value)
        with patch.object(w.transport, "send", return_value=(200, {"code": 200, "data": {"step": 2}})):
            result = w.get(key, refresh=True)
        self.assertEqual(result["steps"][0]["outputs"]["taskId"], "task1")

    def test_selective_revision_preserves_unaffected_outputs_and_history(self):
        key = self.create(); value = w.load(key)
        for row in value["steps"].values(): row.update(state="succeeded", outputs={"taskId": "original"})
        w.save(value)
        plan = copy.deepcopy(self.plan); plan["steps"][0]["request"]["prompt"] = "new angle"
        result = w.prepare(plan, workflow_id=key, expected_revision=1)
        self.assertEqual([s["state"] for s in result["steps"]], ["pending", "succeeded", "pending"])
        self.assertEqual(result["steps"][1]["outputs"]["taskId"], "original")
        self.assertEqual(result["history"][0]["steps"]["shot1"]["outputs"]["taskId"], "original")
        with self.assertRaises(ValueError): w.prepare(plan, workflow_id=key, expected_revision=1)

    def test_running_step_cannot_be_replaced(self):
        key = self.create(); value = w.load(key); value["steps"]["shot1"]["state"] = "submitted"; w.save(value)
        plan = copy.deepcopy(self.plan); plan["rerunStepIds"] = ["shot1"]
        with self.assertRaises(ValueError): w.prepare(plan, workflow_id=key, expected_revision=1)

    def test_unknown_rerun_exposes_duplicate_risk(self):
        key = self.create(); value = w.load(key); value["steps"]["shot1"].update(state="unknown", attemptId="old"); w.save(value)
        plan = copy.deepcopy(self.plan); plan["rerunStepIds"] = ["shot1"]
        w.prepare(plan, workflow_id=key, expected_revision=1)
        self.assertTrue(self.quote(key)["steps"][0]["duplicateRisk"])

    def test_wrong_credential_cannot_resume(self):
        key = self.create(); quote = self.quote(key)
        with patch.dict(os.environ, {"HOLYCRAB_API_KEY": "different"}), patch.object(generation, "create_generation") as create:
            with self.assertRaises(ValueError): w.run_next(key, approval_id=quote["approvalId"], confirmed=True)
            create.assert_not_called()

    def test_partial_upload_never_unlocks_generation(self):
        plan = {"title": "references", "steps": [{"id": "upload", "kind": "upload", "request": {"files": ["a", "b"]}}]}
        key = w.prepare(plan)["workflowId"]; value = w.load(key)
        value["steps"]["upload"].update(state="unknown", uploadPlanId="plan1", outputs={"assetIds": ["asset1"]}); w.save(value)
        with patch.object(uploads, "upload_plan_record", return_value={"state": "failed", "uploaded": [{"assetUniqId": "asset1"}], "notAttempted": ["b"]}), patch.object(real_human, "get_asset") as get:
            result = w.get(key, refresh=True)
        self.assertEqual(result["state"], "needs_attention"); get.assert_not_called()

    def test_asset_processing_is_separate_from_upload_completion(self):
        key = w.prepare({"title": "refs", "steps": [{"id": "up", "kind": "upload", "request": {"files": ["a"]}}]})["workflowId"]
        value = w.load(key); value["steps"]["up"].update(state="processing", uploadPlanId="plan1"); w.save(value)
        record = {"state": "completed", "uploaded": [{"assetUniqId": "asset1"}]}
        with patch.object(uploads, "upload_plan_record", return_value=record), patch.object(real_human, "get_asset", side_effect=[{"ready": False}, {"ready": True}]):
            self.assertEqual(w.get(key, refresh=True)["state"], "waiting")
            self.assertEqual(w.get(key, refresh=True)["state"], "succeeded")

    def test_local_persistence_failure_returns_known_task(self):
        key = self.create(); quote = self.quote(key); original = w.save
        def fail_after_submit(value):
            if value["steps"]["shot1"].get("taskId"): raise OSError("disk full")
            original(value)
        with patch.object(w, "save", side_effect=fail_after_submit), patch.object(generation, "create_generation", return_value={"taskId": "task1"}):
            result = w.run_next(key, approval_id=quote["approvalId"], confirmed=True)
        self.assertFalse(result["localStateSaved"])
        self.assertEqual(result["steps"][0]["taskId"], "task1")

    def test_skills_resources_are_complete_and_traversal_is_rejected(self):
        self.assertTrue(skills.health()["ok"])
        self.assertEqual(len(skills.list_skills()["skills"]), 5)
        for name in ("../../config", "missing"):
            with self.assertRaises(ValueError): skills.get_skill(name)
        with self.assertRaises(ValueError): skills.get_skill("holycrab-video", "../../config.json")

    def test_media_rejects_network_and_overwrite_without_running_tools(self):
        with self.assertRaises(ValueError): media.local_file("https://example.com/video.mp4")
        file = self.root / "result.mp4"; file.write_bytes(b"existing")
        with self.assertRaises(ValueError): media.output_path(str(file))
        self.assertEqual(file.read_bytes(), b"existing")

    def test_media_requires_exact_approval_hash(self):
        with patch.object(media, "preview", return_value={"approvalHash": "current"}):
            with self.assertRaises(ValueError): media.compose({}, confirmed=True, approval_hash="stale")

    def test_new_mcp_tools_validate_without_side_effects(self):
        for name in ("workflow_prepare", "workflow_get", "workflow_estimate", "workflow_run_next", "generation_download", "media_compose", "skill_get"):
            with self.subTest(tool=name), self.assertRaises(ValueError): holycrab.mcp_tool_call(name, {})
        result = holycrab.mcp_tool_call("skill_get", {"name": "holycrab-video"})
        self.assertIn("Video production", result["content"])


if __name__ == "__main__": unittest.main()
