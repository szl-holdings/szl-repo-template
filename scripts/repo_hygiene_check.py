#!/usr/bin/env python3
"""repo_hygiene_check.py: fail-closed repository hygiene gate (SZL Holdings repo template).

Usage:
    python repo_hygiene_check.py [ROOT] [--json]

Exit codes:
    0  every guard ran and passed
    1  at least one guard failed: the repository is missing something
    2  internal error, bad usage, or not every guard could be evaluated
Nothing else returns 0. An exception, an unknown guard status or a missing guard is exit 2.

Guards (each returns PASS or FAIL):
    citation      CITATION.cff exists at the root, parses (YAML subset below), has cff-version 1.2.0,
                  a message, a title and at least one named author.
    placeholders  No template placeholder token is left in the root README, CITATION.cff or LICENSE files.
    limitations   The root README has a limitations heading (Limitations, Known issues, Caveats,
                  Non-goals, Threats to validity, What this is/does not) with a non-empty body.
    boundary      The root README has a heading containing "data boundary" whose body matches the
                  estate's class-matched DISCLAIM pattern (gh_audit.py v3), e.g. "not a clinical tool".
    lockfile      Every dependency manifest (pyproject.toml, requirements*.txt, package.json,
                  Cargo.toml, go.mod) has a lockfile for its ecosystem in its own or an ancestor
                  directory (go.sum: same directory), or pins every dependency exactly, or declares
                  no dependencies. Unparsable manifests fail.
    license       A LICENSE / LICENCE / COPYING file at the root that is non-empty and reads like a licence.

Scope and limits (by design):
    * Only the files named above are read. Code and data are never opened or executed.
    * The CITATION.cff parser supports block mappings and sequences, plain (also multi-line) and
      one-line quoted scalars, block scalars and one-line flow sequences. Anchors, aliases, tags,
      flow mappings and multi-line quoted scalars are reported as unparsable (fail-closed).
    * Directories named .git, node_modules, .venv, venv and __pycache__ are not searched for
      manifests. There is no exclusion file: exclusions would be a bypass.
    * peerDependencies (package.json) and [build-system] requires (pyproject.toml) are not
      treated as dependencies that need locking.
    * Headings are Markdown ATX/setext headings or reStructuredText underlined titles. Bold text
      and HTML headings do not count.

Standard library only. Requires Python 3.11+ (tomllib); without it a TOML manifest is exit 2.
"""
import json
import os
import re
import sys

VERSION = "1.0.0"
PASS, FAIL = "PASS", "FAIL"

# The token is assembled so this file never contains it literally.
PLACEHOLDER_TOKEN = "[[" + "FILL"

# Copied verbatim from gh_audit.py (SZL estate audit), so a boundary that passes here also
# counts as a README disclaimer there.
# Class-matched non-clinical statement (gh_audit.py v3, 2026-10-21). "metaphor" explains anatomical names and is
# deliberately absent: it never states a data boundary.
DISCLAIM = re.compile(r"non[- ]?clinical|not (a |for )?(clinical|medical)|no (phi|patient)|synthetic[- ]only|not a medical", re.I)
LIMIT_HEADING = re.compile(r"limitation|known issue|caveat|non-goal|threats to validity|what (this|it) (is|does) not", re.I)
BOUNDARY_HEADING = re.compile(r"\bdata boundary\b", re.I)

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
README_PREFERENCE = ("README.md", "README.markdown", "README.rst", "README.txt", "README")
REQUIREMENTS_NAME = re.compile(r"requirements[\w.-]*\.txt", re.I)
PY_LOCKS = {"uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock", "pylock.toml", "requirements.lock", "conda-lock.yml", "conda-lock.yaml"}
PY_LOCK_PATTERN = re.compile(r"pylock\.[\w.-]+\.toml")
JS_LOCKS = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock"}
EXACT_PEP440 = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]*\])?\s*===?\s*[^\s,;*<>=!~]+$")
EXACT_SEMVER = re.compile(r"^=?\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
HEX40 = re.compile(r"[0-9a-f]{40}")


class CheckerError(Exception):
    """The checker cannot evaluate a guard (environment or internal problem): exit 2."""


def read_text(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- root files

def root_files(root):
    return sorted(n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n)))


