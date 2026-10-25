"""Offline fixture-based regression tests; never import fixture code."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from dep_claim_audit.audit import audit, FALLBACK_STDLIB, lower_bound
from dep_claim_audit.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def project(self, code="", deps="[]", extra="", readme="", filename="src/demo/__init__.py"):
        self.put("pyproject.toml", '[project]\nname="fixture"\ndependencies=' + deps + '\n' + extra)
        self.put(filename, code)
        self.put("README.md", readme)

    def rules(self):
        return [f.rule for f in audit(self.root)]

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                code = main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, output.getvalue()

    def test_good_fixture(self):
        self.assertEqual(audit(FIXTURES / "good"), [])

    def test_bad_fixture_exact_rules(self):
        findings = audit(FIXTURES / "bad")
        self.assertEqual([f.rule for f in findings], ["imported-dep-undeclared", "requires-python-mismatch", "zero-dep-claim-false"])
        self.assertTrue(all(f.severity == "HIGH" for f in findings))

    def test_stdlib_claim_true(self):
        self.project("import os, json\n", readme="Stdlib-only.")
        self.assertEqual(self.rules(), [])

    def test_declared_unused(self):
        self.project(deps='["requests"]')
        self.assertEqual(self.rules(), ["declared-dep-unused"])
        self.assertEqual(audit(self.root)[0].severity, "MEDIUM")

    def test_test_only(self):
        self.project("import pytest\n", filename="tests/check.py")
        self.assertEqual(self.rules(), ["test-dep-undeclared"])

    def test_test_prefix(self):
        self.project("import pytest\n", filename="test_app.py")
        self.assertEqual(self.rules(), ["test-dep-undeclared"])

    def test_test_suffix(self):
        self.project("import pytest\n", filename="app_test.py")
        self.assertEqual(self.rules(), ["test-dep-undeclared"])

    def test_optional_declaration(self):
        self.project("import pytest\n", extra='[project.optional-dependencies]\ntest=["pytest"]\n', filename="tests/test_app.py")
        self.assertEqual(self.rules(), [])

    def test_dev_group(self):
        self.project("import pytest\n", extra='[dependency-groups]\ndev=["pytest", {include-group="lint"}]\n', filename="tests/test_app.py")
        self.assertEqual(self.rules(), [])

    def test_optional_does_not_declare_runtime(self):
        self.project("import requests\n", extra='[project.optional-dependencies]\nweb=["requests"]\n')
        self.assertEqual(self.rules(), ["imported-dep-undeclared"])

    def test_runtime_also_in_tests(self):
        self.project("import requests\n")
        self.put("tests/test_app.py", "import requests\n")
        self.assertEqual(self.rules(), ["imported-dep-undeclared"])

    def test_used_in_tests(self):
        self.project(deps='["requests"]')
        self.put("tests/test_app.py", "import requests\n")
        self.assertEqual(self.rules(), [])

    def test_requirements(self):
        self.project("import requests\n")
        self.put("requirements-dev.txt", "# comment\nrequests[socks]>=2; python_version >= '3.10'\n")
        self.assertEqual(self.rules(), [])

    def test_setup_cfg(self):
        self.project("import requests\n")
        self.put("setup.cfg", "[options]\ninstall_requires =\n requests>=2\n")
        self.assertEqual(self.rules(), [])

    def test_setup_extras(self):
        self.project("import pytest\n", filename="tests/test_app.py")
        self.put("setup.cfg", "[options.extras_require]\ntest =\n pytest\n")
        self.assertEqual(self.rules(), [])

    def test_allow_unused(self):
        self.project(deps='["my_plugin"]', extra='[tool.dep-claim-audit]\nallow-unused=["my-plugin"]\n')
        self.assertEqual(self.rules(), [])

    def test_local_src_package(self):
        self.project("import sibling\n")
        self.put("src/sibling/__init__.py", "")
        self.assertEqual(self.rules(), [])

    def test_local_root_module(self):
        self.project("import helper\n")
        self.put("helper.py", "")
        self.assertEqual(self.rules(), [])

    def test_relative_import(self):
        self.project("from .sibling import value\n")
        self.assertEqual(self.rules(), [])

    def test_from_import(self):
        self.project("from requests.sessions import Session\n", deps='["requests"]')
        self.assertEqual(self.rules(), [])

    def test_unknown_import(self):
        self.project("import completely_unknown_package\n")
        self.assertEqual(self.rules(), ["imported-dep-undeclared"])

    def test_normalization(self):
        self.project("import My_Package\n", deps='["my-package>=1"]')
        self.assertEqual(self.rules(), [])

    def test_description_claim(self):
        self.project("import requests\n", deps='["requests"]', extra='description="No dependencies"\n')
        self.assertEqual(self.rules(), ["zero-dep-claim-false"])

    def test_top_level_docs(self):
        self.project("import requests\n", deps='["requests"]')
        self.put("NOTES.md", "Zero dependencies")
        self.assertEqual(self.rules(), ["zero-dep-claim-false"])

    def test_nested_docs_not_read(self):
        self.project("import requests\n", deps='["requests"]')
        self.put("docs/notes.md", "Zero dependencies")
        self.assertEqual(self.rules(), [])

    def test_claim_boundaries(self):
        self.project("import requests\n", deps='["requests"]', readme="nonzero dependencies stdlib-onlyish")
        self.assertEqual(self.rules(), [])

    def test_dynamic_import_unresolved(self):
        self.project("import importlib\nname='requests'\nimportlib.import_module(name)\n__import__(name)\n")
        self.assertEqual(self.rules(), [])

    def test_conditional_import_counted(self):
        self.project("if False:\n import requests\n")
        self.assertEqual(self.rules(), ["imported-dep-undeclared"])

    def test_never_executes(self):
        self.project("raise RuntimeError('must never run')\n")
        self.assertEqual(self.rules(), [])

    def test_env_not_read(self):
        self.project()
        (self.root / ".env").write_bytes(b'\xff\xfe')
        self.assertEqual(self.rules(), [])

    def test_match_floor(self):
        self.project("match 1:\n case 1: pass\n", extra='requires-python=">=3.9"\n')
        f = audit(self.root)[0]
        self.assertEqual(f.rule, "requires-python-mismatch")
        self.assertIn("match", f.message)
        self.assertIn("3.9", f.message)
        self.assertEqual(f.line, 1)

    def test_union_annotation(self):
        self.project("def f(a: int | str) -> int | None: pass\n", extra='requires-python=">=3.9"\n')
        self.assertEqual(self.rules(), ["requires-python-mismatch"])

    def test_union_alias(self):
        self.project("Alias = int | str\n", extra='requires-python=">=3.9"\n')
        self.assertEqual(self.rules(), ["requires-python-mismatch"])

    def test_except_star(self):
        self.project("try: pass\nexcept* ValueError: pass\n", extra='requires-python=">=3.10"\n')
        self.assertEqual(self.rules(), ["requires-python-mismatch"])

    def test_tomllib(self):
        self.project("from tomllib import loads\n", extra='requires-python=">=3.10"\n')
        self.assertEqual(self.rules(), ["requires-python-mismatch"])

    @unittest.skipIf(sys.version_info < (3, 12), "requires a Python 3.12 parser")
    def test_type_statement(self):
        self.project("type Alias = int\n", extra='requires-python=">=3.11"\n')
        self.assertEqual(self.rules(), ["requires-python-mismatch"])

    def test_generics_zoneinfo_not_syntax(self):
        self.project("import zoneinfo\nx: list[int] = []\n", extra='requires-python=">=3.8"\n')
        self.assertEqual(self.rules(), [])

    def test_bitwise_not_annotation(self):
        self.project("result = 1 | 2\n", extra='requires-python=">=3.9"\n')
        self.assertEqual(self.rules(), [])

    def test_no_python_claim(self):
        self.project("match 1:\n case _: pass\n")
        self.assertEqual(self.rules(), [])

    def test_readme_python_fallback(self):
        self.project("match 1:\n case _: pass\n", readme="Python 3.9+")
        self.assertEqual(self.rules(), ["requires-python-mismatch"])

    def test_prose_mismatch(self):
        self.project(extra='requires-python=">=3.11"\n', readme="Requires Python >=3.10")
        self.assertEqual(self.rules(), ["requires-python-prose-mismatch"])

    def test_prose_same(self):
        self.project(extra='requires-python=">=3.10"\n', readme="Requires Python >=3.10")
        self.assertEqual(self.rules(), [])

    def test_pyproject_precedes_prose(self):
        self.project("match 1:\n case _: pass\n", extra='requires-python=">=3.10"\n', readme="Python 3.9+")
        self.assertEqual(self.rules(), ["requires-python-prose-mismatch"])

    def test_lower_bound(self):
        self.assertEqual(lower_bound(">=3.8,>=3.10,<4"), (3, 10))
        self.assertEqual(lower_bound("~=3.11"), (3, 11))
        self.assertIsNone(lower_bound("<4"))

    def test_fallback_stdlib(self):
        self.project("import json, asyncio, distutils, tomllib\n")
        with patch("dep_claim_audit.audit.sys.stdlib_module_names", FALLBACK_STDLIB):
            self.assertEqual(self.rules(), [])

    def test_cli_good(self):
        code, output = self.cli(str(FIXTURES / "good"))
        self.assertEqual(code, 0)
        self.assertIn("PASS", output)

    def test_cli_bad_json_sorted(self):
        code, output = self.cli(str(FIXTURES / "bad"), "--json")
        data = json.loads(output)
        self.assertEqual(code, 2)
        self.assertEqual(data["status"], "FAIL")
        self.assertEqual(len(data["findings"]), 3)
        self.assertEqual(data["findings"], sorted(data["findings"], key=lambda f: (f["rule"], f["file"], f["line"])))
        self.assertEqual(set(data["findings"][0]), {"severity", "rule", "file", "line", "message"})
        self.assertEqual(output, self.cli(str(FIXTURES / "bad"), "--json")[1])

    def test_cli_threshold(self):
        self.project(deps='["requests"]')
        code, output = self.cli(str(self.root), "--fail-on", "HIGH")
        self.assertEqual(code, 0)
        self.assertIn("declared-dep-unused", output)
        self.assertEqual(self.cli(str(self.root))[0], 2)

    def test_cli_ignore(self):
        code, output = self.cli(str(FIXTURES / "bad"), "--json", "--ignore", "zero-dep-claim-false")
        self.assertEqual(code, 2)
        self.assertEqual(len(json.loads(output)["findings"]), 2)

    def test_cli_ignore_all_pass(self):
        code, output = self.cli(str(FIXTURES / "bad"), "--ignore", "zero-dep-claim-false,imported-dep-undeclared,requires-python-mismatch")
        self.assertEqual(code, 0)

    def test_cli_usage(self):
        self.assertEqual(self.cli("--bogus")[0], 1)
        self.assertEqual(self.cli("--fail-on", "LOW")[0], 1)
        self.assertEqual(self.cli("--ignore", "bogus")[0], 1)

    def test_cli_version(self):
        self.assertEqual(self.cli("--version"), (0, "0.1.0\n"))

    def test_cli_missing_path(self):
        self.assertEqual(self.cli(str(self.root / "missing"))[0], 1)

    def test_cli_malformed_toml(self):
        self.put("pyproject.toml", "[")
        self.assertEqual(self.cli(str(self.root))[0], 1)

    def test_cli_invalid_source(self):
        self.project("def invalid(\n")
        self.assertEqual(self.cli(str(self.root))[0], 1)

    def test_cli_default_path(self):
        self.project()
        with patch("pathlib.Path.resolve", return_value=self.root):
            self.assertEqual(self.cli()[0], 0)


# Each alias, claim spelling, and excluded directory is an independently named test.
def alias_case(module, dep):
    def test(self):
        self.project(f"import {module}\n", deps=json.dumps([dep]))
        self.assertEqual(self.rules(), [])
    return test


for module, dep in [("PIL", "Pillow"), ("cv2", "opencv-python"), ("sklearn", "scikit-learn"), ("yaml", "PyYAML"), ("bs4", "beautifulsoup4")]:
    setattr(AuditTests, "test_alias_" + module, alias_case(module, dep))


def claim_case(claim):
    def test(self):
        self.project("import requests\n", deps='["requests"]', readme=claim.upper())
        self.assertEqual(self.rules(), ["zero-dep-claim-false"])
    return test


for index, claim in enumerate(["no dependencies", "zero dependencies", "zero-dep", "stdlib only", "stdlib-only", "no third-party dependencies"]):
    setattr(AuditTests, f"test_claim_pattern_{index}", claim_case(claim))


def skip_case(directory):
    def test(self):
        self.project()
        self.put(directory + "/bad.py", "import requests\n")
        self.assertEqual(self.rules(), [])
    return test


for index, directory in enumerate([".venv", "__pycache__", "dist", ".git", "demo.egg-info", "build"]):
    setattr(AuditTests, f"test_skip_directory_{index}", skip_case(directory))


if __name__ == "__main__":
    unittest.main()
