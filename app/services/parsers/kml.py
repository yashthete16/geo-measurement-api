"""KML parser (pure Python, no GDAL).

Handles Placemarks at any nesting depth (Document / Folder), the 2.0-2.3 namespaces,
Point / LineString / LinearRing / Polygon (with holes) / MultiGeometry / gx:Track,
and ``ExtendedData`` (``Data`` and ``SimpleData``). KML is always WGS84 by specification.

Parsing uses ``defusedxml`` so entity-expansion ("billion laughs") uploads are rejected.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from shapely.errors import ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.services.crs import WGS84
from app.services.parsers.base import ParsedFeature, ParsedFile, ParseError

_GEOMETRY_TAGS = {
    "Point",
    "LineString",
    "LinearRing",
    "Polygon",
    "MultiGeometry",
    "Track",
    "MultiTrack",
}


def _local(tag: object) -> str:
    """Tag name without its namespace (comments/PIs have non-string tags)."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el: ET.Element, name: str) -> Iterator[ET.Element]:
    return (c for c in el if _local(c.tag) == name)


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next(_children(el, name), None)


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _parse_coordinates(el: ET.Element) -> list[tuple[float, float]]:
    """``<coordinates>`` -> [(lon, lat), ...]. Altitude is dropped (measurements are 2D)."""
    node = _child(el, "coordinates")
    text = _text(node)
    if not text:
        raise ValueError(f"<{_local(el.tag)}> has no coordinates")
    # Tolerate "lon, lat, alt" written with spaces after commas.
    text = re.sub(r"\s*,\s*", ",", text)
    points: list[tuple[float, float]] = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"Malformed coordinate tuple {token!r}")
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError as exc:
            raise ValueError(f"Non-numeric coordinate {token!r}") from exc
    return points


def _ring(boundary: ET.Element) -> list[tuple[float, float]]:
    ring_el = _child(boundary, "LinearRing")
    if ring_el is None:
        raise ValueError("Polygon boundary has no <LinearRing>")
    return _parse_coordinates(ring_el)


def _track_points(el: ET.Element) -> list[tuple[float, float]]:
    points = []
    for coord in _children(el, "coord"):  # gx:coord -> "lon lat alt"
        parts = (coord.text or "").split()
        if len(parts) < 2:
            raise ValueError("Malformed <gx:coord>")
        points.append((float(parts[0]), float(parts[1])))
    return points


def _geometry(el: ET.Element) -> BaseGeometry:
    kind = _local(el.tag)

    if kind == "Point":
        points = _parse_coordinates(el)
        return Point(points[0])

    if kind in ("LineString", "LinearRing"):
        return LineString(_parse_coordinates(el))

    if kind == "Polygon":
        outer = _child(el, "outerBoundaryIs")
        if outer is None:
            raise ValueError("Polygon has no <outerBoundaryIs>")
        holes = [_ring(b) for b in _children(el, "innerBoundaryIs")]
        return Polygon(_ring(outer), holes)

    if kind == "Track":
        return LineString(_track_points(el))

    if kind in ("MultiGeometry", "MultiTrack"):
        parts = [_geometry(c) for c in el if _local(c.tag) in _GEOMETRY_TAGS]
        return _combine(parts)

    raise ValueError(f"Unsupported KML geometry <{kind}>")


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    """Homogeneous collections become Multi*; mixed ones stay a GeometryCollection."""
    kinds = {p.geom_type for p in parts}
    if kinds == {"Point"}:
        return MultiPoint(parts)
    if kinds == {"LineString"}:
        return MultiLineString(parts)
    if kinds == {"Polygon"}:
        return MultiPolygon(parts)
    return GeometryCollection(parts)


def _properties(placemark: ET.Element) -> dict:
    props: dict = {}
    for tag in ("name", "description"):
        value = _text(_child(placemark, tag))
        if value is not None:
            props[tag] = value

    extended = _child(placemark, "ExtendedData")
    if extended is not None:
        for node in extended.iter():
            name = node.get("name")
            if not name:
                continue
            tag = _local(node.tag)
            if tag == "Data":
                props[name] = _text(_child(node, "value"))
            elif tag == "SimpleData":
                props[name] = _text(node)
    return props


def parse_kml(content: bytes) -> ParsedFile:
    try:
        root = SafeET.fromstring(content)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ParseError(f"Invalid KML (not well-formed XML): {exc}") from exc

    if _local(root.tag) != "kml":
        raise ParseError("Invalid KML: root element is not <kml>")

    features: list[ParsedFeature] = []
    for placemark in root.iter():
        if _local(placemark.tag) != "Placemark":
            continue

        index = len(features)
        props = _properties(placemark)
        geoms = [c for c in placemark if _local(c.tag) in _GEOMETRY_TAGS]

        try:
            if not geoms:
                geometry = None
            elif len(geoms) == 1:
                geometry = _geometry(geoms[0])
            else:  # not valid KML, but seen in the wild
                geometry = _combine([_geometry(g) for g in geoms])
            features.append(ParsedFeature(index, geometry, props))
        except (ValueError, ShapelyError) as exc:
            features.append(ParsedFeature(index, None, props, error=str(exc)))

    if not features:
        raise ParseError("No <Placemark> features found in the KML file")

    return ParsedFile(features=features, crs=WGS84)
