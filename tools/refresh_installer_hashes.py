#!/usr/bin/env python3
"""Refresh candidate installer hashes from this checkout; never edit published assets."""
from pathlib import Path
import hashlib
import re

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'HOLYCRAB_CLI': ('holycrab/scripts/holycrab_cli.py', 'holycrab_cli.py'),
    'CAPABILITIES': ('holycrab/references/capabilities.json', 'capabilities.json'),
    'LAUNCHER': ('bin/holycrab', None),
    'SKILL': ('holycrab/SKILL.md', 'SKILL.md'),
    'OPENAI_YAML': ('holycrab/agents/openai.yaml', 'openai.yaml'),
    'SEGNO': ('holycrab/scripts/vendor/segno-1.6.6-py3-none-any.whl', 'segno.whl'),
    'SEGNO_LICENSE': ('holycrab/scripts/vendor/LICENSE.segno', 'LICENSE.segno'),
}

def main():
    shell_path = ROOT / 'install.sh'
    ps_path = ROOT / 'install.ps1'
    shell, powershell = shell_path.read_text(), ps_path.read_text()
    for variable, (relative, ps_name) in FILES.items():
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        shell, count = re.subn(rf'(?m)^SHA256_{variable}=[0-9a-f]{{64}}$', f'SHA256_{variable}={digest}', shell)
        if count != 1:
            raise SystemExit('Missing or duplicated installer hash: ' + variable)
        if ps_name:
            pattern = rf'(Name = "{re.escape(ps_name)}"; Sha256 = ")[0-9a-f]{{64}}(\")'
            powershell, count = re.subn(pattern, lambda m: m[1]+digest+m[2], powershell)
            if count != 1:
                raise SystemExit('Missing or duplicated PowerShell hash: ' + ps_name)
    shell_path.write_text(shell)
    ps_path.write_text(powershell)
    print('Candidate installer hashes match checkout files.')

if __name__ == '__main__':
    main()
