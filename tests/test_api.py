import io
import zipfile

import pytest

from tests import helpers as h


def upload(client, name, content, content_type="application/octet-stream"):
    return client.post("/api/files/", files={"file": (name, io.BytesIO(content), content_type)})


@pytest.fixture
def kml_file(client):
    content = h.kml(
        h.polygon_placemark("park", hole=h.HOLE),
        h.line_placemark("road"),
        h.point_placemark("pin"),
        "<Placemark><name>label</name></Placemark>",
    )
    r = upload(client, "survey.kml", content)
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


# --------------------------------------------------------------------- upload


def test_upload_kml_returns_file_info(kml_file):
    assert kml_file["filename"] == "survey.kml"
    assert kml_file["feature_count"] == 4
    assert kml_file["crs"] == "EPSG:4326"
    assert kml_file["status"] == "COMPLETED"
    assert kml_file["file_type"] == "kml"
    assert len(kml_file["id"]) == 32


def test_upload_shapefile_zip(client):
    zipped = h.shapefile_zip([[h.ring(h.BOX, clockwise=True)]])
    r = upload(client, "parcels.zip", zipped, "application/zip")
    assert r.status_code == 201
    body = r.json()
    assert (body["file_type"], body["crs"], body["feature_count"]) == ("shapefile", "EPSG:4326", 1)


def test_trailing_slash_redirect_still_works(client):
    r = client.post("/api/files", files={"file": ("a.kml", h.kml(h.point_placemark()))})
    assert r.status_code == 201


@pytest.mark.parametrize("name", ["data.geojson", "photo.png", "noextension", "x.shp"])
def test_unsupported_extension_is_415(client, name):
    assert upload(client, name, b"hello").status_code == 415


def test_fake_zip_is_415(client):
    assert upload(client, "x.zip", b"this is text").status_code == 415


def test_empty_upload_is_400(client):
    assert upload(client, "empty.kml", b"").status_code == 400


def test_oversized_upload_is_413(client, settings):
    assert upload(client, "big.kml", b"x" * (settings.max_upload_bytes + 10)).status_code == 413


def test_corrupt_kml_is_422_and_recorded_as_failed(client):
    r = upload(client, "broken.kml", b"<kml><oops>")
    assert r.status_code == 422
    body = r.json()
    assert "Invalid KML" in body["detail"]
    assert body["file"]["status"] == "FAILED"

    info = client.get(f"/api/files/{body['file']['id']}/").json()
    assert info["status"] == "FAILED" and info["error_message"]
    # Measurements for a failed file are a conflict, not a crash
    assert client.get(f"/api/files/{info['id']}/measurements/").status_code == 409


def test_upload_with_unmeasurable_geometry_still_completes(client):
    r = upload(
        client, "mixed.kml", h.kml(h.point_placemark(), "<Placemark><name>x</name></Placemark>")
    )
    assert r.status_code == 201 and r.json()["status"] == "COMPLETED"


def test_unreadable_placemark_adds_a_warning(client):
    bad = "<Placemark><Point><coordinates>a,b</coordinates></Point></Placemark>"
    r = upload(client, "w.kml", h.kml(h.point_placemark(), bad))
    assert r.status_code == 201
    assert any("unreadable geometry" in w for w in r.json()["warnings"])


# ------------------------------------------------------------------ file info


def test_get_file_info(client, kml_file):
    r = client.get(f"/api/files/{kml_file['id']}/")
    assert r.status_code == 200
    assert r.json() == kml_file


def test_unknown_file_is_404_everywhere(client):
    for path in ("", "features/", "measurements/"):
        assert client.get(f"/api/files/doesnotexist/{path}").status_code == 404


def test_list_and_delete(client, kml_file):
    listing = client.get("/api/files/").json()
    assert listing["total"] == 1 and listing["files"][0]["id"] == kml_file["id"]

    assert client.delete(f"/api/files/{kml_file['id']}/").status_code == 204
    assert client.get(f"/api/files/{kml_file['id']}/").status_code == 404
    assert client.get("/api/files/").json()["total"] == 0
    assert client.delete(f"/api/files/{kml_file['id']}/").status_code == 404


# ----------------------------------------------------------------- features


