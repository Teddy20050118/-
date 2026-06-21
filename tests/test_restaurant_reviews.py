import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from restaurant_reviews import (  # noqa: E402
    analyze_review_signals,
    load_review_cache,
    refresh_restaurant_reviews,
    write_review_cache,
)


class RestaurantReviewsTest(unittest.TestCase):
    def test_load_review_cache_missing_returns_empty_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = load_review_cache(tmp, "不存在餐廳")

        self.assertFalse(report["success"])
        self.assertEqual(report["restaurantName"], "不存在餐廳")
        self.assertEqual(report["sentiment"], "unknown")
        self.assertEqual(report["sources"], [])

    def test_load_review_cache_existing_normalizes_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_review_cache(
                tmp,
                "測試餐廳",
                {
                    "success": True,
                    "restaurantName": "測試餐廳",
                    "overallScore": 88,
                    "sentiment": "positive",
                    "summary": "整體偏正面",
                    "pros": ["餐點具體"],
                    "cons": [],
                    "recommendedFor": ["想快速吃飯的人"],
                    "riskLevel": "low",
                    "riskReasons": [],
                    "sources": [{"title": "來源", "url": "https://example.com", "excerpt": "好吃", "sourceType": "web"}],
                },
            )
            report = load_review_cache(tmp, "測試餐廳")

        self.assertTrue(report["success"])
        self.assertEqual(report["overallScore"], 88)
        self.assertEqual(report["sources"][0]["title"], "來源")

    def test_analyze_review_signals_detects_incentive_review_risk(self):
        sources = [
            {
                "title": "某餐廳 評價",
                "url": "https://example.com/a",
                "excerpt": "五星送飲料，評論送小菜。好吃 讚 服務好 好吃 讚。",
                "sourceType": "web",
            },
            {
                "title": "某餐廳 心得",
                "url": "https://example.com/b",
                "excerpt": "五星送飲料，評論送小菜。好吃 讚 服務好 好吃 讚。",
                "sourceType": "blog",
            },
        ]

        signals = analyze_review_signals(sources)

        self.assertIn("五星送", signals["incentiveHits"])
        self.assertIn("評論送", signals["incentiveHits"])
        self.assertIn(signals["riskLevel"], {"medium", "high"})
        self.assertGreaterEqual(len(signals["riskReasons"]), 1)

    def test_analyze_review_signals_empty_sources_has_no_score(self):
        signals = analyze_review_signals([])

        self.assertEqual(signals["overallScore"], 0)
        self.assertEqual(signals["sentiment"], "unknown")
        self.assertIn("來源不足", signals["riskReasons"][0])

    def test_refresh_without_sources_returns_data_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("restaurant_reviews._collect_review_sources", return_value=[]):
                report = refresh_restaurant_reviews(tmp, "測試餐廳")

        self.assertFalse(report["success"])
        self.assertEqual(report["overallScore"], 0)
        self.assertEqual(report["message"], "找不到足夠的公開評價來源")

    def test_refresh_falls_back_when_llm_fails(self):
        fake_sources = [
            {
                "title": "測試餐廳 評價",
                "url": "https://example.com/review",
                "excerpt": "牛肉麵好吃，份量足，但等很久。",
                "sourceType": "web",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("restaurant_reviews._collect_review_sources", return_value=fake_sources):
                with mock.patch("restaurant_reviews.summarize_reviews", side_effect=RuntimeError("model down")):
                    report = refresh_restaurant_reviews(tmp, "測試餐廳")

            self.assertTrue(report["success"])
            self.assertIn("summary", report)
            self.assertTrue((Path(tmp) / "reviews_測試餐廳.json").exists())


if __name__ == "__main__":
    unittest.main()
