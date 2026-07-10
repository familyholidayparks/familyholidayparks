import unittest

from score_parks import _weighted_aggregate_batch_scores, normalize_score_payload


class ScoreParksScoringTest(unittest.TestCase):
    def test_normalize_replaces_model_total_with_calculated_total(self):
        payload = {
            "total_score": 1,
            "entertainment_score": 10,
            "nature_score": 10,
            "value_score": 10,
            "cleanliness_score": 10,
            "site_size_score": 5,
            "sentiment_score": 5,
            "location_score": 5,
        }
        normalized = normalize_score_payload(payload)
        self.assertEqual(normalized["model_total_score"], 1)
        self.assertEqual(normalized["total_score"], 55)
        self.assertTrue(normalized["warnings"])

    def test_batch_aggregation_calculates_total_from_categories(self):
        batch_scores = [
            (
                {
                    "total_score": 100,
                    "entertainment_score": 20,
                    "nature_score": 20,
                    "value_score": 15,
                    "cleanliness_score": 15,
                    "site_size_score": 10,
                    "sentiment_score": 10,
                    "location_score": 10,
                },
                10,
            ),
            (
                {
                    "total_score": 0,
                    "entertainment_score": 10,
                    "nature_score": 10,
                    "value_score": 5,
                    "cleanliness_score": 5,
                    "site_size_score": 5,
                    "sentiment_score": 5,
                    "location_score": 5,
                },
                10,
            ),
        ]
        aggregated = _weighted_aggregate_batch_scores(batch_scores)
        self.assertEqual(aggregated["total_score"], 73)


if __name__ == "__main__":
    unittest.main()
