"""Static, offline dependency claim checks. Project code is never imported."""
import ast
import configparser
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys
import tomllib

ALIASES = {"pil": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn",
           "yaml": "pyyaml", "bs4": "beautifulsoup4"}
CLAIM_PATTERNS = (
    r"\bno dependencies\b", r"\bzero dependencies\b", r"\bzero-dep\b",
    r"\bstdlib only\b", r"\bstdlib-only\b", r"\bno third-party dependencies\b",
)
PROSE_VERSION = re.compile(r"\b(?:Requires\s+Python\s*>=\s*(\d+\.\d+)|Python\s+(\d+\.\d+)\+)", re.I)
# Union of public top-level stdlib modules across Python 3.8–3.12, including
# platform-specific and subsequently removed modules. Used only on older hosts.
FALLBACK_STDLIB = set('''__future__ __main__ _thread abc aifc argparse array ast
asynchat asyncio asyncore atexit audioop base64 bdb binascii bisect builtins bz2
cProfile calendar cgi cgitb chunk cmath cmd code codecs codeop collections colorsys
compileall concurrent configparser contextlib contextvars copy copyreg crypt csv
ctypes curses dataclasses datetime dbm decimal difflib dis distutils doctest email
encodings ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch fractions
ftplib functools gc genericpath getopt getpass gettext glob graphlib grp gzip
hashlib heapq hmac html http idlelib imaplib imghdr imp importlib inspect io ipaddress
itertools json keyword lib2to3 linecache locale logging lzma mailbox mailcap marshal
math mimetypes mmap modulefinder msilib msvcrt multiprocessing netrc nis nntplib
nt ntpath nturl2path numbers opcode operator optparse os ossaudiodev pathlib pdb
pickle pickletools pipes pkgutil platform plistlib poplib posix posixpath pprint
profile pstats pty pwd py_compile pyclbr pydoc pydoc_data queue quopri random re
readline reprlib resource rlcompleter runpy sched secrets select selectors shelve
shlex shutil signal site smtpd smtplib sndhdr socket socketserver spwd sqlite3 sre_compile
sre_constants sre_parse ssl stat statistics string stringprep struct subprocess sunau
symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile termios textwrap
threading time timeit tkinter token tokenize tomllib trace traceback tracemalloc tty
turtle turtledemo types typing unicodedata unittest urllib uu uuid venv warnings wave
weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc zipapp zipfile zipimport
zlib zoneinfo parser symbol _dummy_thread formatter macpath'''.split())
FALLBACK_STDLIB.update("""
__future__ _abc _aix_support _ast _asyncio _bisect _blake2 _bz2 _codecs _codecs_cn
_codecs_hk _codecs_iso2022 _codecs_jp _codecs_kr _codecs_tw _collections
_collections_abc _compat_pickle _compression _contextvars _crypt _csv _ctypes _curses
_curses_panel _datetime _dbm _decimal _elementtree _frozen_importlib
_frozen_importlib_external _functools _gdbm _hashlib _heapq _imp _io _json _locale
_lsprof _lzma _markupbase _md5 _msi _multibytecodec _multiprocessing _opcode _operator
_osx_support _overlapped _pickle _posixshmem _posixsubprocess _py_abc _pydatetime
_pydecimal _pyio _pylong _queue _random _scproxy _sha1 _sha2 _sha3 _signal _sitebuiltins
_socket _sqlite3 _sre _ssl _stat _statistics _string _strptime _struct _symtable _thread
_threading_local _tkinter _tokenize _tracemalloc _typing _uuid _warnings _weakref
_weakrefset _winapi _zoneinfo abc aifc antigravity argparse array ast asyncio atexit
audioop base64 bdb binascii bisect builtins bz2 cProfile calendar cgi cgitb chunk cmath
cmd code codecs codeop collections colorsys compileall concurrent configparser
contextlib contextvars copy copyreg crypt csv ctypes curses dataclasses datetime dbm
decimal difflib dis doctest email encodings ensurepip enum errno faulthandler fcntl
filecmp fileinput fnmatch fractions ftplib functools gc genericpath getopt getpass
gettext glob graphlib grp gzip hashlib heapq hmac html http idlelib imaplib imghdr
importlib inspect io ipaddress itertools json keyword lib2to3 linecache locale logging
lzma mailbox mailcap marshal math mimetypes mmap modulefinder msilib msvcrt
multiprocessing netrc nis nntplib nt ntpath nturl2path numbers opcode operator optparse
os ossaudiodev pathlib pdb pickle pickletools pipes pkgutil platform plistlib poplib
posix posixpath pprint profile pstats pty pwd py_compile pyclbr pydoc pydoc_data pyexpat
queue quopri random re readline reprlib resource rlcompleter runpy sched secrets select
selectors shelve shlex shutil signal site smtplib sndhdr socket socketserver spwd
sqlite3 sre_compile sre_constants sre_parse ssl stat statistics string stringprep struct
subprocess sunau symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile
termios textwrap this threading time timeit tkinter token tokenize tomllib trace
traceback tracemalloc tty turtle turtledemo types typing unicodedata unittest urllib uu
uuid venv warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc
zipapp zipfile zipimport zlib zoneinfo binhex
""".split())
SKIP = {".venv", "venv", "__pycache__", "dist", "build", ".git"}
RULES = {"zero-dep-claim-false", "imported-dep-undeclared", "declared-dep-unused",
         "test-dep-undeclared", "requires-python-mismatch", "requires-python-prose-mismatch"}


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    file: str
    line: int
    message: str

    def to_dict(self):
        return asdict(self)


