import json
import unittest

from retailer_profiles import brand_context, consumer_context, registry
from test_support import bind_test_registry


def setUpModule():
    bind_test_registry()


class RetailerProfileTests(unittest.TestCase):
    def test_source_links_and_unique_ids(self):
        r = registry()
        ids = [p["profile_id"] for p in r["profiles"]]
        self.assertEqual(len(ids), len(set(ids)))
        for p in r["profiles"] + r["private_brands"]:
            for source in p["source_ids"]:
                self.assertIn(source, r["sources"])

    def test_research_excluded_from_shopper_context(self):
        for p in registry()["profiles"]:
            context = consumer_context(p["profile_id"])
            text = json.dumps(context)
            for key in [
                "research_only",
                "outcomes_to_measure",
                "sampled_products",
                "thesis_mechanism",
            ]:
                self.assertNotIn(key, text)
        self.assertNotEqual(consumer_context("house_of_sport"), consumer_context("dks"))
        self.assertNotEqual(consumer_context("fast_break"), consumer_context("foot_locker"))

    def test_online_remodel_control(self):
        self.assertEqual(
            consumer_context("house_of_sport", "ecommerce"), consumer_context("dks", "ecommerce")
        )
        self.assertEqual(
            consumer_context("fast_break", "ecommerce"),
            consumer_context("foot_locker", "ecommerce"),
        )

    def test_unknown_brand_is_not_assumed_owned(self):
        self.assertIsNone(brand_context("Unregistered fixture brand"))


if __name__ == "__main__":
    unittest.main()
