import base64
import hashlib
import hmac
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import line_bot  # noqa: E402


class LineBotTests(unittest.TestCase):
    def test_signature_uses_raw_body_hmac_sha256(self):
        body = b'{"events":[]}'
        secret = "channel-secret"
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        self.assertTrue(line_bot.verify_signature(body, signature, secret))
        self.assertFalse(line_bot.verify_signature(body + b" ", signature, secret))

    def test_extract_restaurant_name(self):
        self.assertEqual(line_bot.extract_restaurant_name("餐廳：大肥鵝"), "大肥鵝")
        self.assertEqual(line_bot.extract_restaurant_name(" 店家: 東海牛排 "), "東海牛排")
        self.assertEqual(line_bot.extract_restaurant_name("我想吃牛排"), "")

    def test_source_key_and_push_target(self):
        group = {"type": "group", "groupId": "G1", "userId": "U1"}
        self.assertEqual(line_bot.source_key(group), "U1")
        self.assertEqual(line_bot.push_target(group), "G1")


if __name__ == "__main__":
    unittest.main()