def normalize(name):
    return re.sub(r"[-_.]+", "-", name.lower())


def distribution(module):
    return normalize(ALIASES.get(module.lower(), module))


def requirement_name(value):
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", value)
    if not match or value.lstrip().startswith(("-", "#")):
        return None
    return normalize(match.group(1))


def lower_bound(value):
    bounds = []
    for op, major, minor in re.findall(r"(>=|~=|==|>)\s*(\d+)\.(\d+)", value or ""):
        bounds.append((int(major), int(minor)))
    return max(bounds) if bounds else None


def is_test(path):
    return (any(p in {"tests", "test"} for p in path.parts)
            or path.name.startswith("test_") or path.name.endswith("_test.py"))


def union_type(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return union_type(node.left) and union_type(node.right)
    return isinstance(node, (ast.Name, ast.Subscript)) or (
        isinstance(node, ast.Constant) and node.value is None)


def features(tree):
    """Yield certain feature floors; ordinary bitwise expressions are excluded."""
    annotations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Match):
            yield (3, 10), node.lineno, "match statement"
        if isinstance(node, getattr(ast, "TryStar", ())):
            yield (3, 11), node.lineno, "except* statement"
        if isinstance(node, getattr(ast, "TypeAlias", ())):
            yield (3, 12), node.lineno, "type statement"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name.split('.')[0] for a in node.names] if isinstance(node, ast.Import) else [(node.module or "").split(".")[0]]
            if "tomllib" in names and not getattr(node, "level", 0):
                yield (3, 11), node.lineno, "tomllib import"
        if isinstance(node, ast.arg) and node.annotation:
            annotations.append(node.annotation)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            annotations.append(node.returns)
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
            if isinstance(node.annotation, ast.Name) and node.annotation.id == "TypeAlias" and node.value:
                annotations.append(node.value)
        if isinstance(node, ast.Assign) and union_type(node.value):
            annotations.append(node.value)
    seen = set()
    for annotation in annotations:
        for node in ast.walk(annotation):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and union_type(node):
                if node.lineno not in seen:
                    seen.add(node.lineno)
                    yield (3, 10), node.lineno, "union type annotation/alias"


