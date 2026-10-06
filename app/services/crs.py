"""Coordinate reference system helpers.

The core rule of this service: **never measure in degrees**. Anything geographic is
transformed to a metric projected CRS first (see :func:`utm_epsg_for`).
"""

from pyproj import CRS

WGS84 = CRS.from_epsg(4326)
WGS84_LABEL = "EPSG:4326"

# Universal Polar Stereographic, used where UTM is not defined (beyond 84°N / 80°S).
_UPS_NORTH = 32661
_UPS_SOUTH = 32761


class UnsupportedCRSError(ValueError):
    """Raised for CRS definitions we cannot measure with (local/engineering, vertical-only...)."""


def normalise_horizontal(crs: CRS) -> CRS:
    """Return the horizontal (geographic or projected) component of ``crs``."""
    if crs.is_compound:
        for sub in crs.sub_crs_list:
            if sub.is_geographic or sub.is_projected:
                return sub
    if crs.is_geographic or crs.is_projected:
        return crs
    raise UnsupportedCRSError(f"Unsupported CRS type: {crs.name!r}")


def crs_label(crs: CRS) -> str:
    """Human-friendly identifier: ``EPSG:4326`` when we can identify it, else the CRS name."""
    authority = crs.to_authority(min_confidence=70)
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.name or "UNKNOWN"


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone (or UPS at the poles) that contains ``lon, lat``.

    UTM keeps distortion below ~0.1 % inside a zone, which is plenty for surveying-grade
    reporting, and is metric, so areas/lengths come out in m² / m directly.
    """
    if lat >= 84:
        return _UPS_NORTH
    if lat <= -80:
        return _UPS_SOUTH
    zone = min(max(int((lon + 180) // 6) + 1, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def looks_like_lonlat(xmin: float, ymin: float, xmax: float, ymax: float) -> bool:
    """True if a bounding box is plausible as longitude/latitude degrees."""
    return -180 <= xmin <= xmax <= 180 and -90 <= ymin <= ymax <= 90
