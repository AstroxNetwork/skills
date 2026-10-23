"""Actual loopback HTTP transport for installed CLI subprocesses, never real accounts."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = r'''
import os, sys, socket, urllib.parse
endpoint = os.environ['HOLYCRAB_TEST_HTTP_ENDPOINT']
origin = urllib.parse.urlsplit(endpoint)
def offline(event, args):
    if event == 'socket.getaddrinfo':
        if args[0] != '127.0.0.1' or int(args[1]) != origin.port:
            raise RuntimeError('Test blocked non-fixture DNS')
    elif event in {'socket.connect', 'socket.connect_ex'}:
        if args[1] != ('127.0.0.1', origin.port):
            raise RuntimeError('Test blocked non-fixture connection')
    elif event == 'socket.sendto':
        raise RuntimeError('Test blocked datagram')
sys.addaudithook(offline)
from holycrab_runtime import settings, transport
settings.DEFAULT_BASE_URL = endpoint
if os.environ.get('HOLYCRAB_TEST_HTTP_TIMEOUT'):
    original_open = transport.urllib.request.OpenerDirector.open
    def fast_timeout(self, fullurl, data=None, timeout=60):
        return original_open(self, fullurl, data, timeout=float(os.environ['HOLYCRAB_TEST_HTTP_TIMEOUT']))
    transport.urllib.request.OpenerDirector.open = fast_timeout
# Only test-owned loopback output/storage URLs bypass production HTTPS/private-IP
# policy. Production URL-policy behavior is independently tested, not mocked here.
for name in ('validate_download_url', 'validate_presigned_upload_url'):
    original = getattr(transport, name)
    def local_url(value, original=original):
        if isinstance(value, str) and value.startswith(endpoint + '/'):
            return value
        return original(value)
    setattr(transport, name, local_url)
'''


class Service:
    def __init__(self):
        self.calls = []
        self.mode = 'success'
        self.barrier = None
        self.release = threading.Event()
        self.entered = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                body = json.loads(raw) if raw and self.headers.get('Content-Type', '').startswith('application/json') else raw.decode('utf-8', errors='replace')
                path = self.path.split('?', 1)[0]
                owner.calls.append({'method': self.command, 'path': self.path, 'body': body,
                                    'token': self.headers.get('X-User-Token')})
                if owner.mode == 'reject' and not path.startswith('/output'):
                    return self.respond({'code': 422, 'errorCode': 'ASSET_RULE', 'requestId': 'request1',
                        'message': 'Material rejected', 'errors': [{'assetId': 'asset1', 'field': 'durationSeconds',
                        'requestPath': 'audioAssetIds[0]', 'actual': 0, 'rule': {'min': 2}, 'retryable': False}]}, 422)
                if path.endswith('/freeze-credit'):
                    if owner.mode == 'invalid-json':
                        body = b'{invalid response'
                        self.send_response(200)
                        self.send_header('Content-Length', str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if owner.barrier:
                        owner.barrier.wait(timeout=10)
                    return self.respond({'code': 200, 'data': {'frozenCredit': 7}})
                if owner.mode == 'upload-block' and path == '/storage' and sum(c['path'] == '/storage' for c in owner.calls) == 1:
                    owner.entered.set()
                    owner.release.wait(timeout=15)
                if owner.mode == 'register-disconnect' and path == '/api/user-assets/upload':
                    self.close_connection = True
                    return
                if path.endswith('-generation') or path == '/api/tasks/generation':
                    if owner.mode == 'slow-create':
                        time.sleep(1.5)
                    if owner.mode in {'disconnect', 'block'}:
                        owner.entered.set()
                        if owner.mode == 'block':
                            owner.release.wait(timeout=15)
                        self.close_connection = True
                        return
                    return self.respond({'code': 200, 'data': {'uniqId': 'task1'}})
                if path.startswith('/output'):
                    if owner.mode == 'redirect':
                        self.send_response(302)
                        self.send_header('Location', owner.endpoint + '/output/final')
                        self.end_headers()
                        owner.mode = 'success'
                        return
                    data = b'complete video bytes'
                    self.send_response(206 if owner.mode == 'partial' else 200)
                    if owner.mode == 'partial': self.send_header('Content-Range', 'bytes 0-19/100')
                    self.send_header('Content-Length', str(len(data) + (3 if owner.mode == 'truncated' else 0)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == '/api/user/me': data = {'credit': 100, 'username': 'Fixture'}
                elif path == '/api/tasks': data = {'records': [{'uniqId': 'task1', 'step': 2}], 'total': 1, 'pages': 1}
                elif path == '/api/tasks/task1': data = {'uniqId': 'task1', 'step': 2, 'videoUrl': owner.endpoint + '/output/video'}
                elif path == '/api/user-assets/pre-signed-download-url': data = {'uniqId': 'asset1', 'preSignedUrl': owner.endpoint + '/storage', 'objectKey': 'private-object'}
                elif path in {'/storage', '/api/user-assets/upload'}: data = True
                elif path.startswith('/api/user-assets/asset1'): data = {'uniqId': 'asset1', 'step': 'UPLOADED_TO_ARK', 'metadataStatus': 'PENDING', 'durationSeconds': 1.25}
                elif path == '/api/real-human-authorizations/sessions': data = {'authorizationId': 'auth1', 'h5Link': 'https://www.byteplus.com/en/liveness-face-manage/authorization?token=fixture', 'expiresAt': '2099-01-01T00:00:00Z'}
                elif path == '/api/real-human-authorizations/auth1': data = {'authorizationId': 'auth1', 'status': 'SUCCEEDED', 'group': {'uniqId': 'group1', 'name': 'Fixture'}}
                elif self.command == 'DELETE': data = True
                elif self.command == 'PATCH': data = {'uniqId': 'group1', 'name': body['name']}
                elif path == '/api/real-human-groups': data = {'records': [{'uniqId': 'group1', 'name': 'Fixture', 'assetCount': 1}], 'total': 1, 'pages': 1}
                elif path == '/api/real-human-groups/group1/assets': data = {'records': [{'uniqId': 'asset1', 'step': 'UPLOADED_TO_ARK'}], 'total': 1, 'pages': 1}
                else:
                    return self.respond({'code': 404, 'message': 'No fixture for ' + path}, 404)
                self.respond({'code': 200, 'data': data})
            def respond(self, data, status=200):
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # Windows reports the deliberately timed-out client as 10053.
                    pass
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.endpoint = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def environment(directory: Path, library: Path, endpoint: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    guard = directory / 'bootstrap'
    guard.mkdir(exist_ok=True)
    (guard / 'sitecustomize.py').write_text(BOOTSTRAP, encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('HOLYCRAB_', 'CODEX_', 'CLAUDE_'))
           and k not in {'PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'}}
    env.update({'HOLYCRAB_CONFIG_DIR': str(directory / 'config'), 'HOLYCRAB_API_KEY': 'fixture-api-key',
                'HOLYCRAB_NO_UPDATE_CHECK': '1', 'HOLYCRAB_TEST_HTTP_ENDPOINT': endpoint,
                'PYTHONPATH': os.pathsep.join((str(guard), str(library))), 'PYTHONUTF8': '1',
                'PYTHONDONTWRITEBYTECODE': '1', 'NO_PROXY': '127.0.0.1'})
    return env
