"""Sequential shopping engine with paired format scenarios and seeded actions."""

import hashlib
import json
import re
import sqlite3
import threading
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from jev_client import MODEL, JevClient, validate_answer
from retailer_profiles import consumer_context

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data/runs/default"
COHORT = OUT / "cohort"
DB = ROOT / "data/catalog.sqlite"
WORLDS = ["baseline", "hos", "fast_break", "both"]
MAX_STEPS = 24
BANNER_IDS = {
    "DICK’S Sporting Goods": "dks",
    "Foot Locker": "foot_locker",
    "Golf Galaxy": "golf_galaxy",
    "Public Lands": "public_lands",
    "Going Going Gone": "going_going_gone",
    "Champs": "champs",
    "Kids Foot Locker": "kids_foot_locker",
    "Academy": "academy",
    "JD Sports": "jd",
    "Finish Line": "finish_line",
    "Hibbett": "hibbett",
    "Scheels": "scheels",
    "New Balance": "new_balance",
    "adidas": "adidas",
}
RULES = (
    "Predict this particular shopper’s next action, not an ideal shopping recommendation. "
    "Use their mission, constraints, observed merchandise and experience. "
    "Only choose a supplied legal action. They may explore, switch, defer or leave without buying. "
    "Do not optimize retailer revenue. Store descriptions are scenario context; do not invent further stock, discounts, loyalty benefits or features. "
    "Respect the hard total budget and time allowance. Use the history to avoid repetitive actions. "
    "Fit and availability are only known once revealed. A pleasant experience does not require a purchase."
)


