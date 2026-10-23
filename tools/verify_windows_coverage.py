"""Fail closed when a Windows skip lacks same-candidate native evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_checks import source_fingerprint


def verify(unit, native, mapping, fingerprint, expected_cases, expected_architecture=None, expected_python=None):
    errors = []
    rows = mapping.get('cases', [])
    index = {row['test']: row for row in rows}
    if len(index) != len(rows): errors.append('Duplicate skip mapping')
    if unit.get('status') not in ('passed', 'passed_with_skips') or unit.get('executed', 0) < 1:
        errors.append('Unit tests did not pass')
    if unit.get('source') != fingerprint: errors.append('Unit source fingerprint differs from this candidate')
    if native.get('ok') is not True: errors.append('Native cases did not all pass')
    if native.get('sourceFingerprint') != fingerprint: errors.append('Native source fingerprint differs from this candidate')
    environment = native.get('environment', {})
    if environment.get('administrator') is not False or not environment.get('os', '').startswith('Windows'):
        errors.append('Native ordinary Windows user evidence is missing')
    if expected_architecture:
        required = 'win-arm64' if expected_architecture == 'arm64' else 'win-amd64'
        if environment.get('pythonPlatform', '').lower() != required or environment.get('pointerBits') != 64:
            errors.append('Python architecture does not match the native matrix target')
    if expected_python and not environment.get('python', '').startswith(expected_python + '.'):
        errors.append('Python version does not match the matrix target')
    cases = native.get('cases', [])
    native_index = {case['id']: case for case in cases}
    if len(native_index) != len(cases): errors.append('Duplicate native case ID')
    if set(native_index) != set(expected_cases) or native.get('expectedCaseIds') != expected_cases:
        errors.append('Native cases do not exactly match the required case list')
    if any(case.get('status') != 'passed' for case in cases): errors.append('Native case failure contradicts success')
    discovered = {name.removeprefix('tests.') for name in unit.get('testIds', [])}
    skipped = {row['test'].removeprefix('tests.') for row in unit.get('skipped', [])}
    for name in skipped:
        row = index.get(name)
        if row is None or row['disposition'] == 'run-on-windows':
            errors.append('Unexpected Windows skip: ' + name)
    for row in rows:
        if row['test'] not in discovered:
            errors.append('Mapped test disappeared: ' + row['test'])
        disposition = row['disposition']
        if disposition == 'native-equivalent-required':
            if native_index.get(row['requiredCase'], {}).get('status') != 'passed':
                errors.append('Missing successful native equivalent: ' + row['requiredCase'])
        elif disposition not in ('run-on-windows', 'pure-unix', 'separate-external'):
            errors.append('Unknown skip disposition: ' + disposition)
    return errors


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--unit', type=Path, required=True)
    p.add_argument('--native', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--expected-architecture', choices=('x64', 'arm64'))
    p.add_argument('--expected-python')
    args = p.parse_args()
    fingerprint = source_fingerprint()
    try:
        mapping = json.loads((ROOT / 'tests/windows_skip_coverage.json').read_text(encoding='utf-8'))
        expected = json.loads((ROOT / 'tests/windows_native_cases.json').read_text(encoding='utf-8'))
        errors = verify(json.loads(args.unit.read_text(encoding='utf-8-sig')),
                        json.loads(args.native.read_text(encoding='utf-8-sig')), mapping, fingerprint, expected,
                        args.expected_architecture, args.expected_python)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors = ['Invalid or missing evidence: ' + str(exc)]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'ok': not errors, 'errors': errors, 'source': fingerprint}, indent=2), encoding='utf-8')
    for error in errors: print(error)
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
