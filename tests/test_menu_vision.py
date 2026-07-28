import json
import io
import sys
import unittest
from pathlib import Path

from PIL import Image


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import menu_vision  # noqa: E402


class MenuVisionTests(unittest.TestCase):
    def test_normalizes_categories_prices_and_duplicates(self):
        result = menu_vision.normalize_vision_result(
            {
                "restaurant_name": "照片店名",
                "source_type": "menu",
                "confidence": 1.4,
                "categories": [
                    {"name": "主餐", "items": [
                        {"name": "牛肉麵", "price": "NT$ 180"},
                        {"name": "牛肉麵", "price": 999},
                        {"name": "滷肉飯", "price": "時價"},
                    ]},
                ],
            },
            "使用者店名",
        )
        self.assertEqual(result["restaurant_name"], "使用者店名")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["categories"][0]["items"], [
            {"name": "牛肉麵", "price": 180.0},
            {"name": "滷肉飯", "price": None},
        ])

    def test_analyze_encodes_image_and_parses_fenced_json(self):
        captured = {}

        def fake_vision(prompt, image_url, timeout):
            captured.update(prompt=prompt, image_url=image_url, timeout=timeout)
            return "```json\n" + json.dumps({
                "restaurant_name": "測試餐廳",
                "source_type": "dish_display",
                "confidence": 0.88,
                "categories": [{"name": "現場菜色", "items": [{"name": "炒高麗菜", "price": None}]}],
                "warnings": ["價格未顯示"],
            }, ensure_ascii=False) + "\n```"

        result = menu_vision.analyze_menu_image(b"fake-jpeg", "image/jpeg", vision_func=fake_vision)
        self.assertTrue(captured["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(result["restaurant_name"], "測試餐廳")
        self.assertEqual(result["categories"][0]["items"][0]["name"], "炒高麗菜")

    def test_rejects_non_image_and_empty_result(self):
        with self.assertRaisesRegex(ValueError, "僅支援"):
            menu_vision.analyze_menu_image(b"x", "text/plain", vision_func=lambda *args, **kwargs: "{}")
        with self.assertRaisesRegex(ValueError, "沒有辨識到"):
            menu_vision.analyze_menu_image(b"x", "image/png", vision_func=lambda *args, **kwargs: "{}")

    def test_accepts_common_alternate_model_schemas(self):
        dict_categories = menu_vision.normalize_vision_result({
            "categories": {
                "飯類": [{"item_name": "雞腿飯", "amount": "$120"}],
                "飲料": ["紅茶 $30"],
            }
        })
        self.assertEqual(dict_categories["categories"][0]["items"][0]["name"], "雞腿飯")
        self.assertEqual(dict_categories["categories"][1]["items"][0]["price"], 30.0)

        top_level_list = menu_vision.normalize_vision_result([{"dish": "牛肉湯", "cost": 100}])
        self.assertEqual(top_level_list["categories"][0]["items"][0]["price"], 100.0)

    def test_retries_with_simpler_schema_when_first_pass_is_empty(self):
        responses = iter([
            '{"categories":[]}',
            '[{"name":"鍋燒意麵","price":90}]',
        ])

        def fake_vision(*args, **kwargs):
            return next(responses)

        result = menu_vision.analyze_menu_image(b"image", "image/jpeg", "小店", vision_func=fake_vision)
        self.assertEqual(result["restaurant_name"], "小店")
        self.assertEqual(result["categories"][0]["items"][0]["name"], "鍋燒意麵")

    def test_second_pass_verification_replaces_inaccurate_draft(self):
        responses = iter([
            '{"restaurant_name":"麥當勞","categories":[{"name":"主餐","items":[{"name":"大麥克克","price":99}]}]}',
            '{"restaurant_name":"麥當勞","categories":[{"name":"主餐","items":[{"name":"大麥克","price":80},{"name":"麥香雞","price":49}]}]}',
        ])

        result = menu_vision.analyze_menu_image(
            b"image",
            "image/jpeg",
            vision_func=lambda *args, **kwargs: next(responses),
        )
        self.assertEqual(
            [item["name"] for item in result["categories"][0]["items"]],
            ["大麥克", "麥香雞"],
        )

    def test_persisted_document_retains_categories(self):
        document = menu_vision.to_persisted_document({
            "restaurant_name": "店",
            "categories": [{"name": "飲料", "items": [{"name": "紅茶", "price": 30.0}]}],
        })
        self.assertEqual(document["menu_items"][0]["category"], "飲料")
        self.assertEqual(document["schemaVersion"], 2)
        self.assertTrue(document["confirmedAt"])

    def test_exif_orientation_and_overlapping_two_by_two_tiles(self):
        image = Image.new("RGB", (2000, 1800), "white")
        exif = Image.Exif()
        exif[274] = 1
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", exif=exif)
        _, regions, size = menu_vision.prepare_image_regions(buffer.getvalue())
        self.assertEqual(size, (2000, 1800))
        self.assertEqual(len(regions), 4)
        self.assertEqual(regions[0]["box"], [0, 0, 1120, 1008])
        self.assertEqual(regions[1]["box"], [880, 0, 2000, 1008])
        self.assertEqual(regions[1]["box"][0] - regions[0]["box"][2], -240)

    def test_low_resolution_messaging_copy_is_recovered_before_tiling(self):
        image = Image.new("RGB", (841, 607), "white")
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        _, regions, size = menu_vision.prepare_image_regions(buffer.getvalue())
        self.assertEqual(size[0], 2000)
        self.assertEqual(len(regions), 4)

    def test_merge_requires_matching_price_and_reports_conflict(self):
        results = [
            {"id": "tile-1", "box": [0, 0, 100, 100], "categories": [{"name": "牛排", "items": [{"name": "犇頂牛排", "price": 230}]}]},
            {"id": "tile-2", "box": [80, 0, 180, 100], "categories": [{"name": "牛排", "items": [{"name": "犇頂牛排", "price": 330}]}]},
        ]
        merged, conflicts = menu_vision.merge_region_items(results)
        self.assertEqual(len(merged), 2)
        self.assertEqual(conflicts[0]["type"], "price")

    def test_dense_image_routes_overview_tiles_and_verifier(self):
        image = Image.new("RGB", (2000, 1800), "white")
        buffer = io.BytesIO()
        image.save(buffer, "JPEG")
        calls = []

        def fake_vision(prompt, image_url, model=None, timeout=0, temperature=None):
            calls.append((prompt, image_url, model, temperature))
            if "版面與身分" in prompt:
                return '{"restaurant_name":"犇頂牛排","source_type":"menu","menu_type":"牛排館"}'
            if "最終校對員" in prompt:
                return '{"restaurant_name":"犇頂牛排","categories":[{"name":"排餐","items":[{"name":"犇頂牛排","price":230},{"name":"菲力牛排","price":360},{"name":"香煎中卷","price":280}]}]}'
            return '{"categories":[{"name":"排餐","items":[{"name":"犇頂牛排","price":230},{"name":"菲力牛排","price":360}]}]}'

        result = menu_vision.analyze_menu_image(
            buffer.getvalue(), "image/jpeg", "東海愛將", vision_func=fake_vision
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[0][2], "mistral-small-4")
        self.assertTrue(all(call[3] == 0 for call in calls))
        self.assertIsInstance(calls[-1][1], list)
        self.assertEqual(len(calls[-1][1]), 4)
        self.assertEqual(result["detected_restaurant_name"], "犇頂牛排")
        self.assertTrue(result["identityConflict"])
        self.assertEqual(result["quality"]["priceCoverage"], 1.0)

    def test_human_can_correct_ocr_name_and_price_before_confirmation(self):
        result = {
            "categories": [{"name": "飯類", "items": [
                {"name": "豬肝飯", "price": 80},
                {"name": "雞腿飯", "price": None},
            ]}],
            "quality": {"itemCount": 2, "priceCoverage": 0.5},
            "conflicts": [{"candidates": ["豬肝飯", "豬腳飯"], "resolved": False}],
        }
        correction = menu_vision.apply_manual_correction(result, "把豬肝飯改成豬腳飯")
        self.assertIn("豬腳飯", correction["message"])
        self.assertEqual(result["categories"][0]["items"][0]["name"], "豬腳飯")
        self.assertTrue(result["conflicts"][0]["resolved"])
        menu_vision.apply_manual_correction(result, "把雞腿飯價格改成90")
        self.assertEqual(result["categories"][0]["items"][1]["price"], 90)
        self.assertEqual(result["quality"]["priceCoverage"], 1.0)
        self.assertTrue(result["quality"]["humanReviewed"])
        persisted = menu_vision.to_persisted_document(result)
        self.assertEqual(len(persisted["manualCorrections"]), 2)

    def test_human_can_correct_by_preview_number(self):
        result = {"categories": [{"name": "飯類", "items": [
            {"name": "排骨飯", "price": 90}, {"name": "豬肝飯", "price": 80}
        ]}]}
        preview = menu_vision.format_menu_preview(result)
        self.assertIn("2. [飯類] 豬肝飯", preview)
        menu_vision.apply_manual_correction(result, "改 2 菜名 豬腳飯")
        self.assertEqual(result["categories"][0]["items"][1]["name"], "豬腳飯")
        menu_vision.apply_manual_correction(result, "改 2 價格 95")
        self.assertEqual(result["categories"][0]["items"][1]["price"], 95)

    def test_natural_correction_with_plain_change_word_is_detected(self):
        self.assertTrue(menu_vision.looks_like_manual_correction("特製炒醬麵 改特製炸醬麵"))
        result = {"categories": [{"name": "麵類", "items": [{"name": "特製炒醬麵", "price": 80}]}]}
        menu_vision.apply_manual_correction(result, "特製炒醬麵 改特製炸醬麵")
        self.assertEqual(result["categories"][0]["items"][0]["name"], "特製炸醬麵")

    def test_unique_fuzzy_ocr_name_can_be_corrected_without_number(self):
        result = {"categories": [{"name": "麵類", "items": [
            {"name": "特製炒將麵", "price": 80},
            {"name": "牛肉湯麵", "price": 100},
        ]}]}
        correction = menu_vision.apply_manual_correction(result, "把特製炒醬麵改成特製炸醬麵")
        self.assertEqual(result["categories"][0]["items"][0]["name"], "特製炸醬麵")
        self.assertIn("近似品名", correction["message"])

    def test_unknown_manual_correction_does_not_mutate_menu(self):
        result = {"categories": [{"name": "飯類", "items": [{"name": "排骨飯", "price": 90}]}]}
        with self.assertRaisesRegex(ValueError, "找不到品項"):
            menu_vision.apply_manual_correction(result, "把豬肝飯改成豬腳飯")
        self.assertEqual(result["categories"][0]["items"][0]["name"], "排骨飯")


if __name__ == "__main__":
    unittest.main()
