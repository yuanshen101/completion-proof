"""Environment allowlist for isolated Hermes test runs.

The integration harness must not inherit an operator's credentials or profile
switches. Hermes and its child processes receive only the runtime essentials
needed to start, plus explicitly supplied dummy/model values. Windows keeps the
few variables the OS loader and Git-for-Windows need (``SYSTEMROOT``, ``WINDIR``,
``COMSPEC``, ``PATHEXT``).
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

# Deliberately small. Do not add credential-shaped names here.
ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
    # Windows process loader / shell essentials.
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


def sandbox_env(
    extra: Optional[Mapping[str, object]] = None,
    *,
    source: Optional[Mapping[str, str]] = None,
) -> dict:
    """Return a filtered copy of the environment plus explicit values."""
    base = os.environ if source is None else source
    env = {name: base[name] for name in ENV_ALLOWLIST if name in base}
    if extra:
        for key, value in extra.items():
            if value is None:
                env.pop(str(key), None)
            else:
                env[str(key)] = str(value)
    return env
