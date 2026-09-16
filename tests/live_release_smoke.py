#!/usr/bin/env python3
"""Opt-in release acceptance: actual installed processes; three paid submissions maximum.

Internal driver, never installed. This run is authorized for user1 and the selected
cat photo only. No real-person command is accepted. State survives interruptions;
an attempted write is never automatically executed a second time.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def documents(text):
    result = []
    decoder = json.JSONDecoder()
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        result.append(value)
        text = text.lstrip()[end:]
    return result


class LiveRun:
    def __init__(self, root):
        self.root = root.resolve()
        self.path = self.root / 'private-state.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'checks': [], 'writes': {}, 'tasks': {}}
        self.launcher = self.root / 'prefix/bin/holycrab'
        self.env = {**os.environ, 'HOLYCRAB_CONFIG_DIR': str(self.root / 'config'), 'HOLYCRAB_NO_UPDATE_CHECK': '1'}
        self.env.pop('HOLYCRAB_API_KEY', None)
        self.secret = ''

    def save(self):
        descriptor, temporary = tempfile.mkstemp(dir=self.root, prefix='.state-')
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(self.state, stream, ensure_ascii=False, indent=2)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def record(self, label, passed, detail=''):
        if self.secret:
            detail = detail.replace(self.secret, '[REDACTED]')
        detail = re.sub(r'https?://\S+|/(?:Users|private|tmp|var)/\S+', '[REDACTED]', detail)
        self.state['checks'].append({'label': label, 'passed': passed, 'detail': detail[-1000:],
                                     'at': datetime.now(timezone.utc).isoformat()})
        self.save()
        print(('PASS ' if passed else 'FAIL ') + label, flush=True)

    def cli(self, label, *args, expected=(0,), input=None, timeout=120, structured=True):
        if args and args[0] == 'real-human':
            raise RuntimeError('Real-person live commands are excluded')
        completed = subprocess.run([str(self.launcher), *args], env=self.env, input=input,
                                   text=True, encoding='utf-8', capture_output=True, timeout=timeout)
        passed = completed.returncode in expected
        self.record(label, passed, '' if passed else completed.stderr)
        if not passed:
            raise RuntimeError(label + ' failed; see sanitized checks')
        return documents(completed.stdout) if structured else completed.stdout

    def mcp(self, name, arguments):
        if name.startswith('real_human_') or 'groupUniqId' in arguments:
            raise RuntimeError('Real-person live tools are excluded')
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25',
                'capabilities': {}, 'clientInfo': {'name': 'release-acceptance', 'version': '0.4.4'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}},
        ]
        replies = self.cli('MCP ' + name, 'mcp', 'serve', input=''.join(json.dumps(m) + '\n' for m in messages))
        if len(replies) != 3 or any('error' in r for r in replies) or replies[-1]['result'].get('isError'):
            self.record('MCP protocol/tool ' + name, False, json.dumps(replies[-1]) if replies else 'No response')
            raise RuntimeError('MCP tool failed')
        if len(replies[1]['result']['tools']) != 20:
            raise RuntimeError('Unexpected MCP inventory')
        return replies[-1]['result']['structuredContent']

    def once(self, label, action):
        if label in self.state['writes']:
            if 'result' not in self.state['writes'][label]:
                raise RuntimeError(label + ' was already attempted; reconcile instead of retrying')
            return self.state['writes'][label]['result']
        self.state['writes'][label] = {'started': datetime.now(timezone.utc).isoformat()}
        self.save()
        value = action()
        self.state['writes'][label]['result'] = value
        self.save()
        return value

    def prepare(self):
        # Only read the existing credential, never persist it outside private CLI configuration.
        source = Path.home() / '.config/holycrab/config.json'
        self.secret = json.loads(source.read_text())['apiKey']
        self.cli('setup', 'setup', '--stdin', input=self.secret + '\n')
        status = self.cli('auth status', 'auth', 'status')[-1]
        if not status.get('valid') or status.get('account', {}).get('username') != 'user1':
            raise RuntimeError('Expected user1; no writes allowed')
        self.cli('auth clear-key (isolated config)', 'auth', 'clear-key', structured=False)
        self.cli('auth status after clear', 'auth', 'status', expected=(1,))
        self.cli('auth set-key compatibility', 'auth', 'set-key', '--stdin', input=self.secret + '\n')
        self.cli('version', '--version', structured=False)
        self.cli('help', '--help', structured=False)
        self.cli('doctor', 'doctor')
        self.cli('doctor json', 'doctor', '--json')
        self.cli('doctor online', 'doctor', '--json', '--online')
        self.cli('models list', 'models', 'list', '--json')
        for model in ('seedream-5-0-lite-260128', 'dreamina-seedance-2-5-260628', 'seed-audio-1.0'):
            self.cli('models show ' + model, 'models', 'show', model)
        self.state['balanceBefore'] = self.cli('credits balance', 'credits', 'balance')[-1]
        self.cli('tasks list before', 'tasks', 'list', '--page-size', '5')
        self.cli('tasks list pagination', 'tasks', 'list', '--page', '2', '--page-size', '1')
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        self.cli('tasks list filters', 'tasks', 'list', '--start-date', today, '--end-date', today, '--type', 'IMAGE')
        self.cli('invalid date rejected', 'tasks', 'list', '--start-date', 'bad', expected=(2,), structured=False)
        self.cli('update check', 'update', '--check')
        self.cli('update no downgrade', 'update')
        for name, arguments in [('cli_status', {}), ('account_get', {}), ('capabilities_list', {}),
                                ('capability_get', {'model': 'seedream-5-0-lite-260128'}), ('generation_list', {'pageSize': 5})]:
            self.mcp(name, arguments)
        files = self.root / 'files'
        files.mkdir(exist_ok=True)
        original = Path.home() / 'Desktop/test_img/懵懵.jpeg'
        self.state['sourceSha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        for filename in ('懵懵.jpeg', 'cat-copy.jpeg', 'cat-mcp.jpeg'):
            target = files / filename
            if not target.exists():
                shutil.copy2(original, target)
        paths = [str(files / name) for name in ('懵懵.jpeg', 'cat-copy.jpeg')]
        self.cli('upload preview without confirmation', 'assets', 'upload', *paths, expected=(2,))
        batch = self.once('cli-upload-two', lambda: self.cli('assets upload batch', 'assets', 'upload', *paths, '--yes')[-1])
        if batch.get('failedOrUnknown') or len(batch.get('uploaded', [])) != 2:
            raise RuntimeError('Batch did not fully succeed; never repeat upload')
        plan = self.once('mcp-prepare', lambda: self.mcp('asset_upload_prepare', {'files': [str(files / 'cat-mcp.jpeg')]}))
        mcp_batch = self.once('mcp-upload-one', lambda: self.mcp('asset_upload_execute', {'uploadPlanId': plan['uploadPlanId'], 'confirmed': True}))
        if mcp_batch.get('failedOrUnknown') or len(mcp_batch.get('uploaded', [])) != 1:
            raise RuntimeError('MCP upload did not succeed; never repeat upload')
        self.state['assets'] = [x['assetUniqId'] for x in batch['uploaded'] + mcp_batch['uploaded']]
        self.save()
        self.cli('assets wait', 'assets', 'wait', *self.state['assets'], '--timeout', '60', '--interval', '2', timeout=200)
        self.state['asset'] = self.cli('assets get', 'assets', 'get', self.state['assets'][0])[-1]
        self.mcp('asset_get', {'assetId': self.state['assets'][0]})
        self.save()

    def create(self):
        asset = self.cli('asset readiness before generation', 'assets', 'get', self.state['assets'][0])[-1]
        if not asset.get('ready'):
            raise RuntimeError('Asset is not ready')
        requests = {
            'image': {'model': 'seedream-5-0-lite-260128', 'prompt': '参考图片中的灰白猫，制作一张简洁明亮的宠物摄影海报，保留猫咪外观，无文字，无人物。',
                      'size': '2K', 'imageUrls': [asset['url']]},
            'video': {'model': 'dreamina-seedance-2-5-260628', 'prompt': '参考图中的灰白猫自然地眨眼，轻轻转头，镜头固定，柔和光线，无人物，无文字。',
                      'duration': 4, 'resolution': '480p', 'ratio': '16:9', 'videoTaskType': 'reference', 'imageAssetIds': [self.state['assets'][0]]},
            'audio': {'textPrompt': '午后的阳光落在窗边，小猫轻轻眨了眨眼，享受这一刻的安静。',
                      'audioConfig': {'format': 'mp3', 'sample_rate': 24000}},
        }
        self.state['requests'] = requests
        self.state.setdefault('estimates', {})
        self.save()
        for kind, request in requests.items():
            self.state['estimates'][kind] = self.cli(kind + ' estimate', 'generate', 'estimate', '--kind', kind, '--json', json.dumps(request))[-1]
            self.save()
        self.cli('credits estimate compatibility', 'credits', 'estimate', '--kind', 'image', '--json', json.dumps(requests['image']))
        self.mcp('generation_estimate', {'kind': 'audio', 'request': requests['audio']})
        for kind, request in requests.items():
            attempt = 'release-v044-' + kind + '-' + self.root.name.rsplit('.', 1)[-1]
            if kind == 'audio':
                action = lambda: self.mcp('generation_create', {'kind': kind, 'request': request, 'confirmed': True, 'attemptId': attempt})
            else:
                action = lambda: self.cli(kind + ' create', 'generate', 'create', '--kind', kind, '--json', json.dumps(request), '--yes', '--attempt-id', attempt)[-1]
            result = self.once('generation-' + kind, action)
            if result.get('state') != 'created' or not result.get('taskId'):
                raise RuntimeError(kind + ' creation not confirmed; no resubmission')
            self.state['tasks'][kind] = result['taskId']
            self.save()
            self.cli(kind + ' attempt get', 'generate', 'attempts', 'get', attempt)
            self.mcp('generation_attempt_get', {'attemptId': attempt})
        self.cli('attempts list', 'generate', 'attempts', 'list')
        self.mcp('generation_attempt_list', {})

    def check(self):
        outputs = self.root / 'outputs'
        outputs.mkdir(exist_ok=True)
        complete = True
        for kind, task in self.state['tasks'].items():
            self.cli(kind + ' task get', 'tasks', 'get', task)
            data = self.mcp('generation_get', {'taskId': task})
            self.cli(kind + ' task wait', 'tasks', 'wait', task, '--timeout', '1', '--interval', '1', expected=(0, 1, 2))
            step = data.get('step')
            if step == 3:
                raise RuntimeError(kind + ' task failed; no replacement task will be created')
            if step != 2:
                complete = False
                continue
            output = outputs / {'image': 'cat.jpg', 'video': 'cat.mp4', 'audio': 'cat.mp3'}[kind]
            if not output.exists():
                self.cli(kind + ' download', 'download', task, '--output', str(output))
            self.cli(kind + ' download overwrite blocked', 'download', task, '--output', str(output), expected=(1,), structured=False)
            self.cli(kind + ' download force', 'download', task, '--output', str(output), '--force')
            if sys.platform == 'darwin':
                converted = outputs / ('decoded-' + kind + {'image': '.png', 'audio': '.wav', 'video': '.mov'}[kind])
                commands = {
                    'image': ['sips', '-s', 'format', 'png', str(output), '--out', str(converted)],
                    'audio': ['afconvert', '-f', 'WAVE', '-d', 'LEI16', str(output), str(converted)],
                    'video': ['avconvert', '--source', str(output), '--preset', 'Preset640x480', '--output', str(converted), '--replace'],
                }
                checked = subprocess.run(commands[kind], capture_output=True, text=True, timeout=120)
                assert checked.returncode == 0 and converted.stat().st_size > 100, 'Native media decoding failed'
            else:
                checked = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output)], capture_output=True, text=True)
                decoded = json.loads(checked.stdout) if checked.returncode == 0 else {}
                expected_type = 'audio' if kind == 'audio' else 'video'
                assert any(s.get('codec_type') == expected_type for s in decoded.get('streams', [])), 'Media verification failed'
                if kind != 'image':
                    assert float(decoded['format']['duration']) > 0
            self.record(kind + ' media decode', True)
        self.state['balanceAfter'] = self.cli('credits balance after', 'credits', 'balance')[-1]
        self.state['businessComplete'] = complete and len(self.state['tasks']) == 3
        self.save()
        print('Business flow complete: ' + str(self.state['businessComplete']), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'create', 'check'])
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    run = LiveRun(args.root)
    getattr(run, args.phase)()


if __name__ == '__main__':
    main()
