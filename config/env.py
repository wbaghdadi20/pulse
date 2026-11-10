"""Typed readers for environment variables: the only place settings.py reads the env.

Each parsing rule lives here once, as a pure function over (name, environ). Tests
pass a plain dict as `environ` instead of touching the process environment, so they
run without Django and without a database. A blank value counts as unset: Compose
passes `KEY=` from .env as "", and neither "" nor whitespace is ever a valid port,
flag or count.
"""

import os
from collections.abc import Mapping

TRUE_WORDS = frozenset({"true", "1"})
FALSE_WORDS = frozenset({"false", "0"})


class EnvError(Exception):
    """A required variable is missing or its value cannot be parsed."""


def env_str(
    name: str, default: str | None = None, *, environ: Mapping[str, str] = os.environ
) -> str:
    raw = _lookup(name, environ)
    if raw is None:
        return _or_default(name, default)
    return raw


def env_bool(
    name: str, default: bool | None = None, *, environ: Mapping[str, str] = os.environ
) -> bool:
    raw = _lookup(name, environ)
    if raw is None:
        return _or_default(name, default)
    word = raw.strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise EnvError(f"{name}={raw!r} is not a boolean; use true/false or 1/0")


def env_int(
    name: str, default: int | None = None, *, environ: Mapping[str, str] = os.environ
) -> int:
    raw = _lookup(name, environ)
    if raw is None:
        return _or_default(name, default)
    try:
        return int(raw)
    except ValueError:
        raise EnvError(f"{name}={raw!r} is not an integer") from None


def env_list(
    name: str,
    default: list[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
) -> list[str]:
    raw = _lookup(name, environ)
    if raw is None:
        return list(_or_default(name, default))
    return [item.strip() for item in raw.split(",") if item.strip()]


def _lookup(name: str, environ: Mapping[str, str]) -> str | None:
    value = environ.get(name)
    return None if value is None or value.strip() == "" else value


def _or_default[T](name: str, default: T | None) -> T:
    if default is None:
        raise EnvError(f"{name} is not set")
    return default
