import unittest

from overwatch.errors import Rejected
from overwatch.scoring import DEFAULT_WEIGHTS, rank_candidates, resolve_weights, score_candidate


def verified(strength=1.0, **extra):
    return {"verified": True, "strength": strength, **extra}


def full_candidate(provider="Provider A", strength=1.0):
    return {"provider": provider, "qualification_verified": True,
            "factors": {name: verified(strength) for name in DEFAULT_WEIGHTS}}


class ScoringTests(unittest.TestCase):
    def test_fully_verified_candidate_scores_the_full_hundred(self):
        result = score_candidate(full_candidate(), weights=resolve_weights())
        self.assertEqual(result["score"], "100.00")
        self.assertEqual(result["unverified_factors"], [])
        self.assertTrue(result["fully_authoritative"])

    def test_unverified_factor_scores_zero_not_partial(self):
        candidate = full_candidate()
        # A strong-looking but unverified capability claim must contribute nothing.
        candidate["factors"]["capability"] = {"verified": False, "strength": 1.0,
                                               "corroborating": {"crustdata_headcount": "501-1000"}}
        result = score_candidate(candidate, weights=resolve_weights())
        self.assertEqual(result["breakdown"]["capability"]["points"], "0.00")
        self.assertIn("capability", result["unverified_factors"])
        self.assertEqual(result["score"], "75.00")  # 100 - capability weight 25

    def test_partial_strength_is_weighted(self):
        candidate = full_candidate()
        candidate["factors"]["price"] = verified(0.5)  # 15 * 0.5 = 7.5
        result = score_candidate(candidate, weights=resolve_weights())
        self.assertEqual(result["breakdown"]["price"]["points"], "7.50")
        self.assertEqual(result["score"], "92.50")

    def test_unverified_pre_qualification_is_excluded_not_ranked(self):
        candidate = full_candidate("Unverified Co")
        candidate["qualification_verified"] = False
        result = rank_candidates([candidate])
        self.assertEqual(result["ranked"], [])
        self.assertIn("pre-qualification not verified", result["excluded"][0]["reason"])

    def test_ranking_orders_by_score_then_name(self):
        result = rank_candidates([full_candidate("B Corp", 0.5), full_candidate("A Corp", 1.0)])
        self.assertEqual([c["provider"] for c in result["ranked"]], ["A Corp", "B Corp"])
        self.assertEqual(result["recommendation"], "A Corp")

    def test_weights_must_sum_to_one_hundred(self):
        with self.assertRaisesRegex(Rejected, "sum to 100"):
            resolve_weights({"price": 50})

    def test_custom_weights_are_honoured(self):
        weights = resolve_weights({"capability": 40, "coverage_sla": 10, "past_performance": 20,
                                   "price": 10, "stability": 10, "security": 10})
        candidate = full_candidate()
        candidate["factors"]["capability"] = {"verified": False}
        self.assertEqual(score_candidate(candidate, weights=weights)["score"], "60.00")

    def test_unknown_factor_and_bad_strength_are_rejected(self):
        with self.assertRaises(Rejected):
            resolve_weights({"made_up": 0})
        bad = full_candidate()
        bad["factors"]["price"] = verified(1.5)
        with self.assertRaises(Rejected):
            score_candidate(bad, weights=resolve_weights())

    def test_duplicate_or_empty_candidates_are_rejected(self):
        with self.assertRaises(Rejected):
            rank_candidates([full_candidate("A"), full_candidate("A")])
        with self.assertRaises(Rejected):
            rank_candidates([])


if __name__ == "__main__":
    unittest.main()
