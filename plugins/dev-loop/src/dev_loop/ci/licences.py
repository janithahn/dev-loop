"""`dl ci licences`: each direct dependency's licence against licences.allow; unknown is red.

Direct dependencies come from the project's own manifest, never the full install tree:
Python's `[project].dependencies` (pyproject.toml) or requirements.txt, and Node's
package.json `dependencies`. A Python dep's licence is read from the installed distribution's
metadata (License-Expression, then the OSI classifiers, then the free-text License field); a Node
dep's from node_modules/<pkg>/package.json. One line per dependency (name, licence, ok/red); a
licence that cannot be read is "unknown", which counts as red. With no manifest at all the step
is not run. It never passes silently: red on any denied or unknown licence.
"""

from __future__ import annotations

import importlib.metadata as im
import json
import re
import sys
import tomllib
from pathlib import Path

from dev_loop import config

# The OSI "License ::" classifiers for the shipped allow-list, mapped to their SPDX id.
CLASSIFIERS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # a PEP 508 / requirement name's leading run
SPLIT = re.compile(r"\s+(?:OR|AND|WITH)\s+|[()]", re.IGNORECASE)  # an SPDX expression's parts
UNKNOWN = "unknown"


def canonical(name: str) -> str:
    """The PEP 503 normalised distribution name, so a dep matches its installed metadata."""
    return re.sub(r"[-_.]+", "-", name).lower()


def python_deps(root: Path) -> list[str] | None:
    """The project's direct Python deps, or None when it has no Python manifest."""
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        reqs = data.get("project", {}).get("dependencies", [])
        return [m.group() for req in reqs if (m := NAME.match(req.strip()))]
    requirements = root / "requirements.txt"
    if not requirements.exists():
        return None
    lines = (line.split("#")[0].strip() for line in requirements.read_text("utf-8").splitlines())
    return [m.group() for line in lines if line and (m := NAME.match(line))]


def node_deps(root: Path) -> dict[str, str] | None:
    """The project's direct Node deps, or None when it has no package.json."""
    pkg = root / "package.json"
    if not pkg.exists():
        return None
    deps: dict[str, str] = json.loads(pkg.read_text(encoding="utf-8")).get("dependencies", {})
    return deps


def installed(site: list[str] | None) -> dict[str, im.PackageMetadata]:
    """Every installed distribution's metadata by canonical name; `site` None means this env."""
    dists = im.distributions(path=site) if site is not None else im.distributions()
    return {canonical(name): dist.metadata for dist in dists if (name := dist.metadata["Name"])}


def python_licence(meta: im.PackageMetadata) -> str:
    """A distribution's licence: the SPDX expression, then its classifiers, then the free text."""
    if (expr := (meta.get("License-Expression") or "").strip()):
        return expr
    spdx = sorted({CLASSIFIERS[c] for c in meta.get_all("Classifier") or [] if c in CLASSIFIERS})
    if spdx:
        return " OR ".join(spdx)
    text = (meta.get("License") or "").strip()
    return text.splitlines()[0].strip() if text else UNKNOWN


def node_licence(root: Path, name: str) -> str:
    """A Node dep's licence from node_modules/<name>/package.json; unknown when unreadable."""
    pkg = root / "node_modules" / name / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN
    licence = data.get("license")
    if isinstance(licence, str):
        return licence or UNKNOWN
    if isinstance(licence, dict):  # the old {"type": ...} shape
        return str(licence.get("type") or UNKNOWN)
    older = data.get("licenses")
    if isinstance(older, list) and older and isinstance(older[0], dict):
        return str(older[0].get("type") or UNKNOWN)
    return UNKNOWN


def allowed(licence: str, allow: set[str]) -> bool:
    """A licence is ok when every part of its SPDX expression is in the allow-list."""
    if licence == UNKNOWN:
        return False
    parts = [part.strip() for part in SPLIT.split(licence) if part.strip()]
    return bool(parts) and all(part in allow for part in parts)


def check(root: Path, site: list[str] | None = None) -> int:
    """Print one `name  licence  ok/red` line per direct dependency; 1 on any red or no manifest."""
    allow = set(config.load(root).get("licences.allow"))
    python, node = python_deps(root), node_deps(root)
    if python is None and node is None:
        print("not run: no dependency manifest")
        return 1
    rows: list[tuple[str, str]] = []
    if python is not None:
        meta = installed(site)
        rows += [(dep, python_licence(m) if (m := meta.get(canonical(dep))) else UNKNOWN)
                 for dep in python]
    rows += [(dep, node_licence(root, dep)) for dep in node or {}]
    red = 0
    for name, licence in rows:
        ok = allowed(licence, allow)
        red += not ok
        print(f"{name}  {licence}  {'ok' if ok else 'red'}")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(check(config.project_root()))
