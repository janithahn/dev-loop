"""The shell half of hooks/rules-guard.py: the files a Bash command writes, read from its words.

`written(command, cwd)` yields the path each simple command writes or removes: a redirect's
target (`>`, `>>`, `&>`; `2>&1` and `>/dev/null` name no file of ours), the files of tee, mv, rm
and truncate, the last argument of cp, dd's `of=`, the files of an in-place sed or perl, the
paths of git checkout, restore, rm and mv, and a literal path an interpreter's program opens for
writing, writes, renames or removes. A `cd` moves the folder the next relative path is read from, and the
text of `bash -c` is read as a command of its own.

ponytail: words only. A path built from a variable, a script file, `find -exec` or `xargs`
is not seen, and a quoted `>` reads as a redirect. A real shell parser if it matters.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator

OPERATORS = ";|&()<>\n"
WRAPPERS = {"sudo", "env", "command", "time", "nohup", "exec"}
BACKGROUND = {"nohup", "setsid", "disown"}  # a command that outlives its call
ASSIGNMENT = re.compile(r"\w+=.*", re.DOTALL)
SHELLS = {"bash", "sh", "zsh", "dash"}
INTERPRETERS = re.compile(r"python[\d.]*|node|ruby|perl|deno|bun")
EVERY = {"tee", "truncate", "mv", "rm", "unlink", "shred"}  # each plain argument is written
LAST = {"cp", "install", "ln"}  # the last one is
IN_PLACE = re.compile(r"-[a-zE0]*i|--in-place")  # -i, -pi, -Ei.bak; never perl's -Mstrict
GIT = {"checkout", "restore", "rm", "mv"}
QUOTED = r"""\(\s*['"]([^'"]+)['"]"""  # a call's literal first argument
OPENED = re.compile(  # the literal paths a program writes: open(p, "a"), Path(p).open("a"), ...
    rf"""open{QUOTED}\s*,\s*['"][wax]"""
    rf"""|Path{QUOTED}\)\.(?:open\(\s*['"][wax]|write_|unlink|rename|replace|touch)"""
    rf"""|(?:appendFile|writeFile|unlink|rm|truncate|rename|remove)\w*{QUOTED}"""
)


class Simple(NamedTuple):
    """One simple command: its name, arguments, redirect targets and working folder."""

    name: str
    args: list[str]
    targets: list[str]
    cwd: Path


def lex(command: str) -> shlex.shlex:
    """Return the lexer that reads a command into words and operators."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=OPERATORS)
    lexer.whitespace, lexer.whitespace_split = " \t\r", True
    return lexer


def backgrounds(command: str) -> bool:
    """True if a command has a lone `&` or starts nohup, setsid or disown (`a && b` is not).

    ponytail: a quoted lone `"&"` argument reads as the operator; a `bash -c` text is not read.
    """
    if "&" in list(lex(command)):
        return True
    skip = WRAPPERS - {"nohup"}
    for words, _ in split(command):
        while words and (words[0] in skip or ASSIGNMENT.fullmatch(words[0])):
            words = words[1:]
        if words and words[0] in BACKGROUND:
            return True
    return False


def split(command: str) -> Iterator[tuple[list[str], list[str]]]:
    """Yield each simple command's words and redirect targets."""
    lexer = lex(command)
    words: list[str] = []
    targets: list[str] = []
    redirect = False
    for token in lexer:
        if token and set(token) <= set(OPERATORS):
            redirect = ">" in token
            if set(token) & set(";|()\n") or token in {"&", "&&"}:
                yield words, targets
                words, targets = [], []
        elif redirect:
            targets.append(token)
            redirect = False
        else:
            words.append(token)
    yield words, targets


def commands(command: str, cwd: Path) -> Iterator[Simple]:
    """Yield each simple command, a `bash -c` text's own commands before the one that runs it."""
    for words, targets in split(command):
        while words and (words[0] in WRAPPERS or ASSIGNMENT.fullmatch(words[0])):
            words = words[1:]
        if not words:
            continue
        name, args = Path(words[0]).name, words[1:]
        if name == "cd" and args:
            cwd = cwd / Path(args[-1]).expanduser()
        for shell, flag, text in zip(words, words[1:], words[2:]):
            if Path(shell).name in SHELLS and re.fullmatch(r"-\w*c", flag):
                yield from commands(text, cwd)
        yield Simple(name, args, targets, cwd)


def changed(name: str, args: list[str]) -> list[str]:
    """Return the arguments the command writes, by its name."""
    plain = [arg for arg in args if not arg.startswith("-")]
    if name in EVERY:
        return plain
    if name in LAST:
        return plain[-1:]
    if name == "dd":
        return [arg[3:] for arg in args if arg.startswith("of=")]
    if name in {"sed", "perl", "ruby"} and any(IN_PLACE.match(arg) for arg in args):
        return plain
    if name == "git" and plain[:1] and plain[0] in GIT:
        return plain[1:]
    if INTERPRETERS.fullmatch(name):
        return [path for arg in args for found in OPENED.findall(arg) for path in found if path]
    return []


def written(command: str, cwd: Path) -> Iterator[Path]:
    """Yield each path the command writes, resolved from the folder its command runs in."""
    for simple in commands(command, cwd):
        for word in (*simple.targets, *changed(simple.name, simple.args)):
            yield simple.cwd / Path(word).expanduser()
