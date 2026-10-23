"""Native ownership scenarios; called only inside the disposable ordinary account."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path


CLIENT = r'''
import json, os, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
state = Path(os.environ['HC_CLIENT_STATE'])
args = sys.argv[1:]
with open(os.environ['HC_CLIENT_LOG'], 'a', encoding='utf-8') as stream:
    stream.write(json.dumps(args) + '\n')
if args[:3] == ['mcp', 'get', 'holycrab']:
    if os.environ.get('HC_CLIENT_FAIL') == '1':
        print('client inspection failed', file=sys.stderr)
        raise SystemExit(5)
    if state.exists():
        print(state.read_text(encoding='utf-8'))
        raise SystemExit(0)
    print("No MCP server named 'holycrab' found.", file=sys.stderr)
    raise SystemExit(1)
if args[:3] == ['mcp', 'remove', 'holycrab']:
    state.unlink(missing_ok=True)
elif args[:3] == ['mcp', 'add', 'holycrab']:
    command = args[args.index('--')+1:]
    state.write_text(json.dumps({'transport': {'type':'stdio', 'command': command[0], 'args': command[1:]}}), encoding='utf-8')
else:
    raise SystemExit(2)
'''


def run_cases(suite):
    def fixture(name, *, agents='codex', mcp=True):
        root = suite.root / ('ownership-' + name)
        root.mkdir()
        prefix, config, fake = root / 'prefix', root / 'config', root / 'bin'
        fake.mkdir()
        (fake / 'client.py').write_text(CLIENT, encoding='utf-8')
        (fake / 'codex.cmd').write_text('@echo off\n"%HC_TEST_PYTHON%" "%~dp0client.py" %*\n', encoding='ascii')
        env = dict(suite.env, HOLYCRAB_INSTALL_PREFIX=str(prefix), HOLYCRAB_CONFIG_DIR=str(config),
                   HOLYCRAB_INSTALL_AGENTS=agents, HOLYCRAB_INSTALL_MCP='1' if mcp else '0',
                   HC_CLIENT_STATE=str(root / 'client.json'), HC_CLIENT_LOG=str(root / 'client.log'),
                   HC_TEST_PYTHON=sys.executable, PATH=str(fake) + os.pathsep + suite.env['PATH'])
        cli = prefix / 'lib/holycrab/holycrab_cli.py'
        manifest = cli.parent / 'installation.json'
        def install(**kwargs): return suite.install(prefix=prefix, env=env, **kwargs)
        def command(*args, **kwargs): return suite.run([sys.executable, '-X', 'utf8', str(cli), *args], env=env, **kwargs)
        def removed():
            suite.wait_missing(cli)
            suite.wait_missing(prefix / 'bin/holycrab.cmd')
        return root, prefix, config, env, manifest, install, command, removed

    def legacy():
        root, prefix, config, env, manifest, install, command, removed = fixture('legacy')
        install()
        record = json.loads(manifest.read_text(encoding='utf-8'))
        record.pop('agentRegistrations', None)
        manifest.write_text(json.dumps(record), encoding='utf-8')
        install()
        assert json.loads(manifest.read_text())['agentRegistrations']['codex']['managed'] is True
        command('uninstall', '--yes')
        removed()
        assert not Path(env['HC_CLIENT_STATE']).exists()
        return {'adoptedLegacyRegistration': True}
    suite.case('mcp.legacy-adoption', legacy)

    def unknown():
        root, prefix, config, env, manifest, install, command, removed = fixture('unknown')
        state = Path(env['HC_CLIENT_STATE'])
        state.write_text('{"transport":{"type":"stdio","command":"user-server","args":[]}}')
        before = state.read_bytes()
        env['HC_CLIENT_FAIL'] = '1'
        install()
        assert state.read_bytes() == before
        calls = [json.loads(line) for line in Path(env['HC_CLIENT_LOG']).read_text().splitlines()]
        assert all(call[:2] == ['mcp', 'get'] for call in calls)
        command('uninstall', '--yes', expected=None)
        removed()
        assert state.read_bytes() == before
        return {'uninspectableRegistrationPreserved': True}
    suite.case('mcp.unknown-inspection-preserved', unknown)

    def manual():
        root, prefix, config, env, manifest, install, command, removed = fixture('manual')
        state = Path(env['HC_CLIENT_STATE'])
        state.write_text('{"transport":{"type":"stdio","command":"user-server","args":["keep"]}}')
        before = state.read_bytes()
        install()
        assert json.loads(manifest.read_text())['agentRegistrations']['codex']['managed'] is False
        command('uninstall', '--yes')
        removed()
        assert state.read_bytes() == before
        return {'manualRegistrationPreserved': True}
    suite.case('mcp.preserve-manual-registration', manual)

    def pending():
        root, prefix, config, env, manifest, install, command, removed = fixture('pending')
        install()
        state = Path(env['HC_CLIENT_STATE'])
        before = state.read_bytes()
        env['HC_CLIENT_FAIL'] = '1'
        result = command('uninstall', '--yes', expected=None)
        removed()
        assert result.returncode != 0
        assert 'MCP cleanup pending' in result.stdout + result.stderr
        assert state.read_bytes() == before
        return {'pendingCleanupNotSuccess': True}
    suite.case('mcp.inspection-failure-pending', pending)

    def reduce_agents():
        root, prefix, config, env, manifest, install, command, removed = fixture('reduce')
        install()
        state = Path(env['HC_CLIENT_STATE'])
        assert state.exists()
        env.update(HOLYCRAB_INSTALL_AGENTS='none', HOLYCRAB_INSTALL_MCP='0')
        install()
        record = json.loads(manifest.read_text())
        assert record['agents'] == [] and record['mcp'] is False
        assert not state.exists()
        command('uninstall', '--yes')
        removed()
        return {'reducedAgentChoicePreserved': True}
    suite.case('install.reduce-agents', reduce_agents)

    def repair():
        root, prefix, config, env, manifest, install, command, removed = fixture('repair', agents='none', mcp=False)
        install()
        config.mkdir(parents=True, exist_ok=True)
        ledger = config / 'attempts.json'
        ledger.write_bytes(b'{ damaged but must preserve')
        install()
        assert ledger.read_bytes() == b'{ damaged but must preserve'
        result = json.loads(command('doctor', '--json', expected=None).stdout)
        assert result['runtimeOk'] is True and result['ok'] is False
        command('uninstall', '--yes')
        removed()
        assert ledger.exists()
        return {'corruptRecordsPreserved': True}
    suite.case('install.repair-corrupt-ledger', repair)

    def inventory():
        root, prefix, config, env, manifest, install, command, removed = fixture('inventory', agents='none', mcp=False)
        install()
        record = json.loads(manifest.read_text())
        record['coreFiles'] = [row for row in record['coreFiles'] if row['path'] != 'holycrab_runtime/state.py']
        manifest.write_text(json.dumps(record))
        result = command('doctor', '--json', expected=None)
        assert result.returncode != 0 and json.loads(result.stdout)['runtimeOk'] is False
        install()
        command('uninstall', '--yes')
        removed()
        return {'missingRuntimeInventoryRejected': True}
    suite.case('doctor.missing-runtime-inventory', inventory)

    def legacy_path():
        root, prefix, config, env, manifest, install, command, removed = fixture('legacy-path', agents='none', mcp=False)
        install()
        record = json.loads(manifest.read_text())
        record.pop('pathRegistration', None)
        manifest.write_text(json.dumps(record))
        command('uninstall', '--yes')
        removed()
        user_path = suite.ps('[Environment]::GetEnvironmentVariable("Path","User")').stdout.strip()
        assert str(prefix / 'bin').lower() in user_path.lower()
        return {'unownedUserPathPreserved': True}
    suite.case('uninstall.legacy-path-preservation', legacy_path)

    def reparse():
        root, prefix, config, env, manifest, install, command, removed = fixture('reparse', agents='none', mcp=False)
        install()
        outside = root / 'outside'
        outside.mkdir()
        secret = outside / 'config.json'
        secret.write_text('{"doNotDelete":true}')
        junction = root / 'junction'
        result = suite.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(outside)])
        env['HOLYCRAB_CONFIG_DIR'] = str(junction)
        result = command('uninstall', '--yes', '--purge', expected=None)
        assert result.returncode != 0
        assert 'Refused to purge' in result.stdout + result.stderr
        assert secret.read_text() == '{"doNotDelete":true}'
        junction.rmdir()
        return {'junctionTargetPreserved': True}
    suite.case('uninstall.purge-refuse-reparse', reparse)
