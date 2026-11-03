#!/usr/bin/env python3
"""Validate BedLight AI JSON Schema contracts.

Checks:
  1. Every contracts/v*/*.schema.json is a valid Draft 2020-12 schema.
  2. (default) examples/valid/*  MUST validate against their schema.
     examples/invalid/* MUST be rejected by their schema.
     Example -> schema mapping by filename prefix: event-* -> event-envelope, status-* -> device-status.
  3. topics.json references existing schema files.

Usage:
  python scripts/validate_contracts.py               # schemas + examples
  python scripts/validate_contracts.py --schemas-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
PREFIX_TO_SCHEMA = {
    "event-": "event-envelope.schema.json",
    "status-": "device-status.schema.json",
    "recording-": "voice-recording-response.schema.json",
}


def load(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def schema_for(example: Path, version_dir: Path) -> Path:
    for prefix, schema_name in PREFIX_TO_SCHEMA.items():
        if example.name.startswith(prefix):
            return version_dir / schema_name
    raise SystemExit(f"[contracts] no schema mapping for example {example}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas-only", action="store_true")
    args = parser.parse_args()

    errors: list[str] = []
    version_dirs = sorted(p for p in CONTRACTS.glob("v*") if p.is_dir())
    if not version_dirs:
        print("[contracts] FAIL: no contracts/v* directory")
        return 1

    for vdir in version_dirs:
        schemas = sorted(vdir.glob("*.schema.json"))
        if not schemas:
            errors.append(f"{vdir}: no *.schema.json files")
        for s in schemas:
            try:
                Draft202012Validator.check_schema(load(s))
                print(f"[schema ] OK   {s.relative_to(ROOT)}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{s.relative_to(ROOT)}: invalid schema: {e}")

        topics = vdir / "topics.json"
        if topics.exists():
            for t in load(topics)["topics"]:
                if not (vdir / t["schema"]).exists():
                    errors.append(f"topics.json: {t['pattern']} references missing {t['schema']}")

        if args.schemas_only:
            continue

        for kind, must_pass in (("valid", True), ("invalid", False)):
            examples = sorted((vdir / "examples" / kind).glob("*.json"))
            if not examples:
                errors.append(f"{vdir.relative_to(ROOT)}/examples/{kind}: no examples")
            for ex in examples:
                validator = Draft202012Validator(load(schema_for(ex, vdir)), format_checker=FormatChecker())
                errs = sorted(validator.iter_errors(load(ex)), key=lambda e: list(e.path))
                rel = ex.relative_to(ROOT)
                if must_pass and errs:
                    errors.append(f"{rel}: expected VALID but got: {errs[0].message}")
                elif not must_pass and not errs:
                    errors.append(f"{rel}: expected INVALID but schema accepted it")
                else:
                    detail = f" ({errs[0].message[:80]})" if errs else ""
                    print(f"[example] OK   {rel}{detail}")

    if errors:
        print("\n[contracts] FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\n[contracts] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
