import unittest
from copy import deepcopy

from catalog_swarm import BANNER_IDS, OfferIndex, World, uniform
from test_support import bind_test_registry


def setUpModule():
    bind_test_registry()


def fixture(channel="physical"):
    name = next(
        "Test Shoe " + str(i)
        for i in range(100)
        if uniform("fit", "test", "Test Shoe " + str(i)) < 0.8
    )
    offer = dict(
        observation_id="one",
        name=name,
        price=50,
        size_evidence="matched",
        availability="web_buyable",
        role="mission_product",
    )
    p = dict(
        shopper_id="test",
        segment="running",
        product_type="footwear",
        stable_preferences={},
        occasion=dict(
            channel=channel,
            time_available_minutes=60,
            hard_spend_cap=100,
            sizing_or_specification="US men 10; standard width",
        ),
    )
    return dict(
        profile=p,
        offers={r: [deepcopy(offer)] for r in BANNER_IDS},
        addons={r: [] for r in BANNER_IDS},
        access_minutes={r: 5 for r in BANNER_IDS},
    )


class SwarmTests(unittest.TestCase):
    def act(self, w, key):
        self.assertIn(key, w.actions())
        return w.step(key, w.actions()[key])

    def test_sequential_purchase_and_budget(self):
        w = World(fixture(), "baseline")
        self.act(w, "visit:DICK’S Sporting Goods")
        self.act(w, "browse")
        self.assertNotIn("add:one", w.actions())
        self.assertNotIn("checkout", w.actions())
        self.act(w, "check:one")
        self.assertNotIn("add:one", w.actions())
        self.act(w, "try:one")
        self.act(w, "add:one")
        self.act(w, "checkout")
        self.assertEqual(w.purchase["revenue"], 50)
        self.assertEqual(w.purchase["total"], 54)

    def test_same_latent_outcome_across_formats(self):
        results = []
        for scenario in ["baseline", "hos", "fast_break", "both"]:
            w = World(fixture(), scenario)
            for key in ["visit:DICK’S Sporting Goods", "browse", "check:one", "try:one"]:
                self.act(w, key)
            results.append((w.checked, w.fit))
        self.assertTrue(all(x == results[0] for x in results))

    def test_online_control(self):
        a = World(fixture("ecommerce"), "baseline")
        b = World(fixture("ecommerce"), "both")
        self.assertEqual(a.actions(), b.actions())
        for key in ["visit:Foot Locker", "browse", "check:one"]:
            self.act(a, key)
            self.act(b, key)
            self.assertEqual(a.observation(), b.observation())
            self.assertEqual(a.actions(), b.actions())

    def test_overspend_and_time_actions_removed(self):
        data = fixture()
        data["profile"]["occasion"]["hard_spend_cap"] = 40
        w = World(data, "baseline")
        for key in ["visit:DICK’S Sporting Goods", "browse", "check:one", "try:one"]:
            self.act(w, key)
        self.assertNotIn("add:one", w.actions())
        w.minutes = 60
        self.assertEqual(set(w.actions()), {"leave"})

    def test_known_size_and_gender(self):
        p = fixture()["profile"]
        self.assertEqual(
            OfferIndex.size_status(p, dict(size="9", gender="Men's", name="Running shoe")),
            "mismatch",
        )
        self.assertEqual(
            OfferIndex.size_status(p, dict(size="10", gender="Women's", name="Running shoe")),
            "mismatch",
        )
        self.assertEqual(
            OfferIndex.size_status(
                p, dict(size="M10.0/W12.0", gender="Men's|Women's", name="Clog")
            ),
            "matched",
        )

    def test_revisit_preserves_discovery(self):
        w = World(fixture(), "baseline")
        for key in [
            "visit:DICK’S Sporting Goods",
            "browse",
            "switch",
            "visit:DICK’S Sporting Goods",
        ]:
            self.act(w, key)
        self.assertIn("one", w.visible)


if __name__ == "__main__":
    unittest.main()
