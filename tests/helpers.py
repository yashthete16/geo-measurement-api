"""Builders for in-memory test fixtures."""

import io
import zipfile

import shapefile

PRJ_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

# 0.01° x 0.01° box near Pune
BOX = [(73.85, 18.50), (73.86, 18.50), (73.86, 18.51), (73.85, 18.51)]
HOLE = [(73.853, 18.503), (73.857, 18.503), (73.857, 18.507), (73.853, 18.507)]


def _signed_area2(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False))


def ring(points, *, clockwise: bool):
    pts = [*points, points[0]]
    return pts if (_signed_area2(pts) < 0) == clockwise else pts[::-1]


def kml(*placemarks: str, ns: str = "http://www.opengis.net/kml/2.2") -> bytes:
    body = "\n".join(placemarks)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<kml xmlns="{ns}"><Document><Folder>{body}</Folder></Document></kml>'
    ).encode()


def coords(points) -> str:
    return " ".join(f"{x},{y}" for x, y in [*points, points[0]])


def polygon_placemark(name="poly", outer=BOX, hole=None) -> str:
    inner = (
        f"<innerBoundaryIs><LinearRing><coordinates>{coords(hole)}</coordinates>"
        "</LinearRing></innerBoundaryIs>"
        if hole
        else ""
    )
    return (
        f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{coords(outer)}</coordinates></LinearRing></outerBoundaryIs>{inner}"
        "</Polygon></Placemark>"
    )


def line_placemark(name="line", pts=((73.85, 18.5), (73.86, 18.5))) -> str:
    c = " ".join(f"{x},{y}" for x, y in pts)
    return f"<Placemark><name>{name}</name><LineString><coordinates>{c}</coordinates></LineString></Placemark>"


def point_placemark(name="pt", x=73.85, y=18.5) -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{x},{y},10</coordinates></Point></Placemark>"


def shapefile_zip(
    shapes,
    *,
    shape_type=shapefile.POLYGON,
    prj: str | None = PRJ_WGS84,
    stem="data",
    with_dbf=True,
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    """``shapes``: list of lists-of-parts (each part a list of (x, y))."""
    parts = {k: io.BytesIO() for k in ("shp", "shx", "dbf")}
    with shapefile.Writer(**parts, shapeType=shape_type) as w:
        w.field("NAME", "C", 30)
        w.field("N", "N", 10, 0)
        for i, part_list in enumerate(shapes):
            if shape_type == shapefile.POLYGON:
                w.poly(part_list)
            elif shape_type == shapefile.POLYLINE:
                w.line(part_list)
            else:
                w.point(*part_list[0][0])
            w.record(f"feature {i}", i)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{stem}.shp", parts["shp"].getvalue())
        zf.writestr(f"{stem}.shx", parts["shx"].getvalue())
        if with_dbf:
            zf.writestr(f"{stem}.dbf", parts["dbf"].getvalue())
        if prj is not None:
            zf.writestr(f"{stem}.prj", prj)
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()