def test_features_endpoint_has_all_required_fields(client, kml_file):
    body = client.get(f"/api/files/{kml_file['id']}/features/").json()
    assert body["crs"] == "EPSG:4326"
    assert body["pagination"] == {"total": 4, "limit": 100, "offset": 0}
    first, *_, last = body["features"]
    assert first["index"] == 0 and first["geometry_type"] == "Polygon"
    assert first["geometry"]["type"] == "Polygon"
    assert first["crs"] == "EPSG:4326"
    assert first["properties"] == {"name": "park"}
    assert last["geometry"] is None and last["geometry_type"] is None


def test_features_filter_and_pagination(client, kml_file):
    base = f"/api/files/{kml_file['id']}/features/"
    only_points = client.get(base, params={"geometry_type": "Point"}).json()
    assert [f["geometry_type"] for f in only_points["features"]] == ["Point"]

    page = client.get(base, params={"limit": 2, "offset": 1}).json()
    assert [f["index"] for f in page["features"]] == [1, 2]
    assert page["pagination"]["total"] == 4

    assert client.get(base, params={"limit": 0}).status_code == 422
    assert client.get(base, params={"limit": 5000}).status_code == 422


# ------------------------------------------------------------- measurements


def test_measurements_endpoint(client, kml_file):
    body = client.get(f"/api/files/{kml_file['id']}/measurements/").json()
    polygon, line, point, label = body["features"]

    assert polygon["geometry_type"] == "Polygon"
    m = polygon["measurement"]
    assert m["status"] == "OK" and m["projected_crs"] == "EPSG:32643"
    assert 980_000 < m["area_sq_m"] < 982_000  # box minus courtyard
    assert m["area_hectares"] == pytest.approx(m["area_sq_m"] / 10_000, abs=1e-3)
    assert "length_m" not in m

    assert line["measurement"]["length_m"] == pytest.approx(1056.0, rel=2e-3)
    assert "area_sq_m" not in line["measurement"]

    assert point["measurement"]["status"] == "NOT_APPLICABLE"
    assert label["measurement"]["status"] == "UNSUPPORTED"
    assert label["geometry_type"] is None

    summary = body["summary"]
    assert summary["total_features"] == 4
    assert summary["by_status"] == {"OK": 2, "NOT_APPLICABLE": 1, "UNSUPPORTED": 1}
    assert summary["by_geometry_type"]["Polygon"] == 1
    assert summary["total_area_sq_m"] == pytest.approx(m["area_sq_m"], abs=0.01)
    assert summary["total_length_m"] == pytest.approx(line["measurement"]["length_m"], abs=0.01)


def test_measurement_summary_ignores_pagination(client, kml_file):
    url = f"/api/files/{kml_file['id']}/measurements/"
    full = client.get(url).json()["summary"]
    paged = client.get(url, params={"limit": 1, "offset": 3}).json()
    assert paged["summary"] == full
    assert len(paged["features"]) == 1


def test_projected_shapefile_and_geographic_kml_agree(client):
    from pyproj import Transformer

    t = Transformer.from_crs(4326, 32643, always_xy=True)
    projected_ring = h.ring([t.transform(x, y) for x, y in h.BOX], clockwise=True)
    utm_prj = (
        'PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
        'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
        'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
        'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
        'PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],'
        'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
    )
    shp = upload(client, "utm.zip", h.shapefile_zip([[projected_ring]], prj=utm_prj)).json()
    kml = upload(client, "geo.kml", h.kml(h.polygon_placemark())).json()
    assert shp["crs"] == "EPSG:32643"

    def area(file):
        body = client.get(f"/api/files/{file['id']}/measurements/").json()
        return body["features"][0]["measurement"]["area_sq_m"]

    assert area(shp) == pytest.approx(area(kml), rel=1e-4)


def test_zip_slip_style_names_are_harmless(client):
    zipped = h.shapefile_zip([[h.ring(h.BOX, clockwise=True)]], stem="../../evil")
    assert upload(client, "slip.zip", zipped).status_code == 201  # read in memory, never extracted


def test_zip_with_nothing_useful_is_422(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no shapefile")
    r = upload(client, "nothing.zip", buf.getvalue())
    assert r.status_code == 422 and ".shp" in r.json()["detail"]
