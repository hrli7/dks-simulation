"""Scan tracked files without printing secret values."""

import re
import subprocess
from pathlib import Path

PATTERNS = [
    r"api" + r"key_[A-Za-z0-9_]{20,}",
    r"\bsk-" + r"[A-Za-z0-9_-]{20,}",
    r"\bgh[pousr]_" + r"[A-Za-z0-9]{20,}",
    r"github_pat_" + r"[A-Za-z0-9_]{20,}",
    r"AKIA" + r"[A-Z0-9]{16}",
    r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
]


def main():
    root = Path(__file__).resolve().parent
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    failures = []
    for name in filter(None, names):
        if (
            (name.startswith("data/") and name != "data/README.md")
            or name.startswith(("results/", "runs/"))
            or (Path(name).name.startswith(".env") and name != ".env.example")
        ):
            failures.append((name, "private/generated path"))
        content = subprocess.check_output(["git", "show", ":" + name], cwd=root).decode(
            errors="replace"
        )
        if any(re.search(pattern, content) for pattern in PATTERNS):
            failures.append((name, "credential pattern"))
        if "/" + "Users/" in content:
            failures.append((name, "personal absolute path"))
    if failures:
        for name, reason in failures:
            print(f"{name}: {reason}")
        raise SystemExit(1)
    print("Tracked-file check passed: no forbidden data paths or recognized credentials.")


if __name__ == "__main__":
    main()
