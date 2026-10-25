# dep-claim-audit

An offline CLI that checks a Python project's own dependency and Python-version
claims against its repository. Static analysis only: project code is never
executed, the network is never contacted, and `.env` contents are never read.
The tool has no runtime dependencies and requires Python >=3.11.

## Usage and examples

```sh
python3 -m pip install dep-claim-audit
dep-claim-audit .
dep-claim-audit /path/to/project --json
dep-claim-audit . --fail-on HIGH
dep-claim-audit . --ignore declared-dep-unused
dep-claim-audit --version
```

The default path is `.`. Human output is one line per finding:
`SEVERITY rule file:line message`, followed by a `PASS` or `FAIL` summary.
JSON output is a single object with `status` (`PASS` or `FAIL`) and `findings`.
Each finding has `severity`, `rule`, `file`, `line`, and `message`; findings are
sorted by rule, file, and line (message breaks ties).

Exit codes: **0** PASS, **2** FAIL, **1** usage/input error. The default
`--fail-on MEDIUM` fails on HIGH or MEDIUM findings. `--fail-on HIGH` still
prints MEDIUM findings but does not fail because of them. `--ignore RULE,...`
drops those rules entirely; unknown rule names are usage errors. Use it for
plugin dependencies when appropriate, or prefer the specific allow list below.

```sh
dep-claim-audit tests/fixtures/good --json  # PASS, exit 0
dep-claim-audit tests/fixtures/bad --json   # FAIL, exit 2, exactly three HIGH rules
```

The good fixture declares and imports `requests`, and uses `match` with a
Python 3.10 lower bound. The bad fixture claims zero dependencies, imports
undeclared `requests`, and uses `match` with a Python 3.9 lower bound. Neither
fixture is executed and neither requires installing requests. A separate test
covers a true stdlib-only claim.

## Rule table

| Rule | Severity | Trigger |
| --- | --- | --- |
| `zero-dep-claim-false` | HIGH | README/top-level Markdown docs or pyproject description claims “no dependencies”, “zero dependencies”, “zero-dep”, “stdlib only”, “stdlib-only”, or “no third-party dependencies”, but pyproject `dependencies` is non-empty or non-test source imports a third-party module. |
| `imported-dep-undeclared` | HIGH | Non-test source imports a third-party module absent from pyproject dependencies, requirements*.txt, and setup.cfg install_requires. Test-only imports never trigger this rule. |
| `declared-dep-unused` | MEDIUM | A declared runtime dependency (pyproject, requirements, or setup.cfg) is never imported in source or tests. Plugins/entry-point dependencies must be explicitly exempted through `allow-unused`; they cannot be inferred from imports. |
| `test-dep-undeclared` | MEDIUM | A third-party module imported ONLY in tests/ (or test_*.py / *_test.py) is not declared anywhere, including optional-dependencies, dependency-groups, or setup.cfg extras. |
| `requires-python-mismatch` | HIGH | The effective Python lower bound is older than a certain feature used in non-test source; reports the feature, file:line, and claimed bound. Uses pyproject requires-python first, otherwise README “Requires Python >=X” or “Python 3.X+”; no claim means no finding. |
| `requires-python-prose-mismatch` | MEDIUM | README “Requires Python >=X” (also “Python 3.X+”) disagrees with the pyproject requires-python lower bound. |

### Conservative feature floor table

| Python | Features checked |
| --- | --- |
| 3.9 | No syntax finding for builtin generic annotations in evaluated positions (too noisy). `zoneinfo` imports are not syntax features and are skipped. |
| 3.10 | `match` statements (`ast.Match`); `X \| Y` union annotations/aliases (`ast.BinOp` with `BitOr` on names/subscripts, also `None`). |
| 3.11 | `except*` (`ast.TryStar`); `tomllib` import (stdlib since 3.11). |
| 3.12 | `type` statements (`ast.TypeAlias`). F-string nested same-quote reuse is not flagged (too noisy). |

### Claim patterns and import mapping

The following case-insensitive, word-boundary regular expressions are used
literally for prose claim detection. Whitespace and hyphens are literal:

```text
\bno dependencies\b
\bzero dependencies\b
\bzero-dep\b
\bstdlib only\b
\bstdlib-only\b
\bno third-party dependencies\b
```

