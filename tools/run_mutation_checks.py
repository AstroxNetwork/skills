#!/usr/bin/env python3
"""Targeted behavioral mutations in disposable copies; errors/skips are not kills."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_checks import isolated_environment, run_stage, source_fingerprint, write_json

RUNTIME = 'holycrab/scripts/holycrab_runtime/'
HTTP = 'tests.adversarial.check_http_flows.HttpWorkflowTests.'
REGRESSION = 'tests.test_adversarial_regressions.AdversarialRegressionTests.'
# Each edit is an explicit injected defect, never a change to the test oracle.
MUTATIONS = [
    ('duplicate-submit', HTTP + 'test_two_processes_same_attempt_only_create_one_task', [
        ('state.py', 'if attempt_id in attempts:', 'if False:')]),
    ('purge-during-submission', HTTP + 'test_active_submission_prevents_purge_and_keeps_reservation', [
        ('generation.py', '@_state.protect_operation\ndef create_generation(', 'def create_generation(')]),
    ('reusable-upload-plan', HTTP + 'test_concurrent_upload_plan_sends_only_one_file', [
        ('uploads.py', 'plan["state"] = "executing"', 'plan["state"] = "prepared"')]),
    ('audio-estimate-body', HTTP + 'test_all_sixteen_models_real_http_estimate_create_query_download', [
        ('generation.py', 'payload=normalized)', 'payload=None if kind == "audio" else normalized)')]),
    ('success-exit-on-api-error', HTTP + 'test_refused_estimate_exits_nonzero_and_never_creates', [
        ('cli.py', 'except _results.ApiError as error:\n        _presentation.print_result(error.result)\n        return 1',
         'except _results.ApiError as error:\n        _presentation.print_result(error.result)\n        return 0')]),
    ('mcp-error-flag', HTTP + 'test_mcp_backend_rejection_preserves_false_zero_and_is_error', [
        ('mcp.py', '"isError": isinstance(value, _results.OperationResult) and value.outcome != "success",', '"isError": False,')]),
    ('truncated-download', HTTP + 'test_partial_and_truncated_http_download_preserve_old_file', [
        ('downloads.py', 'if expected_length is not None and total != expected_length:', 'if False:')]),
    ('installer-digest-bypass', 'tests.test_v041.V041Tests.test_update_requires_confirmation_and_verifies_digest_before_execution', [
        ('lifecycle.py', 'if digest.hexdigest() != expected:', 'if False:')]),
    ('delete-customized-skill', REGRESSION + 'test_modified_owned_skill_is_not_deleted', [
        ('lifecycle.py', 'if not matches:\n                warnings.append(f"Modified or unverified Skill file was kept: {target}")',
         'if False:\n                warnings.append(f"Modified or unverified Skill file was kept: {target}")')]),
    ('accept-changed-price', REGRESSION + 'test_changed_quote_is_rejected_before_attempt_or_submission', [
        ('generation.py', "if approved_credits is not None and estimate['estimate']['frozenCredit'] != approved_credits:", 'if False:')]),
]


def execute(root, name, test, directory, env):
    report = directory / (name + '.json')
    stage = run_stage(name, [sys.executable, str(root / 'tools/run_checks.py'), '--unit-worker', str(report),
                            '--test-name', test], directory=directory, env=env, timeout=120, cwd=root)
    try:
        details = json.loads(report.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        details = {}
    return stage, details


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', required=True, type=Path)
    args = p.parse_args(argv)
    directory = args.report.resolve().parent / 'mutations'
    directory.mkdir(parents=True, exist_ok=True)
    result = {'schemaVersion': 1, 'source': source_fingerprint(), 'mutations': [], 'ok': False}
    for identifier, test, edits in MUTATIONS:
        row = {'id': identifier, 'test': test, 'status': 'invalid'}
        result['mutations'].append(row)
        try:
            with tempfile.TemporaryDirectory(prefix='holycrab-mutant-') as temp:
                root = Path(temp) / 'source'
                shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('.git', 'build', '__pycache__', '*.pyc'))
                env = isolated_environment(Path(temp))
                env['HOLYCRAB_TEST_LOOPBACK'] = '1'
                baseline, checks = execute(root, identifier + '-baseline', test, directory, env)
                row['baseline'] = baseline
                if baseline['status'] != 'passed' or checks.get('executed') != 1 or checks.get('skipped'):
                    raise ValueError('Baseline selected behavior did not pass exactly one non-skipped test')
                for file, before, after in edits:
                    path = root / RUNTIME / file
                    content = path.read_text(encoding='utf-8')
                    if content.count(before) != 1:
                        raise ValueError('Mutation anchor must be unique: ' + file)
                    mutated = content.replace(before, after)
                    compile(mutated, file, 'exec')
                    path.write_text(mutated, encoding='utf-8')
                # Installed-flow tests must reach the behavioral defect, not fail an inventory check.
                subprocess.run([sys.executable, str(root / 'tools/refresh_installer_hashes.py')],
                               cwd=root, env=env, check=True, capture_output=True, timeout=20)
                stage, checks = execute(root, identifier + '-mutant', test, directory, env)
                row['mutant'] = stage
                assertions = checks.get('failures', [])
                row['assertions'] = assertions
                killed = (stage['status'] == 'failed' and stage.get('exitCode') == 1
                          and checks.get('executed') == 1 and assertions and not checks.get('errors')
                          and not checks.get('skipped') and not checks.get('expectedFailures'))
                row['status'] = 'killed' if killed else 'survived' if stage['status'] == 'passed' else 'invalid'
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            row['reason'] = str(exc)
        print(identifier + ': ' + row['status'], flush=True)
        write_json(args.report, result)
    result['ok'] = bool(result['mutations']) and all(row['status'] == 'killed' for row in result['mutations'])
    write_json(args.report, result)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
