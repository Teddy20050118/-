import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import restaurant_reviews as reviews  # noqa: E402


def source(
    source_id: str,
    text: str,
    *,
    source_type: str = "forum",
    quality: float = 0.8,
    published_at: str | None = None,
) -> dict:
    return {
        "sourceId": source_id,
        "title": f"測試來源 {source_id}",
        "url": f"https://example{source_id[-1]}.com/review",
        "canonicalUrl": f"https://example{source_id[-1]}.com/review",
        "excerpt": text,
        "content": text,
        "sourceType": source_type,
        "publishedAt": published_at,
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
        "sourceQuality": quality,
        "sponsored": False,
        "duplicateOf": None,
        "fetchStatus": "full",
    }


class RestaurantReviewsV2Test(unittest.TestCase):
    def test_missing_cache_returns_v2_unknown_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = reviews.load_review_cache(tmp, "不存在餐廳")

        self.assertEqual(report["schemaVersion"], 2)
        self.assertFalse(report["success"])
        self.assertTrue(report["needsIdentity"])
        self.assertEqual(report["riskLevel"], "unknown")
        self.assertEqual(report["recommendationScore"], 0)
        self.assertEqual(report["confidenceScore"], 0)

    def test_legacy_cache_is_readable_and_marked_for_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            reviews.write_review_cache(
                tmp,
                "測試餐廳",
                {
                    "success": True,
                    "restaurantName": "測試餐廳",
                    "overallScore": 78,
                    "riskLevel": "low",
                    "sources": [
                        {
                            "title": "舊來源",
                            "url": "https://example.com/a",
                            "excerpt": "牛肉麵好吃",
                            "sourceType": "web",
                        }
                    ],
                },
            )
            report = reviews.load_review_cache(tmp, "測試餐廳")

        self.assertTrue(report["success"])
        self.assertTrue(report["needsRefresh"])
        self.assertEqual(report["recommendationScore"], 0)
        self.assertEqual(report["overallScore"], 0)
        self.assertEqual(report["riskLevel"], "unknown")
        self.assertEqual(report["sources"][0]["fetchStatus"], "legacy")

    def test_refresh_requires_confirmed_identity_before_collecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                reviews,
                "identify_restaurant_candidates",
                return_value=[
                    {
                        "officialName": "測試餐廳 A店",
                        "address": "",
                        "confidence": 55,
                    },
                    {
                        "officialName": "測試餐廳 B店",
                        "address": "",
                        "confidence": 53,
                    },
                ],
            ):
                with mock.patch.object(reviews, "_collect_enriched_sources") as collect:
                    report = reviews.refresh_restaurant_reviews(tmp, "測試餐廳")

        collect.assert_not_called()
        self.assertTrue(report["needsIdentity"])
        self.assertFalse(report["success"])
        self.assertEqual(len(report["identityCandidates"]), 2)

    def test_identify_candidates_extracts_address_and_deduplicates(self):
        raw = [
            {
                "title": "測試餐廳 台中店｜地址與評價",
                "url": "https://example.com/store",
                "excerpt": "地址 407台中市西屯區台灣大道三段100號",
                "sourceType": "web",
            },
            {
                "title": "測試餐廳 台中店｜地址與評價",
                "url": "https://example.com/store-copy",
                "excerpt": "地址 407台中市西屯區台灣大道三段100號",
                "sourceType": "web",
            },
        ]
        with mock.patch.object(
            reviews, "_collect_review_sources_via_html_search", return_value=raw
        ):
            candidates = reviews.identify_restaurant_candidates("測試餐廳 台中店")

        self.assertEqual(len(candidates), 1)
        self.assertIn("台中市", candidates[0]["address"])
        self.assertIn("google.com/maps", candidates[0]["mapsUrl"])

    def test_canonical_url_removes_tracking_and_fragment(self):
        url = reviews._canonicalize_url(
            "https://Example.com/review/?id=7&utm_source=test&fbclid=x#comments"
        )
        self.assertEqual(url, "https://example.com/review?id=7")

    def test_full_page_failure_falls_back_to_search_snippet(self):
        identity = {"officialName": "測試餐廳", "address": "", "confirmed": True}
        raw = {
            "title": "測試餐廳評價",
            "url": "https://example.com/review",
            "excerpt": "牛肉麵好吃，份量充足。",
        }
        with mock.patch.object(reviews, "_extract_page", side_effect=OSError("blocked")):
            enriched = reviews._enrich_source(raw, identity)

        self.assertEqual(enriched["fetchStatus"], "snippet")
        self.assertIn("牛肉麵好吃", enriched["content"])

    def test_duplicate_content_is_excluded_from_quality(self):
        sources = [
            source("SRC-1", "牛肉麵很好吃，份量充足，服務人員也很親切。" * 5),
            source("SRC-2", "牛肉麵很好吃，份量充足，服務人員也很親切。" * 5),
        ]
        reviews._mark_duplicates(sources)

        self.assertEqual(sources[1]["duplicateOf"], "SRC-1")
        self.assertEqual(sources[1]["sourceQuality"], 0)

    def test_recency_weight_decreases_for_old_and_unknown_sources(self):
        recent = datetime.now(timezone.utc) - timedelta(days=30)
        old = datetime.now(timezone.utc) - timedelta(days=1500)

        self.assertEqual(reviews._recency_weight(recent.isoformat()), 1.0)
        self.assertEqual(reviews._recency_weight(old.isoformat()), 0.4)
        self.assertEqual(reviews._recency_weight(None), 0.6)

    def test_aspect_one_source_is_estimated_and_two_are_supported(self):
        one_source = [
            source("SRC-1", "這間店的牛肉麵味道很好吃，湯頭也很美味。")
        ]
        result = reviews.analyze_review_signals(one_source)
        self.assertIsInstance(result["aspects"]["taste"]["score"], int)
        self.assertEqual(result["aspects"]["taste"]["status"], "estimated")
        one_source_confidence = result["aspects"]["taste"]["confidence"]

        two_sources = one_source + [
            source("SRC-2", "餐點口味不錯而且很新鮮，會想再次回訪。")
        ]
        result = reviews.analyze_review_signals(two_sources)
        self.assertIsInstance(result["aspects"]["taste"]["score"], int)
        self.assertEqual(result["aspects"]["taste"]["status"], "supported")
        self.assertGreater(
            result["aspects"]["taste"]["confidence"], one_source_confidence
        )
        self.assertGreater(result["recommendationScore"], 0)

    def test_refresh_auto_selects_clear_identity_candidate(self):
        candidate = {
            "officialName": "測試餐廳 台中店",
            "address": "台中市西屯區測試路1號",
            "confidence": 88,
        }
        fake_sources = [
            source("SRC-1", "牛肉麵口味好吃，價格也很划算。"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                reviews, "identify_restaurant_candidates", return_value=[candidate]
            ):
                with mock.patch.object(
                    reviews, "_collect_enriched_sources", return_value=fake_sources
                ):
                    with mock.patch.object(
                        reviews,
                        "_summarize_v2",
                        return_value={
                            "summary": "有初步正面評價。",
                            "prosEvidence": [],
                            "consEvidence": [],
                            "recommendedFor": [],
                        },
                    ):
                        report = reviews.refresh_restaurant_reviews(
                            tmp, "測試餐廳"
                        )

        self.assertFalse(report["needsIdentity"])
        self.assertEqual(
            report["restaurantIdentity"]["officialName"], candidate["officialName"]
        )

    def test_incentive_risk_has_traceable_evidence(self):
        sources = [
            source("SRC-1", "店內公告五星送飲料，評論送小菜。"),
            source("SRC-2", "餐點味道普通，但服務人員親切。"),
        ]
        result = reviews.analyze_review_signals(sources)

        self.assertIn(result["riskLevel"], {"medium", "high"})
        self.assertIn("五星送", result["incentiveHits"])
        ids = result["riskSignals"][0]["evidenceIds"]
        evidence_ids = {item["evidenceId"] for item in result["evidence"]}
        self.assertTrue(ids)
        self.assertTrue(set(ids).issubset(evidence_ids))

    def test_sparse_non_review_sources_report_unknown_risk(self):
        sources = [
            source(
                "SRC-1",
                "官方網站提供餐廳地址與營業時間。",
                source_type="web",
                quality=0.4,
            )
        ]
        result = reviews.analyze_review_signals(sources)
        self.assertEqual(result["riskLevel"], "unknown")

    def test_platform_rating_provides_labeled_fallback_score(self):
        sources = [
            source(
                "SRC-1",
                "Foodpanda 4.9/5，4000+ 則評論。",
                source_type="review_platform",
                quality=0.9,
            )
        ]
        result = reviews.analyze_review_signals(sources)

        self.assertEqual(result["recommendationScore"], 98)
        self.assertEqual(result["scoreBasis"], "platform_rating")
        self.assertEqual(result["platformRating"]["average"], 4.9)

    def test_refresh_builds_separate_recommendation_and_confidence_scores(self):
        identity = {
            "officialName": "測試餐廳 台中店",
            "address": "台中市西屯區測試路1號",
            "confirmed": True,
        }
        fake_sources = [
            source("SRC-1", "牛肉麵味道好吃，價格也很划算。"),
            source("SRC-2", "餐點口味美味，價位便宜值得回訪。"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                reviews, "_collect_enriched_sources", return_value=fake_sources
            ):
                with mock.patch.object(
                    reviews,
                    "_summarize_v2",
                    return_value={
                        "summary": "口味與價格有正面證據。",
                        "prosEvidence": [],
                        "consEvidence": [],
                        "recommendedFor": [],
                    },
                ):
                    report = reviews.refresh_restaurant_reviews(
                        tmp, "測試餐廳", identity
                    )

            cached = reviews.load_review_cache(tmp, "測試餐廳")

        self.assertTrue(report["success"])
        self.assertFalse(report["needsIdentity"])
        self.assertEqual(report["overallScore"], report["recommendationScore"])
        self.assertGreater(report["confidenceScore"], 0)
        self.assertEqual(cached["restaurantIdentity"]["address"], identity["address"])


if __name__ == "__main__":
    unittest.main()
