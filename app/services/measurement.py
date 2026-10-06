"""Area / length calculation.

Pipeline for every supported feature::

    source CRS ──► WGS84 lon/lat ──► UTM zone chosen from the feature's bbox centre ──► measure

Going through WGS84 first (rather than trusting whatever projected CRS the file carries)
means a file in Web Mercator or a distorted national grid is still measured correctly.
Each result is cross-checked against an ellipsoidal (geodesic) calculation on WGS84.
"""

import math
from dataclasses import dataclass

from pyproj import CRS, Geod, Transformer
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union
from shapely.validation import make_valid

from app.models import MeasurementStatus
from app.services.crs import WGS84, normalise_horizontal, utm_epsg_for

POLYGONAL = frozenset({"Polygon", "MultiPolygon"})
LINEAR = frozenset({"LineString", "MultiLineString"})
POINTAL = frozenset({"Point", "MultiPoint"})

# Warn when projected and geodesic results disagree by more than this.
DIVERGENCE_WARN_PCT = 0.5

_GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class MeasurementResult:
    status: MeasurementStatus
    message: str | None = None
    projected_crs: str | None = None
    area_sq_m: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    geodesic_area_sq_m: float | None = None
    geodesic_length_m: float | None = None

    @classmethod
    def of(cls, status: MeasurementStatus, message: str) -> "MeasurementResult":
        return cls(status=status, message=message)


class MeasurementError(ValueError):
    """A single feature could not be measured (reported per feature, never fatal)."""


def _polygonal_part(geom: BaseGeometry) -> BaseGeometry | None:
    """Keep only the area-bearing part of a repaired geometry."""
    if geom.geom_type in POLYGONAL:
        return geom
    if geom.geom_type == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in POLYGONAL and not g.is_empty]
        return unary_union(parts) if parts else None
    return None


def _ring_area(ring) -> float:
    xs, ys = ring.xy
    return abs(_GEOD.polygon_area_perimeter(xs, ys)[0])


def _geodesic_area(geom: BaseGeometry) -> float:
    """Ellipsoidal area, subtracting holes.

    ``Geod.geometry_area_perimeter`` *adds* interior rings, so rings are handled explicitly.
    """
    polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    total = 0.0
    for poly in polygons:
        total += _ring_area(poly.exterior) - sum(_ring_area(r) for r in poly.interiors)
    return total


class MeasurementEngine:
    """Measures geometries. Create one per upload: it caches pyproj transformers, which are
    not shared across threads."""

    def __init__(self) -> None:
        self._transformers: dict[tuple[str, int | str], Transformer] = {}

    def _transformer(self, src: CRS, dst: CRS, key: tuple[str, int | str]) -> Transformer:
        if key not in self._transformers:
            self._transformers[key] = Transformer.from_crs(src, dst, always_xy=True)
        return self._transformers[key]

    def _to_wgs84(self, geom: BaseGeometry, source_crs: CRS) -> BaseGeometry:
        source = normalise_horizontal(source_crs)
        if source == WGS84:
            return geom
        t = self._transformer(source, WGS84, (source.to_wkt(), "wgs84"))
        return transform(t.transform, geom)

    def _to_utm(self, geom: BaseGeometry, epsg: int) -> BaseGeometry:
        t = self._transformer(WGS84, CRS.from_epsg(epsg), ("wgs84", epsg))
        return transform(t.transform, geom)

    def measure(self, geom: BaseGeometry | None, source_crs: CRS) -> MeasurementResult:
        if geom is None:
            return MeasurementResult.of(MeasurementStatus.UNSUPPORTED, "Feature has no geometry")

        kind = geom.geom_type
        if kind in POINTAL:
            return MeasurementResult.of(
                MeasurementStatus.NOT_APPLICABLE, "Points have no area or length"
            )
        if kind not in POLYGONAL | LINEAR:
            return MeasurementResult.of(
                MeasurementStatus.UNSUPPORTED, f"Measurement is not supported for {kind} geometries"
            )
        if geom.is_empty:
            return MeasurementResult.of(MeasurementStatus.UNSUPPORTED, "Geometry is empty")

        try:
            return self._measure_supported(geom, source_crs)
        except (MeasurementError, ShapelyError, ValueError) as exc:
            return MeasurementResult.of(MeasurementStatus.ERROR, str(exc))

    def _measure_supported(self, geom: BaseGeometry, source_crs: CRS) -> MeasurementResult:
        notes: list[str] = []
        wgs = self._to_wgs84(geom, source_crs)

        minx, miny, maxx, maxy = wgs.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise MeasurementError(
                "Coordinates fall outside valid longitude/latitude range after transformation; "
                "check the file's CRS"
            )

        if geom.geom_type in POLYGONAL and not wgs.is_valid:
            repaired = _polygonal_part(make_valid(wgs))
            if repaired is None or repaired.is_empty:
                raise MeasurementError("Invalid polygon could not be repaired")
            wgs = repaired
            notes.append("Self-intersecting polygon was repaired before measuring")

        epsg = utm_epsg_for((minx + maxx) / 2, (miny + maxy) / 2)
        projected = self._to_utm(wgs, epsg)
        if not all(map(_finite, projected.bounds)):
            raise MeasurementError(f"Projection to EPSG:{epsg} failed")

        crs_name = f"EPSG:{epsg}"

        if wgs.geom_type in POLYGONAL:
            area = projected.area
            geodesic = _geodesic_area(wgs)
            self._maybe_warn_divergence(area, geodesic, "area", notes)
            return MeasurementResult(
                status=MeasurementStatus.OK,
                message="; ".join(notes) or None,
                projected_crs=crs_name,
                area_sq_m=area,
                perimeter_m=projected.length,
                geodesic_area_sq_m=geodesic,
            )

        length = projected.length
        geodesic_len = _GEOD.geometry_length(wgs)
        self._maybe_warn_divergence(length, geodesic_len, "length", notes)
        return MeasurementResult(
            status=MeasurementStatus.OK,
            message="; ".join(notes) or None,
            projected_crs=crs_name,
            length_m=length,
            geodesic_length_m=geodesic_len,
        )

    @staticmethod
    def _maybe_warn_divergence(projected: float, geodesic: float, what: str, notes: list) -> None:
        if geodesic > 0:
            diff = abs(projected - geodesic) / geodesic * 100
            if diff > DIVERGENCE_WARN_PCT:
                notes.append(
                    f"Projected {what} differs from the geodesic value by {diff:.2f}% "
                    "(feature is large or far from the UTM zone centre); prefer the geodesic figure"
                )


def _finite(value: float) -> bool:
    return math.isfinite(value)
