"""The user's own text editor, opened on a file in the data repo.

Some things are better edited than typed at a prompt. An address runs to four
lines and a set of line items to a dozen, so `client edit` and `new` hand the
file to `$EDITOR` and read back whatever comes out, the way `git commit` and
`crontab -e` do. That means billkeeper does not need a form for every field,
and the user keeps the editor and the key bindings they already have.

`$VISUAL` is consulted before `$EDITOR` because that is the older convention
and still the one that distinguishes a full-screen editor from a line editor,
and `vi` is the last resort because POSIX requires it to be there. The value is
split the way a shell would split it, so `EDITOR="code --wait"` works, but it
is then run as an argv list: a filename is a filename, never something the
shell gets a second look at.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Final

from billkeeper.errors import BillkeeperError

#: The environment variables naming an editor, in the order they are consulted.
EDITOR_ENV_VARS: Final = ("VISUAL", "EDITOR")

#: What to open when the environment names nothing. POSIX guarantees it exists.
FALLBACK_EDITOR: Final = "vi"


def editor_command() -> list[str]:
    """Return the command to run as an argv list, without the file to open."""
    for name in EDITOR_ENV_VARS:
        value = os.environ.get(name, "")
        if not value.strip():
            continue
        try:
            argv = shlex.split(value)
        except ValueError as exc:
            raise BillkeeperError(
                f"${name} is set to {value!r}, which is not a command billkeeper can "
                f"read: {exc}. Fix it, or unset it to fall back to {FALLBACK_EDITOR}."
            ) from None
        if argv:
            return argv
    return [FALLBACK_EDITOR]


def open_in_editor(path: Path) -> None:
    """Open `path` in the user's editor and wait for them to finish with it.

    Nothing is captured: the editor is talking to the same terminal the user
    is. An editor that exits non-zero is taken at its word and reported, since
    the alternative is reading back a file it may have abandoned half-written.
    """
    argv = [*editor_command(), str(path)]
    try:
        completed = subprocess.run(argv, check=False)
    except OSError as exc:
        raise BillkeeperError(
            f"Cannot run the editor {argv[0]!r}: {exc.strerror}. "
            f"Set $EDITOR to one that is installed."
        ) from None

    if completed.returncode != 0:
        raise BillkeeperError(
            f"The editor {argv[0]!r} exited with status {completed.returncode}, so "
            f"{path} has been left as it was and nothing was committed."
        )
