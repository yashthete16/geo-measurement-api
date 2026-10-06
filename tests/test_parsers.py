import io
import zipfile

import pytest
import shapefile

from app.services.parsers.base import ParseError
from app.services.parsers.kml import parse_kml
from app.services.parsers.shapefile_zip import parse_shapefile_zip
from tests import helpers as h

LIMITS = {"max_uncompressed_bytes": 500_000, "max_members": 50}


# ------------------------------------------------------------------------- KML


def test_kml_basic_features_and_properties():
    content = h.kml(
        h.polygon_placemark("a", hole=h.HOLE),
        h.line_placemark("b"),
        h.point_placemark("c"),
    )
    parsed = parse_kml(content)
    assert [f.geometry.geom_type for f in parsed.features] == ["Polygon", "LineString", "Point"]
    assert parsed.features[0].properties == {"name": "a"}
    assert len(parsed.features[0].geometry.interiors) == 1
    assert parsed.crs.to_epsg() == 4326
    # altitude stripped
    assert not parsed.features[2].geometry.has_z


def test_kml_extended_data_and_nested_folders():
    content = (
        b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><Folder>'
        b"<Placemark><name>x</name><description>desc</description>"
        b'<ExtendedData><Data name="owner"><value>Asha</value></Data>'
        b'<SchemaData><SimpleData name="class">A</SimpleData></SchemaData></ExtendedData>'
        b"<Point><coordinates>1,2</coordinates></Point></Placemark>"
        b"</Folder></Folder></Document></kml>"
    )
    props = parse_kml(content).features[0].properties
    assert props == {"name": "x", "description": "desc", "owner": "Asha", "class": "A"}


def test_kml_other_namespace_versions_and_spaces_after_commas():
    content = (
        b'<kml xmlns="http://earth.google.com/kml/2.1"><Placemark>'
        b"<LineString><coordinates>1, 2, 0  3, 4, 0</coordinates></LineString>"
        b"</Placemark></kml>"
    )
    geom = parse_kml(content).features[0].geometry
    assert list(geom.coords) == [(1.0, 2.0), (3.0, 4.0)]


def test_kml_multigeometry_homogeneous_and_mixed():
    poly = (
        "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
        "0,0 1,0 1,1 0,0</coordinates></LinearRing></outerBoundaryIs></Polygon>"
    )
    point = "<Point><coordinates>5,5</coordinates></Point>"
    content = h.kml(
        f"<Placemark><MultiGeometry>{poly}{poly}</MultiGeometry></Placemark>",
        f"<Placemark><MultiGeometry>{poly}{point}</MultiGeometry></Placemark>",
    )
    types = [f.geometry.geom_type for f in parse_kml(content).features]
    assert types == ["MultiPolygon", "GeometryCollection"]


def test_kml_placemark_without_geometry_is_kept():
    parsed = parse_kml(h.kml("<Placemark><name>label</name></Placemark>"))
    assert parsed.features[0].geometry is None
    assert parsed.features[0].error is None


def test_kml_one_bad_placemark_does_not_fail_the_file():
    bad = "<Placemark><name>bad</name><Point><coordinates>abc,def</coordinates></Point></Placemark>"
    parsed = parse_kml(h.kml(h.point_placemark(), bad, h.line_placemark()))
    assert [f.error is None for f in parsed.features] == [True, False, True]
    assert "Non-numeric" in parsed.features[1].error


def test_kml_degenerate_polygon_is_a_feature_error():
    bad = (
        "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>1,1 2,2"
        "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )
    assert parse_kml(h.kml(bad)).features[0].error


@pytest.mark.parametrize(
    "content",
    [b"not xml at all", b"<kml><Placemark></kml>", b"<html></html>", b"<kml></kml>"],
)
def test_kml_rejects_unusable_files(content):
    with pytest.raises(ParseError):
        parse_kml(content)


def test_kml_rejects_entity_expansion_bomb():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b"<kml><Placemark><name>&b;</name></Placemark></kml>"
    )
    with pytest.raises(ParseError):
        parse_kml(bomb)


# ------------------------------------------------------------------- Shapefile


def _poly_parts(box=h.BOX, hole=None):
    parts = [h.ring(box, clockwise=True)]
    if hole:
        parts.append(h.ring(hole, clockwise=False))
    return parts


