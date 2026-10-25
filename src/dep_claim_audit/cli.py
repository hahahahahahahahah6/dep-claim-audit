"""Command-line interface."""
import argparse
import json
import sys

from . import __version__
from .audit import RULES, audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def main(argv=None):
    parser = Parser(prog="dep-claim-audit")
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on", choices=("HIGH", "MEDIUM"), default="MEDIUM")
    parser.add_argument("--ignore", default="", metavar="RULE,...")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    ignored = {r.strip() for r in args.ignore.split(',') if r.strip()}
    if ignored - RULES:
        parser.error("unknown ignored rule: " + ', '.join(sorted(ignored - RULES)))
    try:
        findings = audit(args.path, ignore=ignored)
    except (OSError, ValueError, SyntaxError) as exc:
        parser.error(str(exc))
    failed = any(f.severity == "HIGH" or args.fail_on == "MEDIUM" for f in findings)
    status = "FAIL" if failed else "PASS"
    if args.json:
        print(json.dumps({"status": status, "findings": [f.to_dict() for f in findings]}))
    else:
        for f in findings:
            print(f"{f.severity} {f.rule} {f.file}:{f.line} {f.message}")
        print(f"{status}: {len(findings)} finding(s)")
    return 2 if failed else 0
