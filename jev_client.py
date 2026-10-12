"""Typed Jev retailer/basket policy. No chat generation or Qwen fallback."""

import hashlib
import json
import math
import os
import shlex
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def configured_key():
    """Only the named Jev credential; never execute or log an environment file."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    root = Path(__file__).resolve().parent
    for path in [root / ".env"]:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.removeprefix("export ").partition("=")
            if sep and key.strip() == "TYPESAFE_API_KEY":
                tokens = shlex.split(value, comments=True)
                if len(tokens) == 1:
                    return tokens[0]
    return None


def request_for(state):
    """Retailer choice and explicitly conditional basket questions share state."""
    groups = {}
    for a in state["alternatives"]:
        r = a["id"].split("_")[0]
        groups.setdefault(r, []).append(a)
    questions = {}
    if len(groups) > 1:
        questions["retailer"] = dict(
            type="choice",
            instructions=(
                "Given this shopper and this occasion, which retailer would they actually buy from, "
                "or would they make no purchase? Predict their behavior, not an ideal recommendation. "
                "Use only the supplied hypothetical offers, preferences and constraints. "
                "Do not favor any retailer or assume a purchase must occur."
            ),
            criteria={
                r: (
                    "Postpone or make no purchase during this occasion."
                    if r == "NONE"
                    else dict(retailer=alts[0]["retailer_name"], available_baskets=alts)
                )
                for r, alts in groups.items()
            },
        )
    for r, alts in groups.items():
        if len(alts) > 1:
            questions["basket_" + r] = dict(
                type="choice",
                instructions=(
                    f"Conditional on this shopper making a purchase from {alts[0]['retailer_name']}, "
                    "which of its listed baskets would they buy? Use their accessory interest and "
                    "budget. This question is conditional; it does not imply they choose this retailer."
                ),
                criteria={a["id"]: a for a in alts},
            )
    return dict(model=MODEL, state=state, questions=questions)


def validate_answer(answer, labels):
    assert answer["type"] == "choice"
    p = answer["probabilities"]
    assert set(p) == set(labels), "Wrong alternatives in response"
    assert all(isinstance(x, (int, float)) and math.isfinite(x) and 0 <= x <= 1 for x in p.values())
    total = sum(p.values())
    # The live API emits probabilities rounded to 0.01. Preserve raw values,
    # accepting only the maximum sum error from independent nearest rounding.
    rounded = all(abs(x - round(x, 2)) < 1e-10 for x in p.values())
    tolerance = 0.005 * len(p) + 1e-8 if rounded else 1e-5
    assert total > 0 and abs(total - 1) <= tolerance, "Invalid probability sum"
    # Preserve vendor choice even when it differs from the rounded modal option.
    # The simulator samples the full distribution, never this convenience field.
    assert answer["choice"] in p
    confidence = answer["confidence"]
    assert math.isfinite(confidence) and 0 <= confidence <= 1
    return {k: v / total for k, v in p.items()}


def joint_probabilities(request, response):
    assert response["model"] == request["model"], "Unexpected model version"
    assert set(response["answers"]) == set(request["questions"])
    checked = {
        q: validate_answer(response["answers"][q], spec["criteria"])
        for q, spec in request["questions"].items()
    }
    groups = {}
    for a in request["state"]["alternatives"]:
        groups.setdefault(a["id"].split("_")[0], []).append(a["id"])
    retailer = checked.get("retailer", {next(iter(groups)): 1.0})
    joint = {}
    for r, alts in groups.items():
        basket = checked.get("basket_" + r, {alts[0]: 1.0})
        for a in alts:
            joint[a] = retailer[r] * basket[a]
    assert abs(sum(joint.values()) - 1) < 2e-5
    return joint


class JevClient:
    def __init__(self, cache, replay_only=False):
        self.replay_only = replay_only
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

    def query(self, request):
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        path = self.cache / (key + ".json")
        if path.exists():
            saved = json.loads(path.read_text())
            assert saved["request"] == request
            return saved["response"], key
        if self.replay_only:
            raise RuntimeError("Replay cache miss; network calls are disabled.")
        if not request["questions"]:
            response = dict(model=MODEL, answers={}, usage=dict(input_tokens=0, output_tokens=0))
            saved = dict(
                request=request, response=response, source="deterministic_single_legal_option"
            )
        else:
            api_key = configured_key()
            if not api_key:
                raise RuntimeError(
                    "TYPESAFE_API_KEY is not configured; no fallback model will run."
                )
            req = urllib.request.Request(
                ENDPOINT,
                data=json.dumps(request).encode(),
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            )
            started = time.monotonic()
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=45) as r:
                        response = json.load(r)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code not in [429, 500, 502, 503, 504, 529] or attempt == 3:
                        raise RuntimeError(
                            f"TypeSafe HTTP {exc.code}; no fallback model will run."
                        ) from None
                    try:
                        retry_after = float(exc.headers.get("Retry-After", "0"))
                    except ValueError:
                        retry_after = 0
                    time.sleep(min(30, max(retry_after, 2**attempt)))
            saved = dict(
                request=request,
                response=response,
                source="typesafe_api",
                elapsed_seconds=time.monotonic() - started,
            )
        # Preserve raw responses even when they fail semantic validation.
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(saved))
        temp.replace(path)
        return response, key

    def ask(self, request):
        response, key = self.query(request)
        return joint_probabilities(request, response), key, response
