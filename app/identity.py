"""The accounts tsconfig knows by name, defined once.

The operator is the account tsconfig runs as (the Raspberry Pi OS default
user); staff is the account behind the `su`-checked login. Everything that
needs the operator's name or home directory takes it from here.
"""

import pwd
from pathlib import Path

OPERATOR_USER = "pi"
STAFF_USER = "root"


def _operator_home() -> Path:
    try:
        return Path(pwd.getpwnam(OPERATOR_USER).pw_dir)
    except KeyError:
        # Dev machines, tests and server mode have no such account.
        return Path("/home") / OPERATOR_USER


OPERATOR_HOME = _operator_home()