def find_readme(root):
    names = root_files(root)
    for want in README_PREFERENCE:
        if want in names:
            return want
    for want in README_PREFERENCE:
        for n in names:
            if n.lower() == want.lower():
                return n
    rest = [n for n in names if n.lower().startswith("readme")]
    return rest[0] if rest else None


def find_licenses(root):
    return [n for n in root_files(root) if n.lower().startswith(("license", "licence", "copying"))]


# ---------------------------------------------------------------- README sections

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+_:.])\1{2,}\s*$")


def strip_fences(lines):
    """Blank out fenced code blocks so '# comment' inside code is never read as a heading."""
    out, fence = [], None
    for line in lines:
        m = FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                out.append("")
                continue
            out.append(line)
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            out.append("")
    return out


def headings(text, rst=False):
    """Return [(line_index, level, title)] for ATX/setext headings (Markdown) or underlined titles (rST)."""
    lines = strip_fences(text.splitlines())
    found = []
    if rst:
        styles = []
        for i in range(1, len(lines)):
            title, under = lines[i - 1].rstrip(), lines[i].rstrip()
            m = RST_UNDERLINE.match(under)
            if m and title.strip() and not RST_UNDERLINE.match(title) and len(under.strip()) >= len(title.strip()):
                style = m.group(1)
                if style not in styles:
                    styles.append(style)
                found.append((i - 1, styles.index(style) + 1, title.strip()))
        return found
    for i, line in enumerate(lines):
        m = ATX.match(line)
        if m:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", m.group(2) or "").strip()
            found.append((i, len(m.group(1)), title))
            continue
        if i > 0 and SETEXT.match(line) and lines[i - 1].strip() and not ATX.match(lines[i - 1]) and not SETEXT.match(lines[i - 1]):
            found.append((i - 1, 1 if line.strip()[0] == "=" else 2, lines[i - 1].strip()))
    return found


def section_bodies(text, rst, pattern):
    """(title, body) for every section whose heading matches pattern; a body runs to the next heading of the same or higher level."""
    lines = strip_fences(text.splitlines())
    hs = headings(text, rst)
    bodies = []
    for k, (idx, level, title) in enumerate(hs):
        if not pattern.search(title):
            continue
        underlined = rst or (not ATX.match(lines[idx]) and idx + 1 < len(lines) and SETEXT.match(lines[idx + 1]))
        start = idx + (2 if underlined else 1)
        end = len(lines)
        for idx2, level2, _ in hs[k + 1:]:
            if level2 <= level:
                end = idx2
                break
        bodies.append((title, "\n".join(l for l in lines[start:end] if not (rst and RST_UNDERLINE.match(l)))))
    return bodies


def meaningful(body):
    """True if the body has a line that is not blank, a thematic break or a bare HTML comment."""
    for line in body.splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"([-*_=]\s*){3,}", s) or re.fullmatch(r"<!--.*-->", s):
            continue
        return True
    return False


def load_readme(root):
    name = find_readme(root)
    if name is None:
        return None, None, False
    return name, read_text(os.path.join(root, name)), name.lower().endswith(".rst")


# ---------------------------------------------------------------- minimal YAML for CITATION.cff

class CffParseError(Exception):
    pass


def _strip_comment(s):
    """Remove a trailing YAML comment, respecting quoted scalars."""
    out, q, i = [], None, 0
    while i < len(s):
        c = s[i]
        if q:
            out.append(c)
            if q == "'" and c == "'" and i + 1 < len(s) and s[i + 1] == "'":
                out.append("'"); i += 2; continue
            if q == '"' and c == "\\" and i + 1 < len(s):
                out.append(s[i + 1]); i += 2; continue
            if c == q:
                q = None
        else:
            before = "".join(out).rstrip()
            if c in "'\"" and (before == "" or before.endswith((":", "-", "[", ","))):
                q = c
            if c == "#" and (i == 0 or s[i - 1] in " \t"):
                break
            out.append(c)
        i += 1
    if q:
        raise CffParseError("unterminated quoted scalar")
    return "".join(out).rstrip()


