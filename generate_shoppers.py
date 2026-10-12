"""Generate and freeze a reproducible synthetic cohort without an external API.

python3 generate_shoppers.py --seed example-001 --count 1000 --output data/shoppers/runs/example-001
Loads a frozen API-generated bank; SHA-256-based selection avoids global random state.
These are combinatorial synthetic profiles, not calibrated population samples.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BANK = ROOT / "data/generated/option_bank.json"
VERSION = "2.0"
BEHAVIOR = (
    "price_behavior",
    "research_habit",
    "service_preference",
    "comparison_style",
    "brand_orientation",
    "promotion_response",
    "return_preference",
    "patience",
    "accessory_behavior",
    "retailer_habit",
)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def generate(seed, count, bank_path=BANK):
    if count < 1:
        raise ValueError("count must be positive")
    raw = Path(bank_path).read_bytes()
    bank_hash = digest(raw)
    bank = json.loads(raw)["categories"]
    profiles = []
    for index in range(count):
        selected = {}

        def pick(category, predicate=lambda item: True):
            eligible = [v for v in bank[category] if predicate(v)]
            if not eligible:
                raise ValueError(f"No compatible options in {category}")
            # Separate key per shopper and field: adding shoppers preserves the prefix.
            key = json.dumps([VERSION, bank_hash, str(seed), index, category])
            value = eligible[int(digest(key.encode()), 16) % len(eligible)]
            selected[category] = value["id"]
            return value

        mission = pick("mission")
        channel = pick("channel")["text"]
        sizing = pick("sizing", lambda x: x["system"] == mission["size_system"])["text"]
        if mission["size_system"] in ("adult", "youth"):
            sizing += "; " + pick("width")["text"] + " width"
        if mission["product_type"] == "cleats":
            sizing += "; soccer cleats for natural grass"
        elif mission["product_type"] == "basketball":
            sizing = "Size 7 indoor basketball"
        elif mission["product_type"] == "golf_ball":
            sizing = "One dozen new golf balls; verify intended model"
        elif mission["product_type"] == "golf_bag":
            sizing = "Carry golf bag suitable for walking rounds"
        time = pick("time_allowance", lambda x: x["channel"] == channel)["minutes"]
        # Browsing has no deadline; a generated urgency must not contradict its mission.
        deadline = pick(
            "deadline", lambda x: mission["segment"] != "browse" or x["id"] == "deadline-none"
        )["text"]
        low, high = mission["preferred_spend_range"]
        preferred = round(low + (high - low) * pick("budget_position")["fraction"])
        cap = round(preferred * (1 + pick("budget_flexibility")["fraction"]))

        def compatible(item):
            return mission["product_type"] in item.get(
                "eligible_product_types", [mission["product_type"]]
            ) and mission["segment"] in item.get("eligible_segments", [mission["segment"]])

        traits = {category: pick(category, compatible)["text"] for category in BEHAVIOR}
        shopper_id = f"llm-v2-{digest(str(seed).encode())[:12]}-{bank_hash[:12]}-{index + 1:06d}"
        no_purchase = (
            "Leave, switch or defer if no observed offer satisfies the essential specifications, "
            "the total checkout cost exceeds the cap, or required availability cannot be established. "
            "Having browsed does not require buying."
        )
        hierarchy = [
            "Hard spending and time limits, required specification and mission take precedence.",
            "Preferences apply only when compatible with the mission and observed offers.",
            "No-purchase, postponement and switching remain valid outcomes.",
            "Do not invent inventory, product properties, reviews, promotions or store experiences.",
        ]
        profile = {
            "schema_version": VERSION,
            "shopper_id": shopper_id,
            "provenance": "Seeded composition of a frozen generated option bank; not an observed person.",
            "segment": mission["segment"],
            "product_type": mission["product_type"],
            "stable_preferences": traits,
            "occasion": {
                "channel": channel,
                "context": mission["context"],
                "target_product_family": mission["target_product_family"],
                "sizing_or_specification": sizing,
                "currency": "USD",
                "preferred_spend": preferred,
                "hard_spend_cap": cap,
                "budget_scope": "Total checkout cost including applicable tax and shipping.",
                "time_available_minutes": time,
                "purchase_deadline": deadline,
                "accessory_rule": traits["accessory_behavior"],
                "no_purchase_rule": no_purchase,
            },
            "instruction_precedence": hierarchy,
            "population_weight": None,
            "generation": {
                "seed": str(seed),
                "index": index,
                "generator_version": VERSION,
                "option_bank_sha256": bank_hash,
                "selected_option_ids": selected,
            },
            "initial_memory": {
                "observed_offers": [],
                "rejected_offers": [],
                "shortlist": [],
                "cart": [],
                "events": [],
                "elapsed_minutes": 0,
                "money_spent": 0,
            },
        }
        # Human-readable view derived from the same authoritative structured fields.
        profile["profile_text"] = (
            f"{mission['context']} Shopping channel: {channel}. Required specification: {sizing}. "
            f"Target product family: {mission['target_product_family'] or 'Open to suitable alternatives'}. "
            f"Preferred total spend: ${preferred}; hard cap: ${cap}. "
            f"Available session time: {time} minutes. Purchase deadline: {deadline}. "
            + " ".join(traits.values())
            + " "
            + no_purchase
            + " "
            + " ".join(hierarchy)
        )
        profiles.append(profile)
    return profiles


def save(seed, count, output, bank_path=BANK):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite a saved run: {output}")
    profiles = generate(seed, count, bank_path)
    bank_raw = Path(bank_path).read_bytes()
    profile_raw = encoded(profiles)
    summary = {
        "count": count,
        "segments": dict(Counter(p["segment"] for p in profiles)),
        "channels": dict(Counter(p["occasion"]["channel"] for p in profiles)),
        "product_types": dict(Counter(p["product_type"] for p in profiles)),
        "distinct_option_combinations": len(
            {json.dumps(p["generation"]["selected_option_ids"], sort_keys=True) for p in profiles}
        ),
    }
    manifest = {
        "cohort_id": f"llm_composed_v2_{seed}",
        "schema_version": VERSION,
        "seed": str(seed),
        "count": count,
        "profiles_sha256": digest(profile_raw),
        "option_bank_sha256": digest(bank_raw),
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "sampling_design": "Uniform within each eligible option list, including missions. This is a synthetic design, not observed population frequencies.",
        "weights": "population_weight is null. Equal-weight results describe this constructed cohort only.",
        "compatibility_rules": [
            "Mission selects the sizing system and preferred-spend range.",
            "Tagged behavioral and retailer options are filtered by product type and segment.",
            "Channel selects the eligible time limits.",
            "Browsing has no purchase deadline.",
            "Hard cap is never below preferred spend.",
            "Hard constraints and the mission override soft preferences.",
        ],
        "limitations": [
            "Soft traits are sampled independently and may not reflect real human correlations.",
            "No real demographic distribution, shopping history or brand preference calibration.",
            "No actual stock, fit outcome, conversion probability or retailer uplift supplied.",
            "Store access and dated merchandise must come from the simulation environment.",
        ],
        "integration_status": "Generated cohorts load with shopper_cohort.load_profiles(path) and are consumed by the sequential shopping engine.",
        "replay": "Reuse frozen profiles.json. Generator and bank snapshots are included. Use identical shoppers across paired worlds with separate session memory; seed Jev action sampling separately.",
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "profiles.json").write_bytes(profile_raw)
    (output / "manifest.json").write_bytes(encoded(manifest))
    (output / "summary.json").write_bytes(encoded(summary))
    (output / "option_bank.json").write_bytes(bank_raw)
    (output / "generator_snapshot.py").write_bytes(Path(__file__).read_bytes())
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bank", type=Path, default=BANK)
    args = parser.parse_args()
    print(json.dumps(save(args.seed, args.count, args.output, args.bank), indent=2))
