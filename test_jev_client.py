import unittest

from jev_client import MODEL, joint_probabilities, request_for, validate_answer


class JevPolicyTests(unittest.TestCase):
    def test_conditional_baskets_and_no_purchase(self):
        state = dict(
            shopper={},
            alternatives=[
                dict(id="FL_shoe", retailer_name="Foot Locker", price=100),
                dict(id="FL_bundle", retailer_name="Foot Locker", price=120),
                dict(id="DKS_shoe", retailer_name="DKS", price=100),
                dict(id="NONE", price=0),
            ],
        )
        request = request_for(state)
        response = dict(
            model=MODEL,
            answers=dict(
                retailer=dict(
                    type="choice",
                    choice="FL",
                    confidence=0.25,
                    probabilities=dict(FL=0.5, DKS=0.3, NONE=0.2),
                ),
                basket_FL=dict(
                    type="choice",
                    choice="FL_shoe",
                    confidence=0.5,
                    probabilities=dict(FL_shoe=0.75, FL_bundle=0.25),
                ),
            ),
        )
        self.assertEqual(
            joint_probabilities(request, response),
            dict(FL_shoe=0.375, FL_bundle=0.125, DKS_shoe=0.3, NONE=0.2),
        )
        # Expected revenue: .375*100 + .125*120 + .3*100 = $82.50.
        p = joint_probabilities(request, response)
        self.assertEqual(sum(a["price"] * p[a["id"]] for a in state["alternatives"]), 82.5)

    def test_only_no_purchase_needs_no_model(self):
        request = request_for(dict(shopper={}, alternatives=[dict(id="NONE", price=0)]))
        self.assertFalse(request["questions"])
        self.assertEqual(
            joint_probabilities(request, dict(model=MODEL, answers={})), dict(NONE=1.0)
        )

    def test_wrong_model_or_illegal_mass_fails(self):
        request = request_for(dict(shopper={}, alternatives=[dict(id="NONE", price=0)]))
        with self.assertRaises(AssertionError):
            joint_probabilities(request, dict(model="other", answers={}))

    def test_bounded_api_rounding_only(self):
        answer = dict(
            type="choice",
            choice="FL",
            confidence=0.76,
            probabilities=dict(FL=0.80, NIKE=0.10, DKS=0.07, JD=0.0, VALUE=0.0, NONE=0.02),
        )
        corrected = validate_answer(answer, answer["probabilities"])
        self.assertAlmostEqual(sum(corrected.values()), 1)
        self.assertAlmostEqual(corrected["FL"], 0.8 / 0.99)
        self.assertAlmostEqual(sum(answer["probabilities"].values()), 0.99)
        answer["probabilities"]["FL"] = 0.4
        with self.assertRaises(AssertionError):
            validate_answer(answer, answer["probabilities"])


if __name__ == "__main__":
    unittest.main()