def _scalar(tok):
    tok = tok.strip()
    if not tok:
        return ""
    if tok in ("~", "null", "Null", "NULL"):
        return None
    if tok[0] in "&*!{|>%@`":
        raise CffParseError(f"unsupported YAML construct: {tok[:20]!r}")
    if tok[0] == "'":
        if len(tok) < 2 or tok[-1] != "'":
            raise CffParseError("bad single-quoted scalar")
        return tok[1:-1].replace("''", "'")
    if tok[0] == '"':
        if len(tok) < 2 or tok[-1] != '"':
            raise CffParseError("bad double-quoted scalar")
        try:
            return json.loads(tok)
        except ValueError:
            raise CffParseError("bad double-quoted scalar")
    if tok[0] == "[":
        if tok[-1] != "]":
            raise CffParseError("multi-line or unterminated flow sequence")
        inner = tok[1:-1].strip()
        if not inner:
            return []
        parts, cur, q = [], "", None
        for c in inner:
            if q:
                cur += c
                if c == q:
                    q = None
            elif c in "'\"":
                q = c; cur += c
            elif c == ",":
                parts.append(cur); cur = ""
            elif c in "[]{}":
                raise CffParseError("nested flow collections are not supported")
            else:
                cur += c
        if q:
            raise CffParseError("unterminated quote in flow sequence")
        parts.append(cur)
        return [_scalar(p) for p in parts]
    if ": " in tok or tok.endswith(":"):
        raise CffParseError(f"mapping indicator inside a plain scalar: {tok[:30]!r}")
    return tok


_KEY = re.compile(r"^((?:\"(?:[^\"\\]|\\.)*\")|(?:'(?:[^']|'')*')|(?:[^\s'\"#:\-\[{][^:#]*?|-[^\s:#][^:#]*?))\s*:(?:\s+(.*))?$")


