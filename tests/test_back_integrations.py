import base64
import asyncio
import hashlib
import hmac
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import BackgroundTasks, HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import back  # noqa: E402


class BackIntegrationTests(unittest.TestCase):
    @staticmethod
    def request_for(body: bytes, signature: str) -> Request:
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/line/webhook",
            "headers": [(b"x-line-signature", signature.encode())],
        }
        return Request(scope, receive)

    def test_vision_upload_uses_shared_ingestion(self):
        result = {
            "restaurant_name": "測試餐廳",
            "source_type": "menu",
            "confidence": 0.9,
            "categories": [{"name": "主餐", "items": [{"name": "牛肉麵", "price": 180}]}],
            "warnings": [],
        }
        with mock.patch.object(back, "analyze_menu_image", return_value=result) as analyze, mock.patch.object(
            back, "_register_vision_menu"
        ) as register:
            upload = UploadFile(io.BytesIO(b"jpeg-bytes"), filename="menu.jpg", headers=Headers({"content-type": "image/jpeg"}))
            response = asyncio.run(back.create_menu_from_photo("測試餐廳", upload))
        self.assertEqual(response["itemCount"], 1)
        self.assertTrue(response["analysisId"])
        register.assert_not_called()
        self.assertEqual(analyze.call_args.args[2], "測試餐廳")

    def test_vision_confirm_persists_pending_analysis(self):
        result = {
            "restaurant_name": "測試餐廳",
            "detected_restaurant_name": "測試餐廳",
            "categories": [{"name": "主餐", "items": [{"name": "牛肉麵", "price": 180}]}],
            "quality": {"score": 0.9, "priceCoverage": 1, "itemCount": 1},
            "conflicts": [],
            "identityConflict": False,
        }
        analysis_id = back._store_pending_analysis(result)
        with mock.patch.object(
            back,
            "_register_vision_menu",
            return_value={"restaurantName": "測試餐廳", "itemCount": 1, "categories": ["主餐"]},
        ) as register:
            response = back.confirm_menu_from_photo(
                analysis_id, back.VisionConfirmReq(restaurant_name="測試餐廳")
            )
        self.assertTrue(response["success"])
        register.assert_called_once()

    def test_vision_confirm_requires_explicit_conflict_acceptance(self):
        result = {
            "restaurant_name": "東海愛將",
            "detected_restaurant_name": "犇頂牛排",
            "categories": [{"name": "排餐", "items": [{"name": "犇頂牛排", "price": 230}]}],
            "quality": {"score": 0.9, "priceCoverage": 1, "itemCount": 1},
            "conflicts": [],
            "identityConflict": True,
        }
        analysis_id = back._store_pending_analysis(result)
        with self.assertRaises(HTTPException) as caught:
            back.confirm_menu_from_photo(analysis_id, back.VisionConfirmReq(restaurant_name="東海愛將"))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn(analysis_id, back.PENDING_ANALYSES)

    def test_pending_vision_api_can_be_human_corrected(self):
        result = {
            "restaurant_name": "便當店",
            "categories": [{"name": "飯類", "items": [{"name": "豬肝飯", "price": 80}]}],
            "quality": {"score": 0.7, "priceCoverage": 1, "itemCount": 1},
            "conflicts": [],
        }
        analysis_id = back._store_pending_analysis(result)
        response = back.correct_menu_from_photo(
            analysis_id,
            back.VisionCorrectionReq(instruction="把豬肝飯改成豬腳飯"),
        )
        self.assertTrue(response["success"])
        self.assertEqual(response["categories"][0]["items"][0]["name"], "豬腳飯")
        self.assertTrue(response["quality"]["humanReviewed"])
        self.assertIn(analysis_id, back.PENDING_ANALYSES)

    def test_line_webhook_rejects_bad_signature(self):
        with mock.patch.dict(os.environ, {"LINE_CHANNEL_SECRET": "secret", "LINE_CHANNEL_ACCESS_TOKEN": "token"}):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(back.line_webhook(self.request_for(b'{"events":[]}', "bad"), BackgroundTasks()))
        self.assertEqual(caught.exception.status_code, 400)

    def test_line_webhook_accepts_empty_verification_event(self):
        body = json.dumps({"events": []}, separators=(",", ":")).encode()
        signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
        with mock.patch.dict(os.environ, {"LINE_CHANNEL_SECRET": "secret", "LINE_CHANNEL_ACCESS_TOKEN": "token"}):
            response = asyncio.run(back.line_webhook(self.request_for(body, signature), BackgroundTasks()))
        self.assertEqual(response, {"ok": True})

    def test_line_recommendation_uses_remembered_restaurant(self):
        source = {"type": "user", "userId": "U-recommend"}
        restaurant_name = "測試推薦餐廳"
        restaurant_menu = {"restaurants": {restaurant_name: {"name": restaurant_name, "categories": {}}}}
        old_menu = back.RESTAURANT_MENUS.get(restaurant_name)
        old_state = back.LINE_STATES.get("U-recommend")
        back.RESTAURANT_MENUS[restaurant_name] = restaurant_menu
        back.LINE_STATES["U-recommend"] = {"restaurant_name": restaurant_name}
        try:
            self.assertEqual(back._find_line_restaurant("推薦我晚餐", back.LINE_STATES["U-recommend"]), restaurant_name)
            with mock.patch.object(back, "generate_conversation", return_value=("推薦大麥克", [])), mock.patch.object(
                back, "push_text"
            ) as pushed:
                back._line_process_recommendation(source, "推薦我晚餐", restaurant_name)
            pushed.assert_called_once_with("U-recommend", "推薦大麥克", mock.ANY)
        finally:
            if old_menu is None:
                back.RESTAURANT_MENUS.pop(restaurant_name, None)
            else:
                back.RESTAURANT_MENUS[restaurant_name] = old_menu
            if old_state is None:
                back.LINE_STATES.pop("U-recommend", None)
            else:
                back.LINE_STATES["U-recommend"] = old_state

    def test_line_pending_result_requires_confirm_command(self):
        user_id = "U-confirm"
        event_id = "event-confirm-unique"
        pending = {
            "restaurant_name": "犇頂牛排",
            "detected_restaurant_name": "犇頂牛排",
            "categories": [{"name": "排餐", "items": [{"name": "犇頂牛排", "price": 230}]}],
            "quality": {"score": 1, "priceCoverage": 1, "itemCount": 1},
        }
        back.LINE_STATES[user_id] = {
            "restaurant_name": "犇頂牛排",
            "pending_result": pending,
            "pending_expires_at": time.time() + 900,
        }
        body = json.dumps({
            "events": [{
                "type": "message",
                "replyToken": "reply-token",
                "webhookEventId": event_id,
                "source": {"type": "user", "userId": user_id},
                "message": {"type": "text", "text": "確認"},
            }]
        }, separators=(",", ":")).encode()
        signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
        try:
            with mock.patch.dict(os.environ, {"LINE_CHANNEL_SECRET": "secret", "LINE_CHANNEL_ACCESS_TOKEN": "token"}), mock.patch.object(
                back, "_register_vision_menu", return_value={"restaurantName": "犇頂牛排", "itemCount": 1, "categories": ["排餐"]}
            ) as register, mock.patch.object(back, "reply_text") as reply:
                response = asyncio.run(back.line_webhook(self.request_for(body, signature), BackgroundTasks()))
            self.assertEqual(response, {"ok": True})
            register.assert_called_once()
            self.assertIn("已確認並建立", reply.call_args.args[1])
            self.assertNotIn("pending_result", back.LINE_STATES[user_id])
        finally:
            back.LINE_STATES.pop(user_id, None)
            back.LINE_EVENT_IDS.pop(event_id, None)

    def test_line_pending_menu_accepts_human_correction(self):
        user_id = "U-correct"
        event_id = "event-correct-unique"
        pending = {
            "restaurant_name": "便當店",
            "categories": [{"name": "飯類", "items": [{"name": "豬肝飯", "price": 80}]}],
            "quality": {"score": 0.7, "priceCoverage": 1, "itemCount": 1},
            "conflicts": [],
        }
        back.LINE_STATES[user_id] = {
            "restaurant_name": "便當店",
            "pending_result": pending,
            "pending_expires_at": time.time() + 900,
        }
        body = json.dumps({
            "events": [{
                "type": "message",
                "replyToken": "reply-token",
                "webhookEventId": event_id,
                "source": {"type": "user", "userId": user_id},
                "message": {"type": "text", "text": "豬肝飯 改豬腳飯"},
            }]
        }, separators=(",", ":")).encode()
        signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
        try:
            with mock.patch.dict(os.environ, {"LINE_CHANNEL_SECRET": "secret", "LINE_CHANNEL_ACCESS_TOKEN": "token"}), mock.patch.object(
                back, "reply_text"
            ) as reply:
                response = asyncio.run(back.line_webhook(self.request_for(body, signature), BackgroundTasks()))
            self.assertEqual(response, {"ok": True})
            self.assertEqual(pending["categories"][0]["items"][0]["name"], "豬腳飯")
            self.assertIn("已把「豬肝飯」改成「豬腳飯」", reply.call_args.args[1])
            self.assertIn("pending_result", back.LINE_STATES[user_id])
        finally:
            back.LINE_STATES.pop(user_id, None)
            back.LINE_EVENT_IDS.pop(event_id, None)


if __name__ == "__main__":
    unittest.main()
