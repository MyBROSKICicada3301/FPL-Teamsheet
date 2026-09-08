"""FPL Assistant, squad, transfer and wildcard advice for the next gameweek."""

# Settings are loaded here, before any submodule is imported, because several
# of them read configuration into module-level constants at import time. See
# fpl/env.py for why the environment still takes precedence over the file.
from . import env as _env

_env.load()

__version__ = "0.1.0"
