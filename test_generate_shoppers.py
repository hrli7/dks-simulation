import json
import tempfile
import unittest
from pathlib import Path

from generate_shoppers import encoded
from generate_shoppers import generate as original_generate
from generate_shoppers import save as original_save
from shopper_cohort import load_profiles, new_session
from test_support import inputs


def generate(seed, count):
    return original_generate(seed, count, inputs() / "option_bank.json")


def save(seed, count, output):
    return original_save(seed, count, output, inputs() / "option_bank.json")


class GeneratedCohortTests(unittest.TestCase):
    def test_replay_prefix_and_different_seed(self):
        a = generate("audit", 50)
        self.assertEqual(encoded(a), encoded(generate("audit", 50)))
        self.assertEqual(a, generate("audit", 80)[:50])
        self.assertNotEqual(
            a[0]["generation"]["selected_option_ids"],
            generate("different", 1)[0]["generation"]["selected_option_ids"],
        )

    def test_constraints_across_large_cohort(self):
        profiles = generate("constraints", 2000)
        self.assertEqual(len({p["shopper_id"] for p in profiles}), 2000)
        for p in profiles:
            o = p["occasion"]
            self.assertLessEqual(o["preferred_spend"], o["hard_spend_cap"])
            self.assertGreater(o["time_available_minutes"], 0)
            if p["segment"] == "browse":
                self.assertEqual(o["purchase_deadline"], "no deadline")
            if p["product_type"] in ("basketball", "golf_ball", "golf_bag", "golf_driver"):
                self.assertNotIn("width", p["generation"]["selected_option_ids"])
                self.assertNotIn("US men", o["sizing_or_specification"])
            if p["product_type"] == "cleats":
                self.assertIn("US youth", o["sizing_or_specification"])
            if p["segment"] == "golf":
                self.assertNotIn("Foot Locker", p["stable_preferences"]["retailer_habit"])
                self.assertNotIn("JD Sports", p["stable_preferences"]["retailer_habit"])
            self.assertIsNone(p["population_weight"])

    def test_frozen_files_loader_and_memory_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cohort"
            save("saved", 10, path)
            profiles = load_profiles(path)
            self.assertEqual(profiles, generate("saved", 10))
            a = new_session(profiles[0], run_id="a", scenario_id="baseline")
            b = new_session(profiles[0], run_id="a", scenario_id="remodel")
            a["memory"]["cart"].append("example")
            self.assertEqual(b["memory"]["cart"], [])
            self.assertEqual(profiles[0]["initial_memory"]["cart"], [])
            with self.assertRaises(FileExistsError):
                save("saved", 10, path)
            (path / "profiles.json").write_text(json.dumps([]))
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_profiles(path)


if __name__ == "__main__":
    unittest.main()
