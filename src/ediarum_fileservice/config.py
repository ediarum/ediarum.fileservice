"""Environment configuration."""

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(Exception):
    "Invalid or missing environment configuration."


MOUNT_ROOT_ENV = "XML_MOUNT_ROOT"
DATABASE_URL_ENV = "DATABASE_URL"
CHECK_TIMES_ENV = "CHECK_TIMES"
CHECK_TIMEZONE_ENV = "CHECK_TIMEZONE"

DEFAULT_CHECK_TIMES = "03:00,15:00"
DEFAULT_CHECK_TIMEZONE = "Europe/Berlin"

# settings of the commit-aware reconciliation this service no longer does; a
# leftover value is ignored (with a warning at startup), not an error
REMOVED_ENV = ("RECONCILE_INTERVAL_SECONDS", "RECONCILE_GRACE_PERIOD_SECONDS")


def parse_check_times(value: str) -> tuple[time, ...]:
    "``03:00,15:00`` → sorted, de-duplicated times; empty → no checks."
    times = set()
    for part in (p.strip() for p in value.split(",")):
        if not part:
            continue
        try:
            hour, minute = part.split(":")
            times.add(time(int(hour), int(minute)))
        except ValueError:
            raise ConfigError(f"{CHECK_TIMES_ENV}: {part!r} is not HH:MM")
    return tuple(sorted(times))


@dataclass(frozen=True)
class Settings:
    "The settings the app and the key CLI read."

    mount_root: Path
    database_url: str
    check_times: tuple[time, ...] = ()  # empty = no scheduled checks
    check_timezone: ZoneInfo = ZoneInfo("UTC")

    @classmethod
    def from_env(cls) -> "Settings":
        "From the environment; ``ConfigError`` without ``XML_MOUNT_ROOT`` or with a bad value."
        mount_root = os.getenv(MOUNT_ROOT_ENV)
        if not mount_root:
            raise ConfigError(f"set {MOUNT_ROOT_ENV} (the shared bind mount, e.g. /data/xml)")
        zone = os.getenv(CHECK_TIMEZONE_ENV, DEFAULT_CHECK_TIMEZONE)
        try:
            check_timezone = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"{CHECK_TIMEZONE_ENV}: unknown time zone {zone!r}")
        return cls(
            mount_root=Path(mount_root),
            database_url=os.getenv(DATABASE_URL_ENV, "sqlite:////data/fileservice.db"),
            check_times=parse_check_times(os.getenv(CHECK_TIMES_ENV, DEFAULT_CHECK_TIMES)),
            check_timezone=check_timezone,
        )


def removed_settings_in_env() -> list[str]:
    "Removed settings that are still set — for the startup warning."
    return [name for name in REMOVED_ENV if os.getenv(name) is not None]