def dumps(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def hashval(x):
    return hashlib.sha256(dumps(x).encode()).hexdigest()


def uniform(*parts):
    return int(hashval(parts)[:13], 16) / 16**13


def tokens(s):
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def savejson(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


class OfferIndex:
    def __init__(self, database=DB):
        db = sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        self.pool = defaultdict(list)
        self.addons = defaultdict(list)
        for r in db.execute(
            "SELECT * FROM snapshot WHERE price>0 AND currency='USD' AND family IN ('historical_variant','competitor_offer','launch_listing')"
        ):
            d = dict(r)
            raw = json.loads(d.pop("raw_json"))
            d["gender"] = raw.get("gender", "")
            d["words"] = tokens(d["name"])
            self.pool[d["category"], d["retailer"]].append(d)
            if d["category"] == "clothing" or "socks" in d["words"]:
                self.addons[d["retailer"]].append(d)
        self.source_hash = db.execute(
            "SELECT value FROM metadata WHERE key='sources_sha256'"
        ).fetchone()[0]
        self.asof = db.execute("SELECT value FROM metadata WHERE key='as_of'").fetchone()[0]
        db.close()

    @staticmethod
    def size_status(p, o):
        spec = p["occasion"]["sizing_or_specification"]
        kind = p["product_type"]
        if kind not in ("footwear", "cleats", "clothing"):
            return "not_applicable"
        if not o["size"]:
            return "unknown"
        size = o["size"].lower()
        gender = (o["gender"] + " " + o["name"]).lower()
        if kind == "clothing":
            required = re.search(r"Adult (XS|S|M|L|XL|XXL)", spec)
            if not required:
                return "unknown"
            mapping = {
                "small": "S",
                "medium": "M",
                "large": "L",
                "x-large": "XL",
                "x-small": "XS",
                "xx-large": "XXL",
            }
            observed = mapping.get(size, size.upper())
            return (
                "matched"
                if observed == required.group(1)
                else ("mismatch" if observed in ["XS", "S", "M", "L", "XL", "XXL"] else "unknown")
            )
        m = re.search(r"US (men|women|youth) ([0-9.]+)", spec)
        if not m:
            return "unknown"
        system, num = m.group(1), float(m.group(2))
        child = any(
            t in gender for t in ["youth", "kid", "boy", "girl", "junior", "toddler", "infant"]
        )
        if system != "youth" and child:
            return "mismatch"
        if system == "youth" and not child:
            return "mismatch" if o["gender"] else "unknown"
        if system == "youth" and any(t in gender for t in ["toddler", "infant"]):
            return "mismatch"
        if system == "men" and ("women" in gender) and "men's|women" not in gender:
            return "mismatch"
        if system == "women" and re.search(r"\bmen\b", gender) and "women" not in gender:
            return "mismatch"
        if "/" in size:
            z = (
                re.search(("m" if system == "men" else "w") + r"([0-9.]+)", size)
                if system != "youth"
                else None
            )
            if z:
                return "matched" if float(z.group(1)) == num else "mismatch"
            return "unknown"
        if re.fullmatch(r"\d+(\.\d+)?", size):
            return "matched" if float(size) == num else "mismatch"
        return "unknown"

    def selection(self, p, retailer, addon=False):
        pool = (
            self.addons.get(retailer, [])
            if addon
            else self.pool.get((p["product_type"], retailer), [])
        )
        target = tokens(p["occasion"]["target_product_family"] or "") - {
            "asics",
            "nike",
            "adidas",
            "wilson",
            "titleist",
        }
        hints = {
            "running": {
                "running",
                "run",
                "runner",
                "pegasus",
                "brooks",
                "asics",
                "hoka",
                "saucony",
            },
            "golf": {"golf", "driver", "pro", "titleist"},
            "school": {"school", "kids", "youth"},
            "lifestyle": {"sneaker", "casual", "jordan", "samba"},
            "team_sports": {"basketball", "soccer", "training"},
            "browse": set(),
        }[p["segment"]]
        grouped = {}
        for o in pool:
            if o["price"] * 1.08 > p["occasion"]["hard_spend_cap"]:
                continue
            if any(
                t in o["availability"].lower() for t in ["not_buyable", "outofstock", "soldout"]
            ):
                continue
            if target and not addon and not target.issubset(o["words"]):
                continue
            status = "unknown" if addon else self.size_status(p, o)
            if status == "mismatch":
                continue
            # Collapse only within a seller; retain the chosen actual variant's ID and price.
            product = o["product_key"] or o["name"]
            score = (3 if status == "matched" else 0) + len(o["words"] & hints)
            if addon:
                score = 2 if "socks" in o["words"] else 0
            rank = (-score, uniform("offer_rank", p["shopper_id"], product), o["observation_id"])
            if product not in grouped or rank < grouped[product][0]:
                grouped[product] = (rank, o, status)
        ranked = sorted(grouped.values(), key=lambda x: x[0])
        result = []
        for rank, o, status in ranked:
            result.append([o["observation_id"], status])
        return result


class World:
    def __init__(self, prepared, scenario):
        self.data = prepared
        self.p = prepared["profile"]
        self.scenario = scenario
        self.physical = self.p["occasion"]["channel"] == "physical"
        self.location = None
        self.minutes = 0
        self.cart = []
        self.visible = {}
        self.checked = {}
        self.fit = {}
        self.pages = defaultdict(int)
        self.browsed = set()
        self.extra_seen = set()
        self.entered = []
        self.history = []
        self.done = False
        self.purchase = None
        self.activity = False
        self.store_memory = {}

    def format(self, r):
        if self.physical and r == "DICK’S Sporting Goods" and self.scenario in ("hos", "both"):
            return "house_of_sport"
        if self.physical and r == "Foot Locker" and self.scenario in ("fast_break", "both"):
            return "fast_break"
        return BANNER_IDS[r]

    def total(self):
        return round(sum(o["price"] for o in self.cart) * 1.08, 2)

    def observation(self):
        return dict(
            shopper={k: self.p[k] for k in ["stable_preferences", "occasion"]},
            time_remaining=self.p["occasion"]["time_available_minutes"] - self.minutes,
            basket=self.cart,
            basket_total_including_assumed_tax=self.total(),
            store=consumer_context(
                self.format(self.location), "physical" if self.physical else "ecommerce"
            )
            if self.location
            else None,
            visible_products=list(self.visible.values()),
            known_size_availability=self.checked,
            known_fit=self.fit,
            recent_history=self.history[-6:],
        )

    def actions(self):
        actions = {
            "leave": dict(
                description="Finish this shopping occasion without another purchase.", minutes=0
            )
        }
        left = self.p["occasion"]["time_available_minutes"] - self.minutes

        def add(key, text, cost, **kw):
            if cost <= left:
                actions[key] = dict(description=text, minutes=cost, **kw)

        if self.location is None:
            for r in BANNER_IDS:
                cost = self.data["access_minutes"][r] if self.physical else 1
                add(
                    "visit:" + r,
                    "Visit this retailer to explore the shopping mission.",
                    cost,
                    retailer_context=consumer_context(
                        self.format(r), "physical" if self.physical else "ecommerce"
                    ),
                )
            return actions
        r = self.location
        add(
            "switch",
            "Leave this retailer and consider another; clear this retailer’s unpaid basket.",
            1,
        )
        if r not in self.browsed:
            add(
                "browse", "Browse the relevant department and its first page of actual products.", 3
            )
        elif self.pages[r] * 6 < len(self.data["offers"][r]):
            add("more", "Browse the next page of relevant products.", 3)
        if r not in self.extra_seen:
            add("extras", "Browse complementary apparel/accessories if useful for this mission.", 3)
        if self.format(r) == "house_of_sport" and not self.activity:
            add(
                "activity",
                "Spend time exploring a sporting activity; no purchase required. Scenario assumes a free 10-minute introductory activity.",
                10,
            )
        for oid, o in self.visible.items():
            if self.checked.get(oid) is None:
                add(
                    "check:" + oid,
                    "Check the required size/specification and availability for " + o["name"],
                    1,
                )
            elif self.checked[oid] and oid not in [x["observation_id"] for x in self.cart]:
                if self.physical and oid not in self.fit:
                    add(
                        "try:" + oid,
                        "Try or inspect " + o["name"] + " for suitability.",
                        4 if self.format(r) != "house_of_sport" else 5,
                    )
                elif not self.physical or self.fit.get(oid):
                    if (sum(x["price"] for x in self.cart) + o["price"]) * 1.08 <= self.p[
                        "occasion"
                    ]["hard_spend_cap"]:
                        add("add:" + oid, "Add " + o["name"] + " to the basket.", 1)
        for o in self.cart:
            add("remove:" + o["observation_id"], "Remove " + o["name"] + " from the basket.", 1)
        if self.cart:
            add("checkout", "Buy the basket at the displayed total, ending this occasion.", 2)
        return actions

    def reveal(self, r, start, items):
        for o in items[start : start + 6]:
            self.visible[o["observation_id"]] = o

    def step(self, key, action):
        self.minutes += action["minutes"]
        result = {}
        if key == "leave":
            self.done = True
            result["terminal"] = "no_purchase"
        elif key.startswith("visit:"):
            self.location = key[6:]
            self.entered.append(self.location)
            self.visible = self.store_memory.setdefault(self.location, {})
            self.cart = []
            result["entered"] = self.location
        elif key == "switch":
            self.location = None
            self.visible = {}
            self.cart = []
        elif key in ("browse", "more"):
            r = self.location
            self.reveal(r, self.pages[r] * 6, self.data["offers"][r])
            self.pages[r] += 1
            self.browsed.add(r)
            if self.format(r) == "fast_break" and r not in self.extra_seen:
                for o in self.data["addons"][r][:2]:
                    self.visible[o["observation_id"]] = o
                self.extra_seen.add(r)
            result["products_visible"] = len(self.visible)
        elif key == "extras":
            for o in self.data["addons"][self.location][:6]:
                self.visible[o["observation_id"]] = o
            self.extra_seen.add(self.location)
            result["products_visible"] = len(self.visible)
        elif key == "activity":
            self.activity = True
            result["experience"] = "Completed introductory sporting activity; no purchase required."
        elif key.startswith("check:"):
            oid = key[6:]
            o = self.visible[oid]
            known = o["size_evidence"] in ("matched", "not_applicable") and o["availability"] in (
                "web_buyable",
                "https://schema.org/InStock",
            )
            ok = known or uniform("availability", self.p["shopper_id"], o["name"]) < 0.75
            self.checked[oid] = ok
            result = {
                "item": oid,
                "required_variant_available_in_scenario": ok,
                "evidence": "archived variant status"
                if known
                else "seeded hypothetical size/availability completion",
            }
        elif key.startswith("try:"):
            oid = key[4:]
            o = self.visible[oid]
            ok = uniform("fit", self.p["shopper_id"], o["name"]) < 0.8
            self.fit[oid] = ok
            result = {
                "item": oid,
                "suitable_in_scenario": ok,
                "evidence": "seeded hypothetical trial outcome; not empirical fit",
            }
        elif key.startswith("add:"):
            self.cart.append(self.visible[key[4:]])
        elif key.startswith("remove:"):
            self.cart = [o for o in self.cart if o["observation_id"] != key[7:]]
        elif key == "checkout":
            assert self.total() <= self.p["occasion"]["hard_spend_cap"]
            self.purchase = dict(
                retailer=self.location,
                items=deepcopy(self.cart),
                revenue=round(sum(o["price"] for o in self.cart), 2),
                total=self.total(),
            )
            self.done = True
            result["terminal"] = "purchase"
        else:
            raise ValueError(key)
        assert self.minutes <= self.p["occasion"]["time_available_minutes"]
        self.history.append(dict(action=key, result=result))
        return result


class Policy:
    def __init__(self, cache=OUT / "cache", replay_only=False):
        self.client = JevClient(cache, replay_only=replay_only)
        self.guard = threading.Lock()
        self.locks = {}

    def choose(self, state, actions):
        request = dict(
            model=MODEL,
            state=state,
            questions={"next_action": dict(type="choice", instructions=RULES, criteria=actions)},
        )
        key = hashval(request)
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            if len(actions) == 1:
                return {next(iter(actions)): 1.0}, None
            response, cachekey = self.client.query(request)
            assert response["model"] == MODEL
            return validate_answer(response["answers"]["next_action"], actions), cachekey


def episode(prepared, scenario, policy):
    world = World(prepared, scenario)
    trace = []
    p = prepared["profile"]
    for step in range(MAX_STEPS):
        obs = world.observation()
        actions = world.actions()
        probs, key = policy.choose(obs, actions)
        u = uniform("action", p["shopper_id"], step)
        cumulative = 0
        chosen = list(probs)[-1]
        for a, prob in probs.items():
            cumulative += prob
            if u < cumulative:
                chosen = a
                break
        result = world.step(chosen, actions[chosen])
        trace.append(
            dict(
                step=step,
                request_cache_key=key,
                actions=actions,
                probabilities=probs,
                draw=u,
                chosen=chosen,
                result=result,
            )
        )
        if world.done:
            break
    return dict(
        consumer=p["shopper_id"],
        scenario=scenario,
        channel=p["occasion"]["channel"],
        segment=p["segment"],
        entered=world.entered,
        minutes=world.minutes,
        activity=world.activity,
        purchase=world.purchase,
        terminal="purchase"
        if world.purchase
        else ("no_purchase" if world.done else "step_cap_censored"),
        trace=trace,
    )