def parse_cff(text):
    """Parse the YAML subset used by CITATION.cff files. Raises CffParseError on anything else."""
    raw = text.split("\n")
    if raw and raw[0].startswith("\ufeff"):
        raw[0] = raw[0][1:]
    lines = []
    for n, line in enumerate(raw, 1):
        line = line.rstrip("\r")
        if line.strip() and "\t" in line[: len(line) - len(line.lstrip(" \t"))]:
            raise CffParseError(f"tab in indentation at line {n}")
        lines.append(line)
    content = [i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#")]
    if content and lines[content[0]].strip() == "---":
        lines[content[0]] = ""
    if content and lines[content[-1]].strip() == "...":
        lines[content[-1]] = ""
    if any(l.strip() in ("---", "...") for l in lines):
        raise CffParseError("multiple YAML documents are not supported")
    pos = [0]

    def indent_of(l):
        return len(l) - len(l.lstrip(" "))

    def next_content():
        while pos[0] < len(lines):
            l = lines[pos[0]]
            if l.strip() and not l.lstrip().startswith("#"):
                return l
            pos[0] += 1
        return None

    def block_scalar(header, parent_indent):
        m = re.fullmatch(r"([|>])([+-]?)(\d?)([+-]?)", header.strip())
        if not m:
            raise CffParseError(f"bad block scalar header {header!r}")
        style, chomp = m.group(1), (m.group(2) or m.group(4))
        collected, ind = [], None
        while pos[0] < len(lines):
            l = lines[pos[0]]
            if l.strip() == "":
                collected.append(""); pos[0] += 1; continue
            li = indent_of(l)
            if li <= parent_indent:
                break
            if ind is None:
                ind = li
            if li < ind:
                raise CffParseError("bad block scalar indentation")
            collected.append(l[ind:]); pos[0] += 1
        while collected and collected[-1] == "" and chomp != "+":
            collected.pop()
        if style == "|":
            val = "\n".join(collected)
        else:
            paras, cur = [], []
            for c in collected:
                if c == "":
                    paras.append(" ".join(cur)); cur = []
                else:
                    cur.append(c.strip())
            paras.append(" ".join(cur))
            val = "\n".join(paras)
        return val if chomp == "-" else val + "\n"

    def parse_value(rest, own_indent):
        """Value after 'key:' or '- ' on the same line; empty means a nested block follows."""
        rest = _strip_comment(rest).strip() if rest else ""
        if rest.startswith(("|", ">")):
            return block_scalar(rest, own_indent)
        if rest:
            if rest[0] in "'\"[":
                val = _scalar(rest)
                nxt = next_content()
                if nxt is not None and indent_of(nxt) > own_indent:
                    raise CffParseError("unexpected indented line after a quoted or flow scalar")
                return val
            parts = [rest]
            while True:  # plain scalars may continue on more-indented lines
                nxt = next_content()
                if nxt is None or indent_of(nxt) <= own_indent:
                    break
                body = _strip_comment(nxt.strip())
                if body.startswith("- ") or body == "-" or _KEY.match(body):
                    raise CffParseError(f"bad indentation at line {pos[0] + 1}")
                parts.append(body); pos[0] += 1
            return _scalar(" ".join(parts))
        nxt = next_content()
        if nxt is None or indent_of(nxt) < own_indent:
            return None
        if indent_of(nxt) == own_indent:
            if nxt.lstrip().startswith("- ") or nxt.strip() == "-":
                return parse_block(own_indent)  # sequence written at the same indent as its key
            return None
        return parse_block(indent_of(nxt))

    def parse_block(ind):
        first = next_content()
        if first is None:
            return None
        if indent_of(first) != ind:
            raise CffParseError("inconsistent indentation")
        if first.lstrip().startswith("- ") or first.strip() == "-":
            seq = []
            while True:
                l = next_content()
                if l is None or indent_of(l) < ind:
                    break
                if indent_of(l) > ind:
                    raise CffParseError(f"inconsistent indentation in sequence at line {pos[0] + 1}")
                body = l.lstrip()
                if not (body.startswith("- ") or body == "-"):
                    break
                pos[0] += 1
                after = body[1:]
                item = after.lstrip(" ")
                item_indent = ind + 1 + (len(after) - len(item))
                if item.strip() and _KEY.match(_strip_comment(item).strip()):
                    lines.insert(pos[0], " " * item_indent + item)  # mapping that starts on the dash line
                    seq.append(parse_block(item_indent))
                else:
                    seq.append(parse_value(item, ind))
            return seq
        mapping = {}
        while True:
            l = next_content()
            if l is None or indent_of(l) < ind:
                break
            if indent_of(l) > ind:
                raise CffParseError(f"inconsistent indentation in mapping at line {pos[0] + 1}")
            body = _strip_comment(l.strip())
            if body.startswith("- ") or body == "-":
                break
            m = _KEY.match(body)
            if not m:
                raise CffParseError(f"cannot parse line {pos[0] + 1}: {l.strip()[:40]!r}")
            key = _scalar(m.group(1))
            if not isinstance(key, str):
                raise CffParseError("non-scalar key")
            if key in mapping:
                raise CffParseError(f"duplicate key {key!r}")
            pos[0] += 1
            mapping[key] = parse_value(m.group(2) or "", ind)
        return mapping

    first = next_content()
    if first is None:
        raise CffParseError("empty document")
    doc = parse_block(indent_of(first))
    if next_content() is not None:
        raise CffParseError(f"unparsed content at line {pos[0] + 1}")
    return doc


# ---------------------------------------------------------------- guards

def guard_citation(root):
    path = os.path.join(root, "CITATION.cff")
    if "CITATION.cff" not in root_files(root):  # listing, not isfile: the name is case-sensitive on every OS
        others = [n for n in root_files(root) if n.lower() == "citation.cff"]
        return FAIL, "CITATION.cff missing at the root" + (f" (found {others[0]!r}; the name is case-sensitive)" if others else "")
    try:
        doc = parse_cff(read_text(path))
    except CffParseError as e:
        return FAIL, f"CITATION.cff is unparsable: {e}"
    if not isinstance(doc, dict):
        return FAIL, "CITATION.cff top level is not a mapping"
    problems = []
    if str(doc.get("cff-version", "")).strip() != "1.2.0":
        problems.append(f"cff-version is {doc.get('cff-version')!r}, expected '1.2.0'")
    for k in ("message", "title"):
        if not isinstance(doc.get(k), str) or not doc.get(k).strip():
            problems.append(f"{k} missing or empty")
    authors = doc.get("authors")
    if not isinstance(authors, list) or not authors:
        problems.append("authors missing or empty")
    else:
        for i, a in enumerate(authors):
            if not isinstance(a, dict) or not any(isinstance(a.get(k), str) and a.get(k).strip() for k in ("family-names", "given-names", "name", "alias")):
                problems.append(f"author {i + 1} has no name")
    if problems:
        return FAIL, "CITATION.cff: " + "; ".join(problems)
    return PASS, "CITATION.cff parses; cff-version 1.2.0; message, title and authors present"


def guard_placeholders(root):
    files = []
    readme = find_readme(root)
    if readme:
        files.append(readme)
    if "CITATION.cff" in root_files(root):
        files.append("CITATION.cff")
    files += [n for n in find_licenses(root) if n not in files]
    hits = []
    for n in files:
        for i, line in enumerate(read_text(os.path.join(root, n)).splitlines(), 1):
            if PLACEHOLDER_TOKEN in line:
                hits.append(f"{n}:{i}")
    if hits:
        return FAIL, f"{len(hits)} template placeholder(s) left: " + ", ".join(hits[:12]) + (" ..." if len(hits) > 12 else "")
    return PASS, "no template placeholders in " + (", ".join(files) if files else "(no README, CITATION.cff or LICENSE to scan)")


def guard_limitations(root):
    name, text, rst = load_readme(root)
    if name is None:
        return FAIL, "no README at the root"
    bodies = section_bodies(text, rst, LIMIT_HEADING)
    if not bodies:
        return FAIL, f"{name} has no Limitations heading"
    for title, body in bodies:
        if meaningful(body):
            return PASS, f"{name}: heading {title!r} with a non-empty body"
    return FAIL, f"{name}: the Limitations section is empty"


def guard_boundary(root):
    name, text, rst = load_readme(root)
    if name is None:
        return FAIL, "no README at the root"
    bodies = section_bodies(text, rst, BOUNDARY_HEADING)
    if not bodies:
        return FAIL, f"{name} has no 'Scope and data boundary' heading"
    for title, body in bodies:
        m = DISCLAIM.search(" ".join(body.split()))
        if m:
            return PASS, f"{name}: {title!r} states {m.group(0)!r}"
    return FAIL, f"{name}: the data-boundary section has no non-clinical statement (DISCLAIM pattern)"


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _lock_in(root, directory, names, pattern=None, same_dir_only=False):
    """Relative path of the first matching lockfile in directory or an ancestor up to root, else None."""
    top = os.path.normcase(os.path.abspath(root))
    d = os.path.abspath(directory)
    while True:
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            entries = []
        for e in entries:
            if (e in names or (pattern is not None and pattern.fullmatch(e))) and os.path.isfile(os.path.join(d, e)):
                return os.path.relpath(os.path.join(d, e), os.path.abspath(root)).replace(os.sep, "/")
        if same_dir_only or os.path.normcase(d) == top or not os.path.normcase(d).startswith(top):
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _req_entries(path, seen=None):
    """(pinned_names, unpinned_lines, problems) for a requirements file, following -r includes."""
    seen = set() if seen is None else seen
    real = os.path.normcase(os.path.abspath(path))
    if real in seen:
        return set(), [], []
    seen.add(real)
    if not os.path.isfile(path):
        return set(), [], [f"included file {os.path.basename(path)!r} not found"]
    joined = re.sub(r"\\\r?\n", " ", read_text(path))
    pinned, unpinned, problems = set(), [], []
    for raw in joined.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line.startswith(("-r", "--requirement")):
            target = re.sub(r"^(-r|--requirement)(=|\s+)?", "", line).strip()
            p2, u2, pr2 = _req_entries(os.path.join(os.path.dirname(path), target), seen)
            pinned |= p2; unpinned += u2; problems += pr2
            continue
        if line.startswith(("-e", "--editable")):
            target = re.sub(r"^(-e|--editable)(=|\s+)?", "", line).strip()
            if target.startswith((".", "/", "file:")) or HEX40.search(target):
                continue
            unpinned.append(line)
            continue
        if line.startswith("-"):
            continue  # index, constraint, hash-mode and build options declare no requirement
        spec = re.sub(r"\s--hash[=\s]\S+", "", line).split(";", 1)[0].strip()
        if " @ " in spec:
            name, url = spec.split(" @ ", 1)
            if "#sha256=" in url or HEX40.search(url) or url.strip().startswith("file:"):
                pinned.add(_norm(re.sub(r"\[.*\]", "", name).strip()))
                continue
            unpinned.append(line)
            continue
        if spec.startswith((".", "/", "file:")):
            continue  # local path requirement
        if EXACT_PEP440.match(spec):
            pinned.add(_norm(re.split(r"[\[=\s]", spec, maxsplit=1)[0]))
            continue
        unpinned.append(line)
    return pinned, unpinned, problems


def _pep508_exact(s):
    return bool(EXACT_PEP440.match(s.split(";", 1)[0].strip()))


def _pep508_name(s):
    return _norm(re.split(r"[\s\[<>=!~;@(]", s.strip(), maxsplit=1)[0])


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _check_pyproject(root, path, tomllib):
    rel = _rel(root, path)
    try:
        data = tomllib.loads(read_text(path))
    except Exception as e:
        return FAIL, f"{rel}: unparsable ({type(e).__name__})"
    proj = data.get("project") or {}
    deps, exact = [], []
    dynamic = set(proj.get("dynamic") or [])
    groups = [proj.get("dependencies") or []] + list((proj.get("optional-dependencies") or {}).values())
    groups += [[s for s in (g or []) if isinstance(s, str)] for g in (data.get("dependency-groups") or {}).values()]
    for group in groups:
        for s in group or []:
            deps.append(_pep508_name(s)); exact.append(_pep508_exact(s))
    poetry = (data.get("tool") or {}).get("poetry") or {}
    tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    tables += [(g or {}).get("dependencies") or {} for g in (poetry.get("group") or {}).values()]
    for table in tables:
        for name, spec in table.items():
            if name.lower() == "python":
                continue
            deps.append(_norm(name))
            if isinstance(spec, str):
                exact.append(bool(re.fullmatch(r"=?=?\d+(\.\d+)*([a-z]+\d*)?", spec.strip())))
            elif isinstance(spec, dict):
                exact.append(bool("path" in spec or HEX40.fullmatch(str(spec.get("rev", ""))) or re.fullmatch(r"=?=?\d+(\.\d+)*", str(spec.get("version", "")).strip())))
            else:
                exact.append(False)
    unknown = bool({"dependencies", "optional-dependencies"} & dynamic)
    if not deps and not unknown:
        return PASS, f"{rel}: no dependencies declared"
    lock = _lock_in(root, os.path.dirname(path), PY_LOCKS, PY_LOCK_PATTERN)
    if lock:
        return PASS, f"{rel}: locked by {lock}"
    if deps and all(exact) and not unknown:
        return PASS, f"{rel}: all {len(deps)} dependencies pinned exactly"
    directory = os.path.dirname(path)
    for n in sorted(os.listdir(directory)):
        if REQUIREMENTS_NAME.fullmatch(n) and os.path.isfile(os.path.join(directory, n)):
            pinned, unpinned, problems = _req_entries(os.path.join(directory, n))
            if not unpinned and not problems and pinned and set(deps) <= pinned:
                return PASS, f"{rel}: every dependency pinned in {n}"
    what = "dynamic dependencies" if unknown else f"{sum(1 for e in exact if not e)} of {len(deps)} dependencies unpinned"
    return FAIL, f"{rel}: {what}, no lockfile (uv.lock, poetry.lock, pdm.lock, pylock.toml, ...) and no fully pinned requirements file"


def _check_requirements(root, path):
    rel = _rel(root, path)
    pinned, unpinned, problems = _req_entries(path)
    if problems:
        return FAIL, f"{rel}: " + "; ".join(problems)
    if not unpinned:
        return PASS, f"{rel}: " + (f"all {len(pinned)} requirements pinned exactly" if pinned else "no requirements declared")
    lock = _lock_in(root, os.path.dirname(path), PY_LOCKS, PY_LOCK_PATTERN)
    if lock:
        return PASS, f"{rel}: locked by {lock}"
    return FAIL, f"{rel}: {len(unpinned)} requirement(s) not pinned with == (first: {unpinned[0][:60]!r}) and no lockfile"


def _check_package_json(root, path):
    rel = _rel(root, path)
    try:
        data = json.loads(read_text(path))
    except ValueError:
        return FAIL, f"{rel}: unparsable JSON"
    if not isinstance(data, dict):
        return FAIL, f"{rel}: top level is not an object"
    specs = []
    for k in ("dependencies", "devDependencies", "optionalDependencies"):
        table = data.get(k) or {}
        if not isinstance(table, dict):
            return FAIL, f"{rel}: {k} is not an object"
        specs += [str(v) for v in table.values()]
    if not specs:
        return PASS, f"{rel}: no dependencies declared"
    lock = _lock_in(root, os.path.dirname(path), JS_LOCKS)
    if lock:
        return PASS, f"{rel}: locked by {lock}"

    def exact(v):
        v = v.strip()
        return bool(EXACT_SEMVER.match(v) or v.startswith(("file:", "link:", "workspace:")) or ("#" in v and HEX40.fullmatch(v.rsplit("#", 1)[1])))
    loose = [v for v in specs if not exact(v)]
    if not loose:
        return PASS, f"{rel}: all {len(specs)} dependencies pinned exactly"
    return FAIL, f"{rel}: {len(loose)} of {len(specs)} dependencies use ranges and there is no lockfile (package-lock.json, pnpm-lock.yaml, yarn.lock, ...)"


def _check_cargo(root, path, tomllib):
    rel = _rel(root, path)
    try:
        data = tomllib.loads(read_text(path))
    except Exception as e:
        return FAIL, f"{rel}: unparsable ({type(e).__name__})"
    tables = [data.get(k) or {} for k in ("dependencies", "dev-dependencies", "build-dependencies")]
    tables.append((data.get("workspace") or {}).get("dependencies") or {})
    for t in (data.get("target") or {}).values():
        if isinstance(t, dict):
            tables += [t.get(k) or {} for k in ("dependencies", "dev-dependencies", "build-dependencies")]
    specs = [v for t in tables if isinstance(t, dict) for v in t.values()]
    if not specs:
        return PASS, f"{rel}: no dependencies declared"
    lock = _lock_in(root, os.path.dirname(path), {"Cargo.lock"})
    if lock:
        return PASS, f"{rel}: locked by {lock}"

    def exact(v):
        if isinstance(v, str):
            return v.strip().startswith("=") and bool(EXACT_SEMVER.match(v.strip()))
        if isinstance(v, dict):
            ver = str(v.get("version", ""))
            return "path" in v or bool(HEX40.fullmatch(str(v.get("rev", "")))) or (ver.startswith("=") and bool(EXACT_SEMVER.match(ver)))
        return False
    if all(exact(v) for v in specs):
        return PASS, f"{rel}: all {len(specs)} dependencies pinned exactly"
    return FAIL, f"{rel}: dependencies without Cargo.lock"


def _check_gomod(root, path):
    rel = _rel(root, path)
    text = re.sub(r"//.*", "", read_text(path))
    if not re.search(r"^\s*require\b", text, re.M):
        return PASS, f"{rel}: no require directives"
    lock = _lock_in(root, os.path.dirname(path), {"go.sum"}, same_dir_only=True)
    if lock:
        return PASS, f"{rel}: locked by {lock}"
    return FAIL, f"{rel}: require directives without go.sum"


def find_manifests(root):
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            if f in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod") or REQUIREMENTS_NAME.fullmatch(f):
                found.append(os.path.join(d, f))
    return found


def guard_lockfile(root):
    manifests = find_manifests(root)
    if not manifests:
        return PASS, "no dependency manifests (pyproject.toml, requirements*.txt, package.json, Cargo.toml, go.mod)"
    tomllib = None
    if any(os.path.basename(p) in ("pyproject.toml", "Cargo.toml") for p in manifests):
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            raise CheckerError("tomllib unavailable: Python 3.11+ is needed to evaluate TOML manifests")
    results = []
    for p in manifests:
        base = os.path.basename(p)
        if base == "pyproject.toml":
            results.append(_check_pyproject(root, p, tomllib))
        elif base == "package.json":
            results.append(_check_package_json(root, p))
        elif base == "Cargo.toml":
            results.append(_check_cargo(root, p, tomllib))
        elif base == "go.mod":
            results.append(_check_gomod(root, p))
        else:
            results.append(_check_requirements(root, p))
    failed = [d for s, d in results if s != PASS]
    if failed:
        return FAIL, f"{len(failed)} of {len(results)} manifest(s) unlocked: " + " | ".join(failed[:6]) + (" ..." if len(failed) > 6 else "")
    return PASS, f"{len(results)} manifest(s) locked, pinned or dependency-free"


def guard_license(root):
    names = find_licenses(root)
    if not names:
        return FAIL, "no LICENSE, LICENCE or COPYING file at the root"
    for n in names:
        text = read_text(os.path.join(root, n))
        if text.strip() and re.search(r"licen[cs]e|permission|copyright", text, re.I):
            return PASS, f"{n} present"
    return FAIL, f"{names[0]} is empty or does not read like a licence"


GUARDS = [
    ("citation", guard_citation),
    ("placeholders", guard_placeholders),
    ("limitations", guard_limitations),
    ("boundary", guard_boundary),
    ("lockfile", guard_lockfile),
    ("license", guard_license),
]
EXPECTED_GUARDS = frozenset({"citation", "placeholders", "limitations", "boundary", "lockfile", "license"})


def evaluate(root):
    """Run every guard. Returns (exit_code, results). Exit 0 only if every expected guard returned PASS."""
    results = {}
    for name, fn in GUARDS:
        try:
            status, detail = fn(root)
        except Exception as e:
            status, detail = "ERROR", f"{type(e).__name__}: {e}"
        if status not in (PASS, FAIL):
            detail = f"invalid guard status {status!r}: {detail}"
            status = "ERROR"
        results[name] = {"status": status, "detail": detail}
    if set(results) != EXPECTED_GUARDS or len(GUARDS) != len(EXPECTED_GUARDS):
        missing = sorted(EXPECTED_GUARDS - set(results))
        results["_completeness"] = {"status": "ERROR", "detail": f"guard set incomplete; missing {missing}"}
        return 2, results
    statuses = [r["status"] for r in results.values()]
    if "ERROR" in statuses:
        return 2, results
    if FAIL in statuses:
        return 1, results
    if statuses and all(s == PASS for s in statuses):
        return 0, results
    return 2, results


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if flags - {"--json"} or len(args) > 1:
        print("usage: repo_hygiene_check.py [ROOT] [--json]", file=sys.stderr)
        return 2
    root = args[0] if args else "."
    if not os.path.isdir(root):
        print(f"repo_hygiene_check: {root!r} is not a directory", file=sys.stderr)
        return 2
    code, results = evaluate(root)
    if "--json" in flags:
        print(json.dumps({"checker": "repo_hygiene_check", "version": VERSION, "root": os.path.basename(os.path.abspath(root)),
                          "exit_code": code, "guards": results}, indent=1, sort_keys=True))
    else:
        for name, r in results.items():
            print(f"{r['status']:5} {name}: {r['detail']}")
        print(f"RESULT {'PASS' if code == 0 else 'FAIL' if code == 1 else 'ERROR'} (exit {code})")
    return code


def _entry():
    try:
        code = main(sys.argv[1:])
    except BaseException as e:  # fail closed: any unexpected exception is an internal error
        try:
            print(f"repo_hygiene_check: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        except BaseException:
            pass
        return 2
    return code if code in (0, 1, 2) else 2


if __name__ == "__main__":
    sys.exit(_entry())
