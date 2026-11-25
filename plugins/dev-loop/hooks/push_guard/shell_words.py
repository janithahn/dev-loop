"""The push guard's reading of a command line: its simple commands, each as tokens.

Split on the shell operators outside quotes, with `sudo` / `env` / `export` wrappers dropped and
`bash -c '..'` opened, so a rule judges every command that would really run.
"""

from __future__ import annotations

import re
import shlex
import sys

ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")  # a leading VAR=value prefix
SPLIT = re.compile(r"&&|\|\||[;|&`\n]")  # the shell operators that end one simple command
WRAP = {"sudo", "command", "env", "export", "exec", "time", "nohup"}  # they run what follows
SHELLS = {"bash", "sh", "zsh", "dash"}
LOGGED: list[str] = []  # the one stderr line for unparsable segments


def tool(token: str) -> str:
    """The program a token names: no directory, no .exe / .cmd, lower case."""
    return re.split(r"[\\/]", token)[-1].lower().removesuffix(".exe").removesuffix(".cmd")


def lexed(command: str) -> list[list[str]]:
    """Each simple command's tokens, split on the shell operators outside quotes."""
    joined = command.replace("\\\n", " ")  # a backslash-newline continues the line
    lexer = shlex.shlex(joined, posix=True, punctuation_chars="();<>|&`\n")
    lexer.whitespace, lexer.whitespace_split, lexer.commenters = " \t\r", True, ""
    out: list[list[str]] = [[]]
    for token in lexer:  # ValueError on an unbalanced quote
        if token and set(token) <= set(lexer.punctuation_chars):
            out.append([])
        else:
            out[-1].append(token)
    return out


def loose(piece: str) -> list[str]:
    """One piece's tokens; an unparsable piece is allowed, and said once on stderr."""
    try:
        return shlex.split(piece)
    except ValueError:
        if not LOGGED:
            LOGGED.append(piece)
            print("push-guard: unparsable segment, allowed", file=sys.stderr)  # noqa: T201
        return []


def unwrap(tokens: list[str]) -> list[str]:
    """The tokens without a leading `sudo`, `command`, `env`, `export` (and flags)."""
    # ponytail: a wrapper flag's value (`sudo -u name git ..`) is not skipped; parse them if seen
    index = 0
    while index < len(tokens) and (tokens[index] in WRAP or (index and tokens[index][0] == "-")):
        index += 1
    return tokens[index:]


def script(tokens: list[str]) -> str | None:
    """The command line inside `bash -c '..'` (sh, zsh, `-lc`), else None."""
    if not tokens or tool(tokens[0]) not in SHELLS:
        return None
    flag = next((i for i, token in enumerate(tokens) if re.fullmatch(r"-[a-z]*c", token)), None)
    return tokens[flag + 1] if flag is not None and flag + 1 < len(tokens) else None


def segments(command: str, depth: int = 0) -> list[list[str]]:
    """Each simple command's tokens, wrappers dropped and `bash -c` opened.

    A heredoc or comment with a lone quote cannot be lexed whole and falls back to pieces.
    """
    try:
        raw = lexed(command)
    except ValueError:
        raw = [loose(piece) for piece in SPLIT.split(command)]
    out: list[list[str]] = []
    for tokens in map(unwrap, raw):
        inner = script(tokens) if depth < 3 else None  # noqa: PLR2004 - nesting, not a setting
        if inner is not None:
            out += segments(inner, depth + 1)
        elif tokens:
            out.append(tokens)
    return out
