"""Sourced retailer context; analyst hypotheses stay outside consumer prompts."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "data/generated/retailer_profiles.json"


@lru_cache(maxsize=1)
def registry():
    return json.loads(REGISTRY.read_text())


def consumer_context(profile_id, channel="physical"):
    if channel not in ("physical", "ecommerce"):
        raise ValueError("Unsupported channel")
    data = registry()
    selected = next(p for p in data["profiles"] if p["profile_id"] == profile_id)
    # Remodels change a physical scenario, not the parent banner's website.
    p = selected
    if channel == "ecommerce" and p["kind"] == "format":
        p = next(
            x
            for x in data["profiles"]
            if x["catalog_banner"] == p["catalog_banner"] and x["kind"] == "banner"
        )
    return {
        "profile_id": p["profile_id"],
        "name": p["display_name"],
        "catalog_banner": p["catalog_banner"],
        "description": p["shopper_description"],
        "merchandise_scope": p["merchandise_scope"],
        "channel_experience": p["physical_experience"]
        if channel == "physical"
        else p["ecommerce_context"],
        "shopping_tradeoffs": p["shopping_tradeoffs"],
        "availability_rule": p["availability_rule"],
        "location_rule": p["location_rule"],
        "scope": "Generic retailer identity / explicit format scenario, not verified local inventory.",
    }


def brand_context(brand_name):
    if not brand_name:
        return None
    for brand in registry()["private_brands"]:
        if brand_name.casefold() in {a.casefold() for a in brand["aliases"]}:
            return {
                k: brand[k]
                for k in (
                    "brand_id",
                    "display_name",
                    "ownership",
                    "shopper_description",
                    "comparison_rule",
                )
            }
    return None


def banner_context(banner, channel="physical"):
    for p in registry()["profiles"]:
        if p["catalog_banner"] == banner and p["kind"] != "format":
            return consumer_context(p["profile_id"], channel)
    return None


def prepare(cohort):
    """Save enriched contexts without changing previous cohorts or API results."""
    cohort = Path(cohort)
    output = cohort / "shopping_contexts_v2.jsonl"
    if output.exists():
        raise FileExistsError(output)
    checksum = hashlib.sha256(REGISTRY.read_bytes()).hexdigest()
    n = 0
    with (cohort / "merchandise_contexts.jsonl").open() as source, output.open("w") as dest:
        for line in source:
            packet = json.loads(line)
            channel = packet["profile"]["occasion"]["channel"]
            packet["retailer_profile_version"] = registry()["version"]
            packet["retailer_profile_sha256"] = checksum
            packet["retailer_contexts"] = {
                banner: banner_context(banner, channel)
                for banner in packet["merchandise"]["by_retailer"]
            }
            packet["format_scenario_contexts"] = {
                p["profile_id"]: consumer_context(p["profile_id"], channel)
                for p in registry()["profiles"]
                if p["kind"] == "format"
                and p["catalog_banner"] in packet["merchandise"]["by_retailer"]
            }
            packet["context_use_rule"] = (
                "Format contexts are alternative scenario descriptions, not additional nearby stores. "
                "Use only the selected location/format. Services still need scenario availability and time/cost inputs. "
                "Private-brand descriptions are supplied when an observed product carries that brand. "
                "Do not use research-only thesis mechanisms or sampled coverage counts as consumer preferences."
            )
            dest.write(json.dumps(packet, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return {"profiles_prepared": n, "output": str(output), "registry_sha256": checksum}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.cohort), indent=2))