def test_shapefile_polygon_with_hole_and_attributes():
    content = h.shapefile_zip([_poly_parts(hole=h.HOLE)])
    parsed = parse_shapefile_zip(content, **LIMITS)
    feature = parsed.features[0]
    assert feature.geometry.geom_type == "Polygon"
    assert len(feature.geometry.interiors) == 1
    assert feature.properties == {"NAME": "feature 0", "N": 0}
    assert parsed.crs.to_epsg() == 4326
    assert parsed.warnings == []


def test_shapefile_polylines_and_null_shapes():
    parts = {k: io.BytesIO() for k in ("shp", "shx", "dbf")}
    with shapefile.Writer(**parts, shapeType=shapefile.POLYLINE) as w:
        w.field("NAME", "C")
        w.line([[[0, 0], [1, 1]]])
        w.record("line")
        w.null()
        w.record("empty")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for ext, bio in parts.items():
            zf.writestr(f"x.{ext}", bio.getvalue())
        zf.writestr("x.prj", h.PRJ_WGS84)
    parsed = parse_shapefile_zip(buf.getvalue(), **LIMITS)
    assert parsed.features[0].geometry.geom_type == "LineString"
    assert parsed.features[1].geometry is None


def test_shapefile_points():
    content = h.shapefile_zip([[[(73.85, 18.5)]]], shape_type=shapefile.POINT)
    assert parse_shapefile_zip(content, **LIMITS).features[0].geometry.geom_type == "Point"


def test_shapefile_without_prj_but_lonlat_assumes_wgs84_with_warning():
    parsed = parse_shapefile_zip(h.shapefile_zip([_poly_parts()], prj=None), **LIMITS)
    assert parsed.crs.to_epsg() == 4326
    assert any("assuming EPSG:4326" in w for w in parsed.warnings)


def test_shapefile_without_prj_and_projected_coordinates_is_rejected():
    big = [(380_000, 2_048_000), (381_000, 2_048_000), (381_000, 2_049_000), (380_000, 2_049_000)]
    content = h.shapefile_zip([_poly_parts(big)], prj=None)
    with pytest.raises(ParseError, match="cannot be determined"):
        parse_shapefile_zip(content, **LIMITS)


def test_shapefile_without_dbf_still_works_with_warning():
    parsed = parse_shapefile_zip(h.shapefile_zip([_poly_parts()], with_dbf=False), **LIMITS)
    assert parsed.features[0].properties == {}
    assert any(".dbf" in w for w in parsed.warnings)


def test_shapefile_in_subfolder_and_macosx_junk_is_ignored():
    content = h.shapefile_zip(
        [_poly_parts()],
        stem="project/layers/parcels",
        extra_files={"__MACOSX/project/._parcels.shp": b"junk", "project/readme.txt": b"hi"},
    )
    assert len(parse_shapefile_zip(content, **LIMITS).features) == 1


def test_zip_without_shp_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notes.txt", "nothing here")
    with pytest.raises(ParseError, match=r"No \.shp"):
        parse_shapefile_zip(buf.getvalue(), **LIMITS)


def test_zip_with_two_shapefiles_is_rejected():
    first = h.shapefile_zip([_poly_parts()], stem="a")
    buf = io.BytesIO(first)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("b.shp", b"x")
    with pytest.raises(ParseError, match="exactly one shapefile"):
        parse_shapefile_zip(buf.getvalue(), **LIMITS)


def test_not_a_zip_is_rejected():
    with pytest.raises(ParseError, match="not a valid .zip"):
        parse_shapefile_zip(b"PK\x03\x04garbage", **LIMITS)


def test_corrupt_shp_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.shp", b"\x00" * 10)
        zf.writestr("a.prj", h.PRJ_WGS84)
    with pytest.raises(ParseError):
        parse_shapefile_zip(buf.getvalue(), **LIMITS)


def test_zip_bomb_is_stopped_by_uncompressed_budget():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.shp", b"\x00" * 2_000_000)  # compresses to ~2 KB
    assert len(buf.getvalue()) < 10_000
    with pytest.raises(ParseError, match="uncompressed"):
        parse_shapefile_zip(buf.getvalue(), **LIMITS)


def test_garbage_prj_is_rejected():
    content = h.shapefile_zip([_poly_parts()], prj="this is not wkt")
    with pytest.raises(ParseError, match=r"\.prj"):
        parse_shapefile_zip(content, **LIMITS)
