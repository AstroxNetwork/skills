"""Exercise HTTPResponse EOF semantics, not a mock which invents read behavior."""
import argparse
import http.client
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

try:
    from .test_reliability_v060 import cli
except ImportError:
    from test_reliability_v060 import cli


class DownloadIntegrityTests(unittest.TestCase):
    def download(self, destination, body, header, *, force=False):
        try:
            from .network_fixtures import getaddrinfo
        except ImportError:
            from network_fixtures import getaddrinfo
        class Socket:
            def makefile(self, mode):
                return io.BytesIO(b"HTTP/1.1 200 OK\r\n" + header + b"\r\n" + body)
        response = http.client.HTTPResponse(Socket())
        response.begin()
        output = io.StringIO()
        task = {"code": 200, "data": {"uniqId": "task1", "videoUrl": "https://cdn.example/output.mp4"}}
        with patch.object(cli._owner("send"), "send", return_value=(200, task)), \
                patch.object(cli.socket, "getaddrinfo", side_effect=getaddrinfo), \
                patch.object(cli._owner("open_download"), "open_download", return_value=response), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            cli.command_download(argparse.Namespace(uniq_id="task1", index=0, output=str(destination), force=force))
        return json.loads(output.getvalue())

    def test_premature_eof_never_creates_or_replaces_output(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as root:
                destination = Path(root) / "output.mp4"
                if existing:
                    destination.write_bytes(b"keep the previous complete video")
                with self.assertRaisesRegex(SystemExit, "expected 10 bytes, received 3"):
                    self.download(destination, b"abc", b"Content-Length: 10\r\n", force=existing)
                if existing:
                    self.assertEqual(destination.read_bytes(), b"keep the previous complete video")
                else:
                    self.assertFalse(destination.exists())
                self.assertEqual(list(Path(root).glob("*.part")), [])

    def test_complete_fixed_length_and_chunked_downloads_succeed(self):
        fixtures = [(b"abc", b"Content-Length: 3\r\n"),
                    (b"3\r\nabc\r\n0\r\n\r\n", b"Transfer-Encoding: chunked\r\n")]
        for body, header in fixtures:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as root:
                destination = Path(root) / "output.mp4"
                result = self.download(destination, body, header)
                self.assertEqual(result["bytes"], 3)
                self.assertEqual(destination.read_bytes(), b"abc")

    def test_incomplete_chunked_download_never_replaces_output(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "output.mp4"
            destination.write_bytes(b"original")
            with self.assertRaises(http.client.IncompleteRead):
                self.download(destination, b"5\r\nabc", b"Transfer-Encoding: chunked\r\n", force=True)
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(list(Path(root).glob("*.part")), [])
