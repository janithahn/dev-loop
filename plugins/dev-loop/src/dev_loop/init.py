"""`dl init`: the project's files. Safe to run again: a second run changes nothing.

Writes dev-loop.yaml (provider, org, project, repo from an Azure DevOps or GitHub `origin`,
`--host ado|github` forcing the provider; the integration branch from
`origin/HEAD`), merges the plugin keys into .claude/settings.json, writes .githooks and enables
them with core.hooksPath (but only from the main worktree: that config is shared with every
other worktree, so from a linked worktree init prints the one command instead of writing it),
writes the plugin-owned .dev-loop/RULES.md (refreshed when the plugin's copy
changes) and points at it from AGENTS.md (canonical for every harness: the project law and a
plain pointer line) and CLAUDE.md (`@AGENTS.md` plus the import); each is created from its
template when missing, else given only the line it lacks. Every written file has its
`{{placeholders}}` filled, with the project named from the repository (the origin's repo, else
the main worktree's folder) and a neutral default where no answer is known. Copies the host's
CI template (Azure steps, or the GitHub workflow to .github/workflows) and the knowledge
templates that are missing. Never overwrites a file it did not write, never touches an existing
settings key but its own two.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config
from .config import PROJECT, project_root

PLUGIN = Path(__file__).resolve().parents[2]
TEMPLATES = PLUGIN / "templates"
HOOKS = ("pre-commit", "pre-push")
RULES = ".dev-loop/RULES.md"
IMPORT = f"@{RULES}"
POINTER = f"Follow the working rules in [{RULES}]({RULES})."  # harnesses without @
WORKFLOW = ".github/workflows/dev-loop.yml"
# The githook body: `dl` from PATH, else the newest copy in the plugin cache (outside Claude Code
# `dl` is not on PATH); with neither, say loudly that the checks did not run and let git go on.
GITHOOK = """#!/usr/bin/env sh
# Written by `dl init`. Test: tests/test_init_githook.py
dl="$(command -v dl)"
if [ -z "$dl" ]; then
  dl="$(for d in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/dev-loop/dev-loop/*/; do
    [ -e "${d}.orphaned_at" ] || [ ! -f "${d}bin/dl" ] || echo "${d}bin/dl"
  done | sort -V | tail -n 1)"
fi
if [ -z "$dl" ]; then
  echo "dev-loop: dl not found, {name} checks NOT run (install the plugin or put its bin on PATH)" >&2
  exit 0
fi
exec "$dl" githook {name}
"""
ADO = re.compile(
    r"(?:dev\.azure\.com/|ssh\.dev\.azure\.com:v3/)(?P<org>[^/]+)/(?P<project>[^/]+)"
    r"(?:/_git)?/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
GITHUB = re.compile(r"(?:^|[/@])github\.com[/:](?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def origin(root: Path, host: str | None = None, remote: str = "origin") -> dict[str, str]:
    """Return provider, org, (ADO) project and repo from the remote's URL; host forces provider."""
    url = git(root, "remote", "get-url", remote)
    for provider, pattern in (("ado", ADO), ("github", GITHUB)):
        found = pattern.search(url) if host in (None, provider) else None
        if found:
            parts = {key: unquote(value) for key, value in found.groupdict().items()}
            return {"provider": provider, **parts}
    return {"provider": host} if host else {}


