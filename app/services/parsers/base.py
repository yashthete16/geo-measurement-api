"""Shared types for the format-specific parsers."""

import datetime as dt
import decimal
import math
from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


class ParseError(ValueError):
    """The whole file is unusable (corrupt, wrong format, unknown CRS...). Mapped to HTTP 422."""


@dataclass
class ParsedFeature:
    index: int
    geometry: BaseGeometry | None
    properties: dict[str, Any] = field(default_factory=dict)
    # Set when this one feature's geometry could not be built. The file still succeeds.
    error: str | None = None


@dataclass
class ParsedFile:
    features: list[ParsedFeature]
    crs: CRS
    warnings: list[str] = field(default_factory=list)


def to_jsonable(value: Any) -> Any:
    """Make DBF / KML attribute values safe to store as JSON."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
