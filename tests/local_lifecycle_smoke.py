#!/usr/bin/env python3
"""Opt-in, user-authorized local default-uninstall/reinstall acceptance. Never purges."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parents[1]
    prefix = Path.home() / '.local'
    config = Path.home() / '.config/holycrab'
    launcher = prefix / 'bin/holycrab'
    manifest = json.loads((prefix / 'lib/holycrab/installation.json').read_text())
    assert Path(manifest['prefix']).resolve() == prefix.resolve()
    assert manifest['managedBy'] == 'holycrab-installer'
    assert manifest['agents'] == ['codex', 'claude']
    backup = root / 'local-recovery'
    backup.mkdir(mode=0o700)
    shutil.copytree(config, backup / 'config')
    shutil.copytree(prefix / 'lib/holycrab', backup / 'lib')
    shutil.copy2(launcher, backup / 'holycrab')
    key_hash = hashlib.sha256((config / 'config.json').read_bytes()).hexdigest()
    env = {**os.environ, 'HOLYCRAB_INSTALL_SOURCE_DIR': str(source), 'HOLYCRAB_INSTALL_PREFIX': str(prefix),
           'HOLYCRAB_CONFIG_DIR': str(config), 'HOLYCRAB_INSTALL_AGENTS': ','.join(manifest['agents']),
           'HOLYCRAB_INSTALL_MCP': '1' if manifest['mcp'] else '0', 'HOLYCRAB_NO_UPDATE_CHECK': '1'}
    env.pop('HOLYCRAB_API_KEY', None)
    def install():
        result = subprocess.run(['sh', str(source / 'install.sh')], env=env, text=True, capture_output=True)
        assert result.returncode == 0, 'Installation failed; local recovery backup retained'
        assert subprocess.check_output([str(launcher), '--version'], env=env, text=True).strip() == 'holycrab 0.4.4'
    install()
    print('PASS local candidate installation', flush=True)
    try:
        result = subprocess.run([str(launcher), 'uninstall', '--yes'], env=env, text=True, capture_output=True)
        assert result.returncode == 0, 'Default uninstall failed; reinstalling'
        assert not launcher.exists() and not (prefix / 'lib/holycrab/holycrab_cli.py').exists()
        assert hashlib.sha256((config / 'config.json').read_bytes()).hexdigest() == key_hash
        print('PASS actual default uninstall; credential bytes preserved', flush=True)
    finally:
        install()
        print('PASS local reinstallation', flush=True)
    assert hashlib.sha256((config / 'config.json').read_bytes()).hexdigest() == key_hash
    doctor = json.loads(subprocess.check_output([str(launcher), 'doctor', '--json'], env=env, text=True))
    assert doctor['ok']
    account = json.loads(subprocess.check_output([str(launcher), 'auth', 'status'], env=env, text=True))
    assert account['valid'] and account['account']['username'] == 'user1'
    for parent in ('.agents', '.claude'):
        for relative in ('SKILL.md', 'references/capabilities.json', 'agents/openai.yaml'):
            assert (Path.home() / parent / 'skills/holycrab' / relative).read_bytes() == (source / 'holycrab' / relative).read_bytes()
    print('PASS local doctor, connected account and both Agent Skills restored', flush=True)


if __name__ == '__main__':
    main()
