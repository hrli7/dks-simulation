"""Prepare, run, replay, and summarize paired sequential shopping experiments."""

import argparse
import hashlib
import json
import math
import shutil
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import catalog_swarm as sim
import retailer_profiles
from generate_shoppers import save as save_cohort
from metrics import summarize
from shopper_cohort import load_profiles

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {p.name: sha(p) for p in sorted(ROOT.glob("*.py"))}


def bind(run):
    retailer_profiles.REGISTRY = run / "retailer_profiles.json"
    retailer_profiles.registry.cache_clear()


def prepare(args):
    if args.count < 1:
        raise ValueError("count must be positive")
    if args.run.exists():
        raise FileExistsError("Choose a new run directory; prepared runs are immutable.")
    # Check dependencies before creating a partial run directory.
    for path in [
        args.catalog,
        args.inputs / "option_bank.json",
        args.inputs / "retailer_profiles.json",
    ]:
        if not path.is_file():
            raise FileNotFoundError(path)
    args.run.mkdir(parents=True)
    shutil.copyfile(args.inputs / "retailer_profiles.json", args.run / "retailer_profiles.json")
    bind(args.run)
    save_cohort(args.seed, args.count, args.run / "cohort", args.inputs / "option_bank.json")
    profiles = load_profiles(args.run / "cohort")
    index = sim.OfferIndex(args.catalog)
    lookup = {}
    for pool in index.pool.values():
        for offer in pool:
            item = {
                k: offer[k]
                for k in [
                    "observation_id",
                    "retailer",
                    "name",
                    "brand",
                    "size",
                    "color",
                    "price",
                    "family",
                    "availability",
                    "observed_at",
                    "source_url",
                    "gtin14",
                    "product_key",
                ]
            }
            item["private_brand_context"] = retailer_profiles.brand_context(offer["brand"])
            lookup[offer["observation_id"]] = item
    sim.savejson(args.run / "offer_lookup.json", lookup)
    prepared = args.run / "prepared"
    prepared.mkdir()
    for i, profile in enumerate(profiles):
        sim.savejson(
            prepared / f"{i:05d}.json",
            dict(
                profile=profile,
                offers={r: index.selection(profile, r) for r in sim.BANNER_IDS},
                addons={r: index.selection(profile, r, True) for r in sim.BANNER_IDS},
                access_minutes={
                    r: 5 + int(sim.uniform("travel", profile["shopper_id"], r) * 21)
                    for r in sim.BANNER_IDS
                },
            ),
        )
    snapshots = args.run / "source"
    snapshots.mkdir()
    for path in ROOT.glob("*.py"):
        shutil.copyfile(path, snapshots / path.name)
    sim.savejson(
        args.run / "design.json",
        dict(
            count=len(profiles),
            seed=args.seed,
            worlds=sim.WORLDS,
            model=sim.MODEL,
            catalog_sha256=sha(args.catalog),
            catalog_as_of=index.asof,
            source_sha256=source_hashes(),
            input_sha256={
                str(p.relative_to(args.run)): sha(p) for p in sorted(args.run.rglob("*.json"))
            },
            assumptions={
                "availability_unknown": 0.75,
                "fit_success": 0.8,
                "tax": 0.08,
                "access_minutes": [5, 25],
                "max_steps": sim.MAX_STEPS,
                "population": "Unweighted synthetic occasions; not annual store demand.",
                "inventory": "Snapshot offers are assumed accessible; unknown variants are simulated.",
                "online": "Physical format changes have identical online controls.",
            },
        ),
    )
    print(f"Prepared {len(profiles)} shoppers and {len(sim.WORLDS)} paired scenarios.")


def verify(run):
    design = read(run / "design.json")
    if design["source_sha256"] != source_hashes():
        raise ValueError(
            "Code changed after preparation; restore the source snapshot or prepare a new run."
        )
    for name, expected in design["input_sha256"].items():
        if sha(run / name) != expected:
            raise ValueError(f"Frozen input changed: {name}")
    bind(run)
    return design


def packet(run, i, lookup):
    prepared = read(run / "prepared" / f"{i:05d}.json")
    for kind in ["offers", "addons"]:
        prepared[kind] = {
            r: [
                {
                    **lookup[oid],
                    "size_evidence": status,
                    "role": "optional_addon" if kind == "addons" else "mission_product",
                }
                for oid, status in refs
            ]
            for r, refs in prepared[kind].items()
        }
    return prepared


class DemoPolicy:
    """Deterministic legal-action fixture; never an alternative behavioral model."""

    def choose(self, state, actions):
        preferred = next(
            (
                a
                for prefix in [
                    "checkout",
                    "add:",
                    "try:",
                    "check:",
                    "browse",
                    "visit:DICK’S Sporting Goods",
                ]
                for a in actions
                if a.startswith(prefix)
            ),
            "leave",
        )
        return {a: float(a == preferred) for a in actions}, None


