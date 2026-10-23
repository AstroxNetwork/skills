"""User-created request files, including PowerShell 5.1 redirection output."""
import codecs
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_reliability_v060 import cli


class RequestEncodingTests(unittest.TestCase):
    def test_unicode_request_files_preserve_exact_text(self):
        request = {"model": "berry-1.0", "prompt": "中文 🦀 & % !", "audio": False}
        text = json.dumps(request, ensure_ascii=False)
        encodings = (text.encode("utf-8"), codecs.BOM_UTF8 + text.encode("utf-8"),
                     codecs.BOM_UTF16_LE + text.encode("utf-16-le"),
                     codecs.BOM_UTF16_BE + text.encode("utf-16-be"))
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "请求 with spaces.json"
            for raw in encodings:
                with self.subTest(prefix=raw[:4]):
                    source.write_bytes(raw)
                    self.assertEqual(cli.parse_json_argument("@" + str(source)), request)

    def test_invalid_encoding_has_repair_guidance_without_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bad.json"
            source.write_bytes(b'{"prompt":"private text\xff"}')
            with self.assertRaisesRegex(ValueError, "UTF-8.*UTF-16") as raised:
                cli.parse_json_argument("@" + str(source))
            self.assertNotIn("private text", str(raised.exception))

    def test_inline_json_and_top_level_object_contract(self):
        self.assertEqual(cli.parse_json_argument('{"audio":false}'), {"audio": False})
        with self.assertRaises(SystemExit):
            cli.parse_json_argument("[]")
        with self.assertRaises(json.JSONDecodeError):
            cli.parse_json_argument("{bad}")
