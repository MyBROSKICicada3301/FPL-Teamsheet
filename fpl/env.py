"""Read settings from a .env file, so secrets live outside the code.

Small and deliberate rather than a dependency: the file format is a handful of
rules and this project has no package manager.

Two decisions worth stating.

**The environment wins over the file.** A variable already set in the shell is
never overwritten by .env. That is what makes a one-off override work,

    GEMINI_MODEL=gemini-3.6-flash ./start.sh

and it means a value exported by a deployment is not silently replaced by a
stale checkout.

**Loading happens at package import**, in `fpl/__init__.py`. Several modules
read their configuration into constants at import time, so anything loading
later would arrive after those constants were already fixed. Importing a
package for its side effect is not something to do lightly; it is done here
because the alternative is every module deferring its own configuration, which
is a lot of machinery for one file of settings.
"""

import os
from pathlib import Path

#: Project root, one level above this package.
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PATH = ROOT / ".env"


def parse(text: str) -> dict[str, str]:
    """Parse .env text into a mapping.

    Supported: `KEY=value`, `export KEY=value`, `#` comments on their own line,
    blank lines, and values wrapped in single or double quotes. Values are not
    interpolated: a `$` is a dollar sign, which is what someone pasting a
    generated key expects.
    """
    out: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        key, sep, value = line.partition("=")
        if not sep:
            continue                      # not an assignment; ignore quietly
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # An unquoted value may carry a trailing comment; a quoted one may
            # legitimately contain a '#'.
            value = value.split(" #", 1)[0].rstrip()

        out[key] = value

    return out


def load(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """Load `path` into os.environ. Returns what was applied.

    A missing file is not an error. The tool works without one; .env only
    saves typing and keeps secrets out of shell history.
    """
    target = Path(path) if path else DEFAULT_PATH
    try:
        text = target.read_text()
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return {}

    applied = {}
    for key, value in parse(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
