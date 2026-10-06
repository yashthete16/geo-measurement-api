"""Generate the sample files in ``sample_data/`` (re-runnable).

    python scripts/make_sample_data.py

Everything is placed around Pune, India (≈ 18.52°N, 73.85°E, UTM zone 43N).
"""

import datetime as dt
import io
import zipfile
from pathlib import Path

import shapefile
from pyproj import Transformer

OUT = Path(__file__).resolve().parent.parent / "sample_data"

PRJ_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)
PRJ_UTM43N = (
    'PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],'
    'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
)

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Pune survey</name>
    <Folder>
      <name>Parcels</name>
      <Placemark>
        <name>Park boundary</name>
        <description>Public park, outer boundary only</description>
        <ExtendedData>
          <Data name="owner"><value>PMC</value></Data>
          <Data name="zone"><value>A</value></Data>
        </ExtendedData>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            73.8500,18.5000,0 73.8600,18.5000,0 73.8600,18.5100,0 73.8500,18.5100,0 73.8500,18.5000,0
          </coordinates></LinearRing></outerBoundaryIs>
        </Polygon>
      </Placemark>
      <Placemark>
        <name>Plot with courtyard (hole)</name>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            73.8700,18.5200 73.8800,18.5200 73.8800,18.5300 73.8700,18.5300 73.8700,18.5200
          </coordinates></LinearRing></outerBoundaryIs>
          <innerBoundaryIs><LinearRing><coordinates>
            73.8730,18.5230 73.8770,18.5230 73.8770,18.5270 73.8730,18.5270 73.8730,18.5230
          </coordinates></LinearRing></innerBoundaryIs>
        </Polygon>
      </Placemark>
    </Folder>
    <Folder>
      <name>Network</name>
      <Placemark>
        <name>Main road</name>
        <ExtendedData><SchemaData schemaUrl="#roads">
          <SimpleData name="class">arterial</SimpleData>
        </SchemaData></ExtendedData>
        <LineString><coordinates>
          73.8500,18.5000 73.8550,18.5050 73.8600,18.5100 73.8650,18.5150
        </coordinates></LineString>
      </Placemark>
      <Placemark>
        <name>Survey benchmark</name>
        <Point><coordinates>73.8550,18.5050,560</coordinates></Point>
      </Placemark>
    </Folder>
    <Placemark>
      <name>Mixed collection (polygon + point)</name>
      <MultiGeometry>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
          73.9000,18.5500 73.9050,18.5500 73.9050,18.5550 73.9000,18.5500
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
        <Point><coordinates>73.9100,18.5600</coordinates></Point>
      </MultiGeometry>
    </Placemark>
    <Placemark>
      <name>Label only (no geometry)</name>
    </Placemark>
  </Document>
</kml>
"""


def _clockwise(ring):
    """Shapefile spec: exterior rings are clockwise, holes counter-clockwise."""
    area2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False))
    return ring if area2 < 0 else ring[::-1]


def _closed(ring):
    return ring if ring[0] == ring[-1] else [*ring, ring[0]]


def _build_shapefile_zip(path: Path, prj: str, polygons, lines, to_xy=lambda lon, lat: (lon, lat)):
    """Write a zipped polygon shapefile (``polygons``) -- or a polyline one if ``lines``."""
    parts = {k: io.BytesIO() for k in ("shp", "shx", "dbf")}
    kind = shapefile.POLYLINE if lines else shapefile.POLYGON
    with shapefile.Writer(**parts, shapeType=kind) as w:
        w.field("NAME", "C", 40)
        w.field("LANDUSE", "C", 20)
        w.field("SURVEYED", "D")
        if lines:
            for name, use, coords in lines:
                w.line([[list(to_xy(*c)) for c in coords]])
                w.record(name, use, dt.date(2024, 3, 15))
        else:
            for name, use, outer, holes in polygons:
                rings = [_clockwise(_closed([to_xy(*c) for c in outer]))]
                rings += [_clockwise(_closed([to_xy(*c) for c in h]))[::-1] for h in holes]
                w.poly([[list(p) for p in r] for r in rings])
                w.record(name, use, dt.date(2024, 3, 15))

    stem = path.stem
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{stem}.shp", parts["shp"].getvalue())
        zf.writestr(f"{stem}.shx", parts["shx"].getvalue())
        zf.writestr(f"{stem}.dbf", parts["dbf"].getvalue())
        zf.writestr(f"{stem}.prj", prj)
        zf.writestr(f"{stem}.cpg", "UTF-8")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / "survey.kml").write_text(KML, encoding="utf-8")

    polygons = [
        (
            "Parcel 1",
            "residential",
            [(73.85, 18.50), (73.86, 18.50), (73.86, 18.51), (73.85, 18.51)],
            [],
        ),
        (
            "Parcel 2 (with courtyard)",
            "commercial",
            [(73.87, 18.52), (73.88, 18.52), (73.88, 18.53), (73.87, 18.53)],
            [[(73.873, 18.523), (73.877, 18.523), (73.877, 18.527), (73.873, 18.527)]],
        ),
    ]
    # Geographic shapefile (EPSG:4326)
    _build_shapefile_zip(OUT / "parcels_wgs84.zip", PRJ_WGS84, polygons, None)

    # Same parcels stored in projected metres (EPSG:32643) -> proves CRS handling end to end.
    t = Transformer.from_crs(4326, 32643, always_xy=True)
    _build_shapefile_zip(OUT / "parcels_utm43n.zip", PRJ_UTM43N, polygons, None, to_xy=t.transform)

    roads = [
        ("Main road", "arterial", [(73.85, 18.50), (73.855, 18.505), (73.86, 18.51)]),
        ("Ring road", "highway", [(73.80, 18.45), (73.90, 18.45)]),
    ]
    _build_shapefile_zip(OUT / "roads_wgs84.zip", PRJ_WGS84, None, roads)
    print("Sample data written to", OUT)


if __name__ == "__main__":
    main()