def run(args):
    design = verify(args.run)
    if args.workers < 1:
        raise ValueError("workers must be positive")
    mode = "fixture" if args.demo else "jev"
    mode_file = args.run / "execution.json"
    if mode_file.exists() and read(mode_file)["mode"] != mode:
        raise ValueError("Do not mix demo and Jev episodes in the same run.")
    sim.savejson(mode_file, {"mode": mode})
    policy = DemoPolicy() if args.demo else sim.Policy(args.run / "cache")
    lookup = read(args.run / "offer_lookup.json")
    dest = args.run / "episodes"
    dest.mkdir(exist_ok=True)
    jobs = [
        (i, w)
        for i in range(design["count"])
        for w in design["worlds"]
        if not (dest / f"{i:05d}-{w}.json").exists()
    ]

    def job(pair):
        i, world = pair
        try:
            result = sim.episode(packet(args.run, i, lookup), world, policy)
            sim.savejson(dest / f"{i:05d}-{world}.json", result)
            return None
        except Exception as exc:
            # Do not persist exception payloads, request headers, or credentials.
            return {"index": i, "world": world, "error_type": type(exc).__name__}

    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, error in enumerate(pool.map(job, jobs), 1):
            if error:
                errors.append(error)
            if n % 20 == 0:
                print(f"{n}/{len(jobs)} episodes processed; {len(errors)} errors", flush=True)
    sim.savejson(args.run / "errors.json", errors)
    if errors:
        raise RuntimeError(
            f"{len(errors)} episodes failed; inspect errors.json and configuration, then resume."
        )
    print(f"Completed {len(jobs)} pending episodes ({mode}).")


def replay(run):
    design = verify(run)
    lookup = read(run / "offer_lookup.json")
    demo = read(run / "execution.json")["mode"] == "fixture"
    policy = DemoPolicy() if demo else sim.Policy(run / "cache", replay_only=True)
    count = 0
    for i in range(design["count"]):
        for world in design["worlds"]:
            path = run / "episodes" / f"{i:05d}-{world}.json"
            saved = read(path)
            replayed = sim.episode(packet(run, i, lookup), world, policy)
            if replayed != saved:
                raise ValueError(f"Replay mismatch: {path.name}")
            count += 1
    print(f"Exact replay verified for {count} episodes; no API calls.")


def analyze(run):
    design = verify(run)
    records = {
        w: [read(run / "episodes" / f"{i:05d}-{w}.json") for i in range(design["count"])]
        for w in design["worlds"]
    }
    summary, comparisons = {}, []
    for channel in ["physical", "ecommerce", "all"]:
        summary[channel] = {
            w: {
                banner: summarize(
                    [r for r in rows if channel == "all" or r["channel"] == channel], banner
                )
                for banner in sim.BANNER_IDS
            }
            for w, rows in records.items()
        }
        for world, banner in [("hos", "DICK’S Sporting Goods"), ("fast_break", "Foot Locker")]:
            pairs = [
                (a, b)
                for a, b in zip(records["baseline"], records[world])
                if channel == "all" or a["channel"] == channel
            ]

            def revenue(r):
                return (
                    r["purchase"]["revenue"]
                    if r["purchase"] and r["purchase"]["retailer"] == banner
                    else 0
                )

            delta = [revenue(b) - revenue(a) for a, b in pairs]
            mean = statistics.mean(delta) if delta else None
            se = statistics.stdev(delta) / math.sqrt(len(delta)) if len(delta) > 1 else None
            base = sum(revenue(a) for a, _ in pairs)
            new = sum(revenue(b) for _, b in pairs)
            comparisons.append(
                dict(
                    channel=channel,
                    scenario=world,
                    banner=banner,
                    n=len(pairs),
                    revenue_change_pct=100 * (new / base - 1) if base else None,
                    paired_revenue_per_occasion_delta=mean,
                    descriptive_95_interval=[mean - 1.96 * se, mean + 1.96 * se]
                    if se is not None
                    else None,
                )
            )
    for i, row in enumerate(records["baseline"]):
        if row["channel"] == "ecommerce":
            original = {k: v for k, v in row.items() if k != "scenario"}
            for world in design["worlds"]:
                if {k: v for k, v in records[world][i].items() if k != "scenario"} != original:
                    raise ValueError("Online control mismatch")
    sim.savejson(
        run / "analysis.json",
        dict(
            summary=summary,
            comparisons=comparisons,
            execution=read(run / "execution.json"),
            interpretation="Descriptive synthetic-sample intervals, not empirical forecasts or causal confidence intervals.",
        ),
    )
    print(f"Analysis saved to {run / 'analysis.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run", "replay", "analyze"])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, default=ROOT / "data/generated")
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.sqlite")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", default="example-001")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    elif args.command == "run":
        run(args)
    elif args.command == "replay":
        replay(args.run)
    else:
        analyze(args.run)


if __name__ == "__main__":
    main()
