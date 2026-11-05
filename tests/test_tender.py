import unittest
from overwatch.tender import compare_bids
from overwatch.service import Rejected


class TenderTests(unittest.TestCase):
    def bid(self, supplier="supplier-a", price="100.10", **overrides):
        return dict(supplier=supplier, unit_price=price, quantity=3, freight="10.00",
                    tax="5.00", currency="USD", qualified=True, screened=True,
                    evidence_id="written-bid-1", availability_confirmed=True,
                    expires_at=2000, **overrides)

    def test_exact_decimal_totals_and_cheapest_eligible_bid(self):
        result = compare_bids([self.bid(), self.bid("supplier-b", "90.10")], now=1000, currency="USD")
        self.assertEqual(result["ranked"][0]["supplier"], "supplier-b")
        self.assertEqual(result["ranked"][0]["total"], "285.30")
        self.assertEqual(result["ranked"][1]["total"], "315.30")

    def test_discovery_alone_cannot_qualify_supplier(self):
        bid = self.bid()
        bid["qualified"] = False
        result = compare_bids([bid], now=1000, currency="USD")
        self.assertEqual(result["ranked"], [])
        self.assertIn("qualification", result["excluded"][0]["reason"])

    def test_stale_bid_missing_commitment_and_currency_are_excluded(self):
        for field, value in [("expires_at", 900), ("availability_confirmed", False),
                             ("currency", "EUR"), ("evidence_id", ""), ("screened", False)]:
            bid = self.bid()
            bid[field] = value
            with self.subTest(field=field):
                self.assertEqual(compare_bids([bid], now=1000, currency="USD")["ranked"], [])

    def test_invalid_amounts_or_quantity_do_not_enter_scoring(self):
        for field, value in [("unit_price", "NaN"), ("quantity", -1), ("quantity", 1.5)]:
            bid = self.bid()
            bid[field] = value
            with self.assertRaises(Rejected):
                compare_bids([bid], now=1000, currency="USD")

    def test_different_quantities_are_not_ranked_as_equivalent_bids(self):
        bid = self.bid("supplier-b")
        bid["quantity"] = 1
        with self.assertRaisesRegex(Rejected, "quantity"):
            compare_bids([self.bid(), bid], now=1000, currency="USD")