def audit(path, ignore=()):
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError("project path must be a directory")
    manifest = root / "pyproject.toml"
    config = tomllib.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() and not manifest.is_symlink() else {}
    project = config.get("project", {})
    runtime, dev, locations = set(), set(), {}

    def add(values, target, filename):
        for value in values:
            name = requirement_name(value)
            if name:
                target.add(name)
                locations.setdefault(name, filename)

    add(project.get("dependencies", []), runtime, "pyproject.toml")
    for values in project.get("optional-dependencies", {}).values():
        add(values, dev, "pyproject.toml")
    for values in config.get("dependency-groups", {}).values():
        add([v for v in values if isinstance(v, str)], dev, "pyproject.toml")
    for req in sorted(root.glob("requirements*.txt")):
        if req.is_symlink():
            continue
        add(req.read_text(encoding="utf-8").splitlines(), runtime, req.name)
    setup = root / "setup.cfg"
    if setup.exists() and not setup.is_symlink():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(setup, encoding="utf-8")
        add(parser.get("options", "install_requires", fallback="").splitlines(), runtime, setup.name)
        if parser.has_section("options.extras_require"):
            for _, value in parser.items("options.extras_require"):
                add(value.splitlines(), dev, setup.name)
    allow = {normalize(n) for n in config.get("tool", {}).get("dep-claim-audit", {}).get("allow-unused", [])}
    docs = [(p.name, p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.md")) if p.is_file() and not p.is_symlink()]
    claims = []
    for filename, content in docs + [("pyproject.toml", project.get("description", ""))]:
        for number, line in enumerate(content.splitlines(), 1):
            if any(re.search(pattern, line, re.I) for pattern in CLAIM_PATTERNS):
                claims.append((filename, number))
    readme = next((content for name, content in docs if name.lower() == "readme.md"), "")
    prose = [(m, tuple(map(int, (m.group(1) or m.group(2)).split('.')))) for m in PROSE_VERSION.finditer(readme)]
    bound = lower_bound(project.get("requires-python", ""))
    effective = bound or (prose[0][1] if prose else None)
    local = set()
    for base in (root, root / "src"):
        if base.is_dir():
            local.update(p.name for p in base.iterdir() if p.is_dir())
            local.update(p.stem for p in base.glob("*.py"))
    stdlib = set(getattr(sys, "stdlib_module_names", FALLBACK_STDLIB)) | set(sys.builtin_module_names)
    imports, syntax = [], []
    for source in sorted(root.rglob("*.py")):
        rel = source.relative_to(root)
        if source.is_symlink():
            continue
        if any(p in SKIP or p.endswith(".egg-info") or p.startswith('.') for p in rel.parts):
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(rel))
        test = is_test(rel)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split('.')[0]]
            for name in names:
                if name not in local and name not in stdlib:
                    imports.append((distribution(name), name, rel.as_posix(), node.lineno, test))
        if not test and effective:
            for floor, line, feature in features(tree):
                if floor > effective:
                    syntax.append((rel.as_posix(), line, feature, floor))
    findings = []

    def emit(rule, severity, file, line, message):
        if rule not in ignore:
            findings.append(Finding(severity, rule, file, line, message))

    source_names = {d for d, _, _, _, test in imports if not test}
    all_names = {d for d, _, _, _, _ in imports}
    if project.get("dependencies") or source_names:
        for file, line in claims:
            emit("zero-dep-claim-false", "HIGH", file, line,
                 "Dependency-free claim conflicts with runtime dependencies or third-party source imports.")
    for dep, module, file, line, test in imports:
        if not test and dep not in runtime:
            emit("imported-dep-undeclared", "HIGH", file, line, f"Import {module!r} ({dep}) is not declared as a runtime dependency.")
        elif test and dep not in source_names and dep not in runtime | dev:
            emit("test-dep-undeclared", "MEDIUM", file, line, f"Test-only import {module!r} ({dep}) is not declared.")
    for dep in sorted(runtime - all_names - allow):
        emit("declared-dep-unused", "MEDIUM", locations[dep], 1,
             f"Declared dependency {dep!r} is never imported; plugins may need allow-unused.")
    for file, line, feature, floor in syntax:
        emit("requires-python-mismatch", "HIGH", file, line,
             f"{feature} requires Python {floor[0]}.{floor[1]}; claimed lower bound is {effective[0]}.{effective[1]}.")
    if bound:
        for match, version in prose:
            if version != bound:
                emit("requires-python-prose-mismatch", "MEDIUM", "README.md", readme[:match.start()].count('\n') + 1,
                     f"Prose lower bound {version[0]}.{version[1]} disagrees with pyproject {bound[0]}.{bound[1]}.")
    return sorted(set(findings), key=lambda f: (f.rule, f.file, f.line, f.message))