def integration(root: Path, remote: str = "origin") -> str:
    """Return the branch the remote's HEAD points at, else main."""
    head = git(root, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    return head.removeprefix(f"{remote}/") or "main"


# Neutral defaults for the template placeholders a fresh project cannot answer yet; the author
# fills them in. `{{project}}` is the one value we do know, from the repository.
DEFAULTS = {
    "one_line_purpose": "a short description of what it does",
    "design_paths": "the paths to your design documents",
    "law_1": "a rule no feature may break",
    "law_2": "another rule no feature may break",
    "law_3": "a third rule no feature may break",
    "stack_summary": "the languages, frameworks and services it runs on",
    "date": "on adoption",
    "owner": "the maintainers",
}


def fill(text: str, project: str) -> str:
    """Replace every {{placeholder}} with the project name or its neutral default."""
    text = text.replace("{{project}}", project)
    for key, value in DEFAULTS.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def project_name(root: Path) -> str:
    """The repository's name: the origin's repo, else the main worktree's top-level folder."""
    repo = origin(root).get("repo")
    if repo:
        return repo
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent.name if common else root.name


def linked_worktree(root: Path) -> bool:
    """Whether root is a linked worktree, whose .git/config is shared with the main worktree."""
    git_dir = git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return bool(common) and git_dir != common


def write_new(path: Path, text: str, mode: int | None = None) -> bool:
    """Write text unless the file already holds it; return whether anything changed."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    if mode:
        path.chmod(mode)
    return True


def manifest() -> dict[str, str]:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text("utf-8"))


def version() -> str:
    return str(manifest()["version"])


def config_text(root: Path, host: str | None) -> str:
    text = (TEMPLATES / PROJECT).read_text(encoding="utf-8")
    remotes = git(root, "remote").split()
    remote = remotes[0] if "origin" not in remotes and len(remotes) == 1 else "origin"
    for key, value in {**origin(root, host, remote), "remote": remote}.items():
        text = re.sub(rf"^(  {key}:) \S+", rf"\1 {value}", text, flags=re.MULTILINE)
    return re.sub(r"^(  integration:) \S+", rf"\1 {integration(root, remote)}", text,
                  flags=re.MULTILINE)


def merge_settings(root: Path) -> bool:
    """Set the marketplace (pinned, no auto-update) and the enabled plugin; keep all else."""
    path = root / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    market = settings.setdefault("extraKnownMarketplaces", {})
    ref = f"v{version()}"
    repo = urlparse(manifest()["repository"]).path.strip("/")
    entry = market.get("dev-loop")
    if entry is None:
        source = {"source": "github", "repo": repo, "ref": ref}
        market["dev-loop"] = {"source": source, "autoUpdate": False}
    elif entry.get("source", {}).get("repo") == repo:
        entry["source"]["ref"] = ref
    else:
        sys.stderr.write("dl init: marketplace 'dev-loop' left as the project set it\n")
    settings.setdefault("enabledPlugins", {})["dev-loop@dev-loop"] = True
    return write_new(path, json.dumps(settings, indent=2) + "\n")


def copy_missing(
    source: Path, target: Path, transform: Callable[[str], str] | None = None
) -> list[str]:
    """Copy each file under source to the same place under target unless it exists."""
    made = []
    for file in sorted(p for p in source.rglob("*") if p.is_file() and p.name != ".gitkeep"):
        dest = target / file.relative_to(source)
        if not dest.exists():
            text = file.read_text(encoding="utf-8")
            write_new(dest, transform(text) if transform else text)
            made.append(dest.relative_to(target).as_posix())
    return made


def copy_ci(root: Path, host: str | None) -> list[str]:
    """Copy the provider's CI template when missing: the GitHub workflow, else the Azure steps."""
    loaded = config.load(root)
    provider = host or (None if loaded.error else loaded.get("host.provider"))
    if provider == "local":
        return []  # the local host has no hosted CI: the gates run with `dl land`
    if provider != "github":
        made = copy_missing(TEMPLATES / "ci" / "azure", root / "azure")
        return [f"azure/{name}" for name in made]
    if (root / WORKFLOW).exists():
        return []
    text = (TEMPLATES / "ci" / "github" / "dev-loop.yml").read_text(encoding="utf-8")
    write_new(root / WORKFLOW, text.replace("{{tag}}", f"v{version()}"))
    return [WORKFLOW]


def add_line(path: Path, line: str, accepted: str) -> bool:
    """Append line unless the file holds it or accepted; never change other bytes."""
    text = path.read_bytes()
    if {line.encode(), accepted.encode()} & {row.strip() for row in text.splitlines()}:
        return False
    with path.open("ab") as file:
        file.write(b"\n" if text.endswith(b"\n") else b"\n\n")
        file.write(line.encode() + b"\n")
    return True


def import_rules(root: Path, project: str) -> list[str]:
    """Create AGENTS.md and CLAUDE.md from their templates, or add each the line it lacks."""
    changed = []
    for name, line, accepted in (("AGENTS.md", POINTER, IMPORT), ("CLAUDE.md", IMPORT, IMPORT)):
        path = root / name
        if path.exists():
            made = add_line(path, line, accepted)
        else:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            made = write_new(path, fill(text, project))
        if made:
            changed.append(name)
    return changed


def enable_hooks(root: Path) -> str | None:
    """Point git at .githooks via core.hooksPath; return it if changed, warn if we must not."""
    current = git(root, "config", "core.hooksPath")
    if current == ".githooks":
        return None
    if current:
        sys.stderr.write(
            f"dl init: core.hooksPath is {current}, left as set; "
            "call `dl githook <name>` from your own hooks to run the checks\n"
        )
        return None
    if linked_worktree(root):
        # core.hooksPath lives in .git/config, shared by every worktree; never write it silently.
        sys.stderr.write(
            "dl init: run `git config core.hooksPath .githooks` in the main worktree to enable "
            "the hooks (it is not set here: this is a linked worktree sharing the main config)\n"
        )
        return None
    git(root, "config", "core.hooksPath", ".githooks")
    return "core.hooksPath"


def init(root: Path, host: str | None = None) -> list[str]:
    """Do every step; return what changed."""
    project = project_name(root)
    changed = []
    if not (root / PROJECT).exists():
        write_new(root / PROJECT, config_text(root, host))
        changed.append(PROJECT)
    if merge_settings(root):
        changed.append(".claude/settings.json")
    for name in HOOKS:
        hook = root / ".githooks" / name
        if hook.exists() and "Written by `dl init`" not in hook.read_text(encoding="utf-8"):
            sys.stderr.write(f"dl init: .githooks/{name} is the project's own, left as is\n")
        elif write_new(hook, GITHOOK.replace("{name}", name), 0o755):
            changed.append(f".githooks/{name}")
    hooks = enable_hooks(root)
    if hooks:
        changed.append(hooks)
    if write_new(root / RULES, (TEMPLATES / "RULES.md").read_text(encoding="utf-8")):
        changed.append(RULES)
    changed += import_rules(root, project)
    changed += copy_ci(root, host)
    changed += copy_missing(TEMPLATES / "knowledge", root, lambda text: fill(text, project))
    return changed


def main(argv: list[str]) -> int:
    if argv[:1] == ["discover"]:  # the knowledge keys, after the files
        from . import init_discover

        return init_discover.main(argv[1:])
    parser = argparse.ArgumentParser(prog="dl init")
    parser.add_argument("--host", choices=("ado", "github", "local"),
                        help="force the host provider")
    host = parser.parse_args(argv).host
    try:
        changed = init(project_root(), host)
    except (OSError, ValueError) as error:
        print(f"dl init: {error}")
        return 1
    print("\n".join(f"wrote {item}" for item in changed) or "nothing to change")
    return 0
