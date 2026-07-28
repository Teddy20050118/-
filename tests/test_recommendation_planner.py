import sys
import unittest
from pathlib import Path
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ollama_fuc  # noqa: E402


def steak_menu():
    items = [
        {"name": "犇頂牛排", "price": 230},
        {"name": "沙朗牛排", "price": 300},
        {"name": "丁骨牛排", "price": 330},
        {"name": "菲力牛排", "price": 360},
        {"name": "香煎雞排", "price": 240},
        {"name": "黃金豬排", "price": 270},
        {"name": "香煎中卷", "price": 280},
        {"name": "風味鮭魚", "price": 300},
    ]
    return {"restaurants": {"犇頂牛排": {"name": "犇頂牛排", "categories": {"排餐": {"items": items}}}}}


class RecommendationPlannerTests(unittest.TestCase):
    def deterministic(self, text, **prefs):
        with mock.patch.object(ollama_fuc, "_llm_plan", side_effect=RuntimeError("timeout")):
            return ollama_fuc.recommend(steak_menu(), {"notes": text, **prefs})

    def test_generic_dinner_is_one_beef_main(self):
        result = self.deterministic("推薦我晚餐")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["protein"], "beef")
        self.assertNotIn(result["items"][0]["name"], {"香煎中卷", "黃金豬排"})

    def test_budget_250_stays_in_range_and_selects_beef(self):
        result = self.deterministic("預算250", budget=250)
        self.assertEqual(result["items"][0]["name"], "犇頂牛排")
        self.assertLessEqual(result["items"][0]["price"], 250)

    def test_no_beef_excludes_all_beef(self):
        result = self.deterministic("不吃牛", excludes=["牛"])
        self.assertTrue(result["items"])
        self.assertTrue(all(item["protein"] != "beef" for item in result["items"]))

    def test_seafood_only_when_requested(self):
        result = self.deterministic("今天想吃海鮮")
        self.assertEqual(result["items"][0]["protein"], "seafood")

    def test_llm_ids_are_validated_and_one_person_is_capped(self):
        with mock.patch.object(
            ollama_fuc,
            "_llm_plan",
            return_value=["missing", "item-007", "item-002", "item-006"],
        ):
            result = ollama_fuc.recommend(steak_menu(), {"notes": "推薦我晚餐"})
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["protein"], "beef")

    def test_chat_respects_explicit_model_and_temperature(self):
        with mock.patch.object(ollama_fuc, "API_BASE_URL", "https://example.test/v1"), mock.patch.object(
            ollama_fuc, "API_KEY", "key"
        ), mock.patch.object(ollama_fuc, "_api_chat", return_value="ok") as api:
            ollama_fuc.chat([{"role": "user", "content": "hi"}], model="chosen", temperature=0.2)
        self.assertEqual(api.call_args.args[1], "chosen")
        self.assertEqual(api.call_args.kwargs["temperature"], 0.2)


if __name__ == "__main__":
    unittest.main()
