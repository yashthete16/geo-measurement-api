import pytest
from pyproj import CRS, Geod, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPolygon,
    Point,
    Polygon,
)

from app.models import MeasurementStatus
from app.services.crs import WGS84, crs_label, utm_epsg_for
from app.services.measurement import MeasurementEngine

GEOD = Geod(ellps="WGS84")
BOX = [(73.85, 18.50), (73.86, 18.50), (73.86, 18.51), (73.85, 18.51)]
HOLE = [(73.853, 18.503), (73.857, 18.503), (73.857, 18.507), (73.853, 18.507)]


def geodesic_ring_area(points):
    xs, ys = zip(*[*points, points[0]], strict=True)
    return abs(GEOD.polygon_area_perimeter(xs, ys)[0])


# ---------------------------------------------------------------- CRS selection


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (73.85, 18.52, 32643),  # Pune
        (-0.12, 51.5, 32630),  # London (zone 30N)
        (-58.4, -34.6, 32721),  # Buenos Aires (21S)
        (179.9, 10, 32660),  # right edge of the grid
        (-180, 10, 32601),  # left edge
        (10, 85, 32661),  # UPS north
        (10, -85, 32761),  # UPS south
    ],
)
def test_utm_zone_selection(lon, lat, expected):
    assert utm_epsg_for(lon, lat) == expected


def test_crs_label():
    assert crs_label(CRS.from_epsg(4326)) == "EPSG:4326"
    assert crs_label(CRS.from_epsg(32643)) == "EPSG:32643"


# ---------------------------------------------------------------- measurements


def test_polygon_area_is_in_square_metres_not_degrees():
    result = MeasurementEngine().measure(Polygon(BOX), WGS84)
    assert result.status == MeasurementStatus.OK
    assert result.projected_crs == "EPSG:32643"
    # The 0.01° x 0.01° box is ~1.17 km². Degrees² would give 0.0001.
    assert result.area_sq_m == pytest.approx(1_168_327, rel=1e-4)
    # Within a few hundredths of a percent of the ellipsoidal truth.
    assert result.area_sq_m == pytest.approx(geodesic_ring_area(BOX), rel=1e-3)


def test_holes_are_subtracted_in_both_methods():
    result = MeasurementEngine().measure(Polygon(BOX, [HOLE]), WGS84)
    expected = geodesic_ring_area(BOX) - geodesic_ring_area(HOLE)
    assert result.area_sq_m == pytest.approx(expected, rel=1e-3)
    assert result.geodesic_area_sq_m == pytest.approx(expected, rel=1e-6)


def test_multipolygon_area_is_sum_of_parts():
    other = [(74.0, 19.0), (74.01, 19.0), (74.01, 19.01), (74.0, 19.01)]
    result = MeasurementEngine().measure(MultiPolygon([Polygon(BOX), Polygon(other)]), WGS84)
    expected = geodesic_ring_area(BOX) + geodesic_ring_area(other)
    assert result.area_sq_m == pytest.approx(expected, rel=1e-3)


def test_linestring_length():
    line = LineString([(73.85, 18.5), (73.86, 18.5)])
    result = MeasurementEngine().measure(line, WGS84)
    assert result.status == MeasurementStatus.OK
    assert result.length_m == pytest.approx(1056.0, rel=2e-3)
    assert result.length_m == pytest.approx(result.geodesic_length_m, rel=1e-3)
    assert result.area_sq_m is None


def test_multilinestring_length_is_sum():
    parts = [[(0.0, 0.0), (0.0, 1.0)], [(0.0, 2.0), (1.0, 2.0)]]
    result = MeasurementEngine().measure(MultiLineString(parts), WGS84)
    expected = sum(GEOD.geometry_length(LineString(p)) for p in parts)
    assert result.length_m == pytest.approx(expected, rel=2e-3)


def test_projected_source_crs_gives_same_answer_as_geographic():
    geographic = MeasurementEngine().measure(Polygon(BOX), WGS84)
    for epsg in (32643, 3857, 7755):  # UTM 43N, Web Mercator, India LCC
        crs = CRS.from_epsg(epsg)
        t = Transformer.from_crs(4326, crs, always_xy=True)
        projected_box = Polygon([t.transform(x, y) for x, y in BOX])
        result = MeasurementEngine().measure(projected_box, crs)
        assert result.area_sq_m == pytest.approx(geographic.area_sq_m, rel=1e-3), epsg


def test_web_mercator_is_not_trusted_for_measurement():
    """EPSG:3857 inflates area by ~1/cos²(lat); we must not report that."""
    t = Transformer.from_crs(4326, 3857, always_xy=True)
    box = Polygon([t.transform(x, y) for x, y in BOX])
    naive = box.area
    result = MeasurementEngine().measure(box, CRS.from_epsg(3857))
    assert naive > result.area_sq_m * 1.10
    assert result.area_sq_m == pytest.approx(1_168_327, rel=1e-3)


def test_z_values_are_ignored():
    result = MeasurementEngine().measure(
        LineString([(73.85, 18.5, 100), (73.86, 18.5, 900)]), WGS84
    )
    assert result.length_m == pytest.approx(1056.0, rel=2e-3)


# ---------------------------------------------------------------- graceful handling


def test_point_is_not_applicable():
    result = MeasurementEngine().measure(Point(73.85, 18.5), WGS84)
    assert result.status == MeasurementStatus.NOT_APPLICABLE
    assert result.area_sq_m is None and result.length_m is None


def test_geometry_collection_is_unsupported_not_a_crash():
    result = MeasurementEngine().measure(GeometryCollection([Point(0, 0), Polygon(BOX)]), WGS84)
    assert result.status == MeasurementStatus.UNSUPPORTED
    assert "GeometryCollection" in result.message


def test_none_and_empty_geometries():
    engine = MeasurementEngine()
    assert engine.measure(None, WGS84).status == MeasurementStatus.UNSUPPORTED
    assert engine.measure(Polygon(), WGS84).status == MeasurementStatus.UNSUPPORTED


def test_bowtie_polygon_is_repaired_and_flagged():
    bowtie = Polygon([(73.85, 18.50), (73.86, 18.51), (73.86, 18.50), (73.85, 18.51)])
    assert not bowtie.is_valid
    result = MeasurementEngine().measure(bowtie, WGS84)
    assert result.status == MeasurementStatus.OK
    assert result.area_sq_m > 0
    assert "repaired" in result.message


def test_out_of_range_coordinates_are_a_feature_error():
    result = MeasurementEngine().measure(LineString([(500, 10), (501, 10)]), WGS84)
    assert result.status == MeasurementStatus.ERROR
    assert "range" in result.message


def test_southern_hemisphere_uses_south_zone():
    box = [(-58.40, -34.60), (-58.39, -34.60), (-58.39, -34.59), (-58.40, -34.59)]
    result = MeasurementEngine().measure(Polygon(box), WGS84)
    assert result.projected_crs == "EPSG:32721"
    assert result.area_sq_m == pytest.approx(geodesic_ring_area(box), rel=1e-3)
