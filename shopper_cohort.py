"""Load frozen synthetic shoppers without an API; create isolated session memory.

Run `python3 shopper_cohort.py --cohort data/runs/example-001/cohort` to verify the cohort and print its coverage.
Do not write session changes back into the source profiles.
"""

import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

COHORT = Path(__file__).resolve().parent / "data/runs/default/cohort"


def load_profiles(directory=COHORT):
    directory = Path(directory)
    raw = (directory / "profiles.json").read_bytes()
    manifest = json.loads((directory / "manifest.json").read_text())
    if hashlib.sha256(raw).hexdigest() != manifest["profiles_sha256"]:
        raise ValueError("Cohort checksum mismatch: use a new version for edits.")
    profiles = json.loads(raw)
    if len(profiles) != manifest["count"]:
        raise ValueError("Cohort count mismatch.")
    ids = [p["shopper_id"] for p in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("Shopper IDs must be unique.")
    for p in profiles:
        occasion = p["occasion"]
        if not 0 < occasion["preferred_spend"] <= occasion["hard_spend_cap"]:
            raise ValueError(f"Invalid budget: {p['shopper_id']}")
        if occasion["time_available_minutes"] <= 0:
            raise ValueError(f"Invalid time limit: {p['shopper_id']}")
        if occasion["channel"] not in ("physical", "ecommerce"):
            raise ValueError(f"Unknown channel: {p['shopper_id']}")
        for key in ("context", "sizing_or_specification", "no_purchase_rule"):
            if not occasion[key]:
                raise ValueError(f"Missing {key}: {p['shopper_id']}")
    return profiles


def new_session(profile, *, run_id, scenario_id):
    """Return independent mutable state; caller persists it under its run directory."""
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "shopper_id": profile["shopper_id"],
        "memory": deepcopy(profile["initial_memory"]),
        "remaining_spend_cap": profile["occasion"]["hard_spend_cap"],
        "remaining_minutes": profile["occasion"]["time_available_minutes"],
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    args = parser.parse_args()
    profiles = load_profiles(args.cohort)
    print(
        json.dumps(
            {
                "count": len(profiles),
                "segments": dict(Counter(p["segment"] for p in profiles)),
                "channels": dict(Counter(p["occasion"]["channel"] for p in profiles)),
                "checksum_verified": True,
            },
            indent=2,
        )
    )
