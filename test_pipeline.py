"""Offline integration and API-contract checks; no live calls or credentials."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generate_inputs import TRAIT_SCHEMA, LunaGenerator, build_bank, validate_bank
from jev_client import MODEL, JevClient

ROOT = Path(__file__).resolve().parent


class PipelineTests(unittest.TestCase):
    def test_openai_structured_generation_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            generator = LunaGenerator(temp)
            parsed = {
                "options": [
                    {
                        "id": "neutral",
                        "text": "fixture",
                        "eligible_product_types": ["footwear"],
                        "eligible_segments": ["running"],
                    }
                ]
            }
            response = {
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(parsed)}]}],
            }
            with (
                patch.dict("os.environ", {"OPENAI_API_KEY": "unit-test-placeholder"}),
                patch(
                    "urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
                ) as call,
            ):
                self.assertEqual(generator.generate("traits", "test", TRAIT_SCHEMA), parsed)
                request = json.loads(call.call_args.args[0].data)
                self.assertEqual(request["model"], "gpt-6-luna")
                self.assertFalse(request["store"])
                self.assertTrue(request["text"]["format"]["strict"])
            with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
                self.assertEqual(generator.generate("traits", "test", TRAIT_SCHEMA), parsed)
            saved = next(Path(temp).glob("*.json")).read_text()
            self.assertNotIn("unit-test-placeholder", saved)
            self.assertNotIn("Authorization", saved)

    def test_jev_replay_miss_never_calls_network(self):
        with tempfile.TemporaryDirectory() as temp:
            client = JevClient(temp, replay_only=True)
            with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
                with self.assertRaisesRegex(RuntimeError, "Replay cache miss"):
                    client.query({"model": MODEL, "state": {}, "questions": {}})

    def test_bank_rejects_incompatible_traits(self):
        bank = build_bank(None, 1, demo=True)
        bank["categories"]["patience"][0]["eligible_product_types"] = []
        with self.assertRaisesRegex(ValueError, "No compatible"):
            validate_bank(bank)

    def test_end_to_end_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            inputs = temp / "inputs"
            run = temp / "run"

            def command(*args, success=True):
                result = subprocess.run(
                    [sys.executable, *map(str, args)], cwd=ROOT, capture_output=True, text=True
                )
                if success:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                return result

            command("generate_inputs.py", "--demo", "--output", inputs)
            command(
                "catalog_io.py",
                "--demo",
                "--input",
                temp / "offers.jsonl",
                "--output",
                temp / "catalog.sqlite",
                "--as-of",
                "2000-01-01",
            )
            command(
                "simulate.py",
                "prepare",
                "--run",
                run,
                "--inputs",
                inputs,
                "--catalog",
                temp / "catalog.sqlite",
                "--count",
                "8",
                "--seed",
                "test",
            )
            command("simulate.py", "run", "--run", run, "--demo", "--workers", "2")
            command("simulate.py", "run", "--run", run, "--demo", "--workers", "2")
            command("simulate.py", "replay", "--run", run)
            command("simulate.py", "analyze", "--run", run)
            output = json.loads((run / "analysis.json").read_text())
            self.assertEqual(output["execution"]["mode"], "fixture")
            self.assertEqual(len(list((run / "episodes").glob("*.json"))), 32)
            (run / "retailer_profiles.json").write_text("{}")
            result = command("simulate.py", "run", "--run", run, "--demo", success=False)
            self.assertIn("Frozen input changed", result.stderr)


if __name__ == "__main__":
    unittest.main()
