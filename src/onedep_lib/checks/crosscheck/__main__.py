"""
Command line: python -m onedep_lib.checks.crosscheck FILE.cif [FILE.cif ...]

Exit status is 0 when no errors were found (warnings allowed), 1 when any
file has an error, and 2 when a file or the rule store cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from onedep_lib.checks.crosscheck.checks import Context, validate
from onedep_lib.checks.crosscheck.rules import DEFAULT_RULES_PATH, load_rules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m onedep_lib.checks.crosscheck",
        description="Run the cross-checks in schemas/cif/cross_checks.cif on mmCIF files (parsed with gemmi).",
    )
    parser.add_argument("files", nargs="+", type=Path, help="mmCIF files to check")
    parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES_PATH,
        help="rule store (default: onedep_lib/schemas/cif/cross_checks.cif)",
    )
    parser.add_argument(
        "--dict",
        dest="dictionary",
        type=Path,
        help="PDBx/mmCIF dictionary file; enables the dictionary-enumeration pairing rules",
    )
    parser.add_argument(
        "--expt",
        action="append",
        default=[],
        metavar="MODE",
        help="experiment mode for gated cross-reference rules: xray, nd, fd, ec, em, nmr, ssnmr",
    )
    parser.add_argument(
        "--subtype",
        action="append",
        default=[],
        metavar="SUBTYPE",
        help="entry subtype for makeMandatorySubtypes rules: EC, EM-helical, EM-single_part, EM-subtomogram",
    )
    parser.add_argument(
        "--code",
        action="append",
        default=[],
        metavar="CODE",
        help="requested accession code for 'coded' rules, e.g. PDB",
    )
    parser.add_argument(
        "--detect-context",
        action="store_true",
        help="also derive experiment modes and EM subtypes from _exptl.method and _em_experiment",
    )
    parser.add_argument(
        "--lenient", action="store_true", help="accept duplicate tags instead of rejecting the file (no .gz support)"
    )
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    parser.add_argument("--errors-only", action="store_true", help="omit warnings")
    args = parser.parse_args(argv)

    try:
        rules = load_rules(args.rules)
    except Exception as exc:  # noqa: BLE001 - report any rule-store failure and stop
        print(f"Cannot load rules from {args.rules}: {exc}", file=sys.stderr)
        return 2

    context = Context(
        {m.lower() for m in args.expt},
        set(args.subtype),
        {c.upper() for c in args.code},
    )

    status = 0
    report = {}
    for path in args.files:
        try:
            findings = validate(
                path,
                dictionary_path=args.dictionary,
                context=context,
                detect=args.detect_context,
                rules=rules,
                lenient=args.lenient,
            )
        except Exception as exc:  # noqa: BLE001 - gemmi raises RuntimeError/ValueError on bad CIF
            print(f"{path}: cannot read: {exc}", file=sys.stderr)
            status = 2
            continue
        if args.errors_only:
            findings = [f for f in findings if f.severity == "error"]
        if any(f.severity == "error" for f in findings):
            status = max(status, 1)
        if args.json:
            report[str(path)] = [f.as_dict() for f in findings]
            continue
        print(f"{path}: {len(findings)} issue(s)")
        for f in findings:
            print(f)
        print()

    if args.json:
        print(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
