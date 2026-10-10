"""Every `just` command the repo tells people to run has to exist.

Recipes are named in a lot of places nobody re-reads when a recipe is renamed:
START-HERE, the docs, the skills, hints the CLI prints, the builder's "Copy deploy
commands" button. A reader who types a name that no longer exists is told only
that just has no such recipe, at the moment they were following instructions.
This reads every `just <module> <recipe>` in the tracked text and checks it
against what `just --summary` lists.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
just = shutil.which("just")
requires_just = pytest.mark.skipif(just is None, reason="just is not installed")

#: Files that hold instructions for people.
SUFFIXES = {".md", ".py", ".js", ".html", ".example", ".json", ".toml"}
#: `just <module> <recipe>` written as a command: quoted, in backticks, or on a
#: line of a fenced code block. Prose such as "just one more" is not one.
QUOTED = re.compile(r"""[`"']just ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?""")
IN_BLOCK = re.compile(r"^\s*just ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def recipes() -> set[str]:
    out = subprocess.run(  # noqa: S603
        [just, "--summary"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return set(out.split())


def references():
    tracked = subprocess.run(  # noqa: S603
        [shutil.which("git"), "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for rel in tracked:
        path = ROOT / rel
        if path == Path(__file__).resolve():
            continue  # its own examples
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        fenced = False
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            found = list(QUOTED.finditer(line))
            if fenced and (block := IN_BLOCK.match(line)):
                found.append(block)
            for match in found:
                yield rel, number, match.group(1), match.group(2)


@requires_just
def test_every_just_command_in_the_repo_exists():
    available = recipes()
    modules = {name.split("::")[0] for name in available}
    missing = []
    for rel, line, first, second in references():
        if first not in modules:
            # "just reply with the number" is English; "just survey-build" is
            # a recipe name from before the modules.
            if "-" in first:
                missing.append(f"{rel}:{line}: just {first}")
        elif second and f"{first}::{second}" not in available:
            missing.append(f"{rel}:{line}: just {first} {second}")
    assert missing == [], "commands that are not recipes:\n" + "\n".join(missing)


@requires_just
def test_every_module_has_its_page():
    modules = {name.split("::")[0] for name in recipes()}
    pages = {p.stem for p in (ROOT / "docs" / "recipes").glob("*.md")}
    assert modules == pages
