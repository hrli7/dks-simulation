"""Build a simulation snapshot from normalized JSONL offers or fictional fixtures."""

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path

from generate_inputs import BANNERS

COLUMNS = [
    "observation_id",
    "retailer",
    "name",
    "brand",
    "category",
    "size",
    "color",
    "price",
    "currency",
    "family",
    "availability",
    "observed_at",
    "source_url",
    "gtin14",
    "product_key",
    "raw_json",
]


def build(source, output, as_of):
    from datetime import date

    date.fromisoformat(as_of)
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    source = Path(source)
    rows, ids = [], set()
    for line in source.read_text().splitlines():
        if not line.strip():
            continue
        offer = json.loads(line)
        for key in ["retailer", "name", "category", "observed_at"]:
            if not isinstance(offer.get(key), str) or not offer[key]:
                raise ValueError(f"Missing offer field: {key}")
        if offer["retailer"] not in BANNERS.values():
            raise ValueError("Unknown retailer; use the banner mapping in generate_inputs.py")
        if (
            offer.get("currency") != "USD"
            or not isinstance(offer.get("price"), (int, float))
            or not math.isfinite(offer["price"])
            or offer["price"] <= 0
        ):
            raise ValueError("Offers need positive finite USD prices")
        if date.fromisoformat(offer["observed_at"][:10]) > date.fromisoformat(as_of):
            raise ValueError("Offer observation is later than the snapshot cutoff")
        if offer.get("family") not in ["historical_variant", "competitor_offer", "launch_listing"]:
            raise ValueError("Unsupported evidence family")
        oid = (
            offer.get("observation_id")
            or hashlib.sha256(json.dumps(offer, sort_keys=True).encode()).hexdigest()
        )
        if oid in ids:
            raise ValueError("Duplicate observation ID")
        ids.add(oid)
        offer["observation_id"] = oid
        offer["raw_json"] = json.dumps({"gender": offer.get("gender", "")})
        rows.append([offer.get(k) for k in COLUMNS])
    if not rows:
        raise ValueError("Catalog cannot be empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as db:
        db.execute(
            "CREATE TABLE snapshot ("
            + ",".join(k + (" REAL" if k == "price" else " TEXT") for k in COLUMNS)
            + ")"
        )
        db.executemany("INSERT INTO snapshot VALUES (" + ",".join("?" for _ in COLUMNS) + ")", rows)
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)",
            [("as_of", as_of), ("sources_sha256", hashlib.sha256(source.read_bytes()).hexdigest())],
        )


def demo(path):
    """Generate fictional equal-assortment plumbing fixtures, not real merchandise."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    offers = []
    for retailer in BANNERS.values():
        for size in [7, 8, 9, 9.5, 10, 11, 12]:
            for sex in ["Men", "Women"]:
                offers.append(
                    dict(
                        retailer=retailer,
                        name=f"Fictional {sex} Running Shoe",
                        brand="Fixture",
                        category="footwear",
                        size=str(size),
                        gender=sex,
                        price=50,
                        currency="USD",
                        family="historical_variant",
                        availability="web_buyable",
                        observed_at="2000-01-01",
                        source_url="https://example.com/fictional-test-product",
                        product_key=f"fixture-{sex}",
                    )
                )
    path.write_text("".join(json.dumps(x) + "\n" for x in offers))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/private/offers.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/catalog.sqlite"))
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        demo(args.input)
    build(args.input, args.output, args.as_of)