Only README and other top-level `*.md` files, plus pyproject description, are
searched for claims; nested documentation is not searched. Python files are
scanned recursively, excluding `.venv`, `venv`, `__pycache__`, `dist`, `build`,
`.git`, hidden directories and `*.egg-info`. Symlinked input files are skipped. Test paths include `tests/`,
`test/`, `test_*.py`, and `*_test.py`. Relative imports and top-level names
matching local directories/packages or `.py` modules at the root or in `src/`
are treated as local. Non-literal dynamic imports produce no findings.

Import/distribution names are lowercased and runs of hyphens, underscores, or
dots are equivalent. The complete small alias table is:

| Import | Distribution |
| --- | --- |
| PIL | Pillow |
| cv2 | opencv-python |
| sklearn | scikit-learn |
| yaml | PyYAML |
| bs4 | beautifulsoup4 |

Unknown third-party imports remain reportable as undeclared. Manifest inputs
are pyproject dependencies and optional-dependencies, PEP 735 dependency-groups,
root requirements*.txt files, and setup.cfg install_requires/extras_require.
Requirements are read as text, never installed. Optional/dev declarations
satisfy test imports but not runtime imports. To exempt plugin/entry-point
packages from unused checks, declare them explicitly:

```toml
[tool.dep-claim-audit]
allow-unused = ["my-plugin", "pytest-cov"]
```

## Differentiation

- **vs phantom-deps:** that checks whether referenced packages EXIST on PyPI
  (hallucinated deps, needs the registry); this checks whether the project's
  OWN claims match its OWN source, offline, and never asks whether a package exists.
- **vs dep-audit / dep-audit-cli:** those answer “which deps can I remove / replace,
  are they vulnerable or outdated” across ecosystems and lockfiles; this answers
  only “are this Python project's dependency claims true” — no removal
  suggestions, no vulns, no outdated checks, no lockfile, no network.
- **vs pip-check / pipdeptree:** those inspect the INSTALLED environment at
  runtime; this audits the REPO's claims statically, before install.
- **vs readme-claim-audit:** that audits README install names, flags, links,
  versions, make targets, module paths; this audits dependency claims and
  requires-python — no overlap in rules.

## Honest limitations

- Static import scanning misses dynamic imports. Calls such as
  `importlib.import_module(variable)` and `__import__(variable)` are not resolved
  and produce no finding. Even literal dynamic calls are outside this scanner.
  PASS means no detected contradiction, not proof of dependency freedom.
- Conditional/optional imports are still counted as imports. A finding may need
  a human to confirm the import is truly unconditional.
- Prose claim detection is pattern-based, not semantic. A sentence like
  “we removed all dependencies in v2” in a changelog section could false-positive.
  Prose search is scoped to README + top-level docs (and project description);
  the exact regex list is documented above. Negation and code fences are not
  interpreted.
- The stdlib set depends on the running interpreter version:
  `sys.stdlib_module_names` when available, otherwise a bundled fallback covering
  Python 3.8–3.12 top-level names. A removed or newly added stdlib module can be
  classified differently on another interpreter.
- The import→distribution alias table is small and documented above. Namespace
  packages and distributions exposing unrelated import names may require manual
  review. A local directory with the same name can hide a third-party import.
- This is a minimal syntax feature table, not a complete compatibility checker.
  Source must parse on the running Python interpreter; to inspect Python 3.12
  syntax, run on Python 3.12 or newer. Unparseable source is an input error,
  never a silent PASS. Union expressions used as assignments are treated as
  aliases; ordinary name-based bitwise OR assignments can need human review.
  Future annotations do not suppress the specified union feature check.
- Python lower bounds are extracted from explicit `>=`, `>`, `~=`, or `==`
  major.minor constraints. Patch-level distinctions, exclusions and complex
  version-range satisfiability are not modeled. Strict `>` is conservatively
  reduced to its written major.minor floor.
- Requirements includes, editable installs, URL-only requirements, environment
  markers, group includes, computed setup.py metadata and transitive dependencies
  are not resolved. Marked declarations still count. Plugin behavior cannot be
  discovered statically; use `allow-unused` or `--ignore` deliberately.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
# If isolated build dependencies are unavailable offline:
python3 -m build --no-isolation
```

The package uses a src layout, setuptools build-meta, and the MIT license.
