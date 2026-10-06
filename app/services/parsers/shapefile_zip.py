"""Zipped-Shapefile parser built on ``pyshp`` (pure Python, no GDAL).

The zip is never extracted to disk: members are read into memory under a hard byte budget,
which removes path-traversal ("zip slip") risk and bounds zip-bomb damage.
"""

import codecs
import io
import posixpath
import zipfile
from typing import Any

import shapefile
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape as shapely_shape

from app.services.crs import WGS84, looks_like_lonlat, normalise_horizontal
from app.services.parsers.base import ParsedFeature, ParsedFile, ParseError, to_jsonable

_COMPANION_EXTENSIONS = (".shx", ".dbf", ".prj", ".cpg")


def _read_members(content: bytes, max_total: int, max_members: int) -> dict[str, bytes]:
    """Read every real file in the zip into memory, enforcing size/count limits."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ParseError("Upload is not a valid .zip archive") from exc

    members: dict[str, bytes] = {}
    budget = max_total
    with zf:
        infos = [
            i
            for i in zf.infolist()
            if not i.is_dir()
            and not i.filename.startswith("__MACOSX/")
            and not posixpath.basename(i.filename).startswith("._")
        ]
        if len(infos) > max_members:
            raise ParseError(f"Zip contains too many files ({len(infos)} > {max_members})")

        for info in infos:
            try:
                with zf.open(info) as fh:
                    # Read budget+1 so an overrun is detectable even if the header lies.
                    data = fh.read(budget + 1)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
                raise ParseError(f"Could not read {info.filename!r} from the zip: {exc}") from exc
            if len(data) > budget:
                raise ParseError("Zip expands beyond the allowed uncompressed size")
            budget -= len(data)
            members[info.filename] = data
    return members


def _find_companion(members: dict[str, bytes], stem: str, ext: str) -> bytes | None:
    for name, data in members.items():
        if name.lower() == f"{stem}{ext}":
            return data
    return None


def _encoding_from_cpg(cpg: bytes | None) -> str:
    if not cpg:
        return "utf-8"
    label = cpg.decode("ascii", errors="ignore").strip()
    if label.isdigit():
        label = f"cp{label}"
    try:
        return codecs.lookup(label).name
    except LookupError:
        return "utf-8"


def _drop_extra_dims(coords: Any) -> Any:
    """Strip Z/M so every coordinate is (x, y)."""
    if coords and isinstance(coords[0], int | float):
        return tuple(coords[:2])
    return [_drop_extra_dims(c) for c in coords]


def _to_geometry(shp: shapefile.Shape):
    if shp.shapeType == shapefile.NULL:
        return None
    geo = dict(shp.__geo_interface__)
    geo["coordinates"] = _drop_extra_dims(geo["coordinates"])
    return shapely_shape(geo)


def parse_shapefile_zip(
    content: bytes, *, max_uncompressed_bytes: int, max_members: int
) -> ParsedFile:
    members = _read_members(content, max_uncompressed_bytes, max_members)

    shp_names = sorted(n for n in members if n.lower().endswith(".shp"))
    if not shp_names:
        raise ParseError("No .shp file found inside the zip")
    if len(shp_names) > 1:
        raise ParseError(
            f"Zip must contain exactly one shapefile, found {len(shp_names)}: "
            + ", ".join(shp_names)
        )

    shp_name = shp_names[0]
    stem = shp_name[:-4].lower()
    shx, dbf, prj, cpg = (_find_companion(members, stem, e) for e in _COMPANION_EXTENSIONS)

    warnings: list[str] = []
    if dbf is None:
        warnings.append("No .dbf found: features have no attributes")

    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(members[shp_name]),
            shx=io.BytesIO(shx) if shx else None,
            dbf=io.BytesIO(dbf) if dbf else None,
            encoding=_encoding_from_cpg(cpg),
            encodingErrors="replace",
        )
    except Exception as exc:  # noqa: BLE001 - pyshp raises assorted types on corrupt input
        raise ParseError(f"Could not read the shapefile: {exc}") from exc

    with reader:
        crs = _resolve_crs(prj, reader, warnings)

        field_names = [f[0] for f in reader.fields[1:]] if dbf else []
        features: list[ParsedFeature] = []
        try:
            iterator = (
                ((sr.shape, sr.record.as_dict()) for sr in reader.iterShapeRecords())
                if dbf
                else ((s, {}) for s in reader.iterShapes())
            )
            for index, (shp, record) in enumerate(iterator):
                props = {k: to_jsonable(record.get(k)) for k in field_names}
                try:
                    features.append(ParsedFeature(index, _to_geometry(shp), props))
                except Exception as exc:  # noqa: BLE001 - one bad shape must not sink the file
                    features.append(ParsedFeature(index, None, props, error=str(exc)))
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"Shapefile is corrupt or truncated: {exc}") from exc

    if not features:
        raise ParseError("The shapefile contains no features")

    return ParsedFile(features=features, crs=crs, warnings=warnings)


def _resolve_crs(prj: bytes | None, reader: shapefile.Reader, warnings: list[str]) -> CRS:
    if prj:
        try:
            crs = CRS.from_wkt(prj.decode("utf-8", errors="replace").strip())
            return normalise_horizontal(crs)
        except (CRSError, ValueError) as exc:
            raise ParseError(f"Could not interpret the .prj coordinate system: {exc}") from exc

    bbox = reader.bbox
    if bbox is not None and looks_like_lonlat(bbox[0], bbox[1], bbox[2], bbox[3]):
        warnings.append(
            "No .prj found; coordinates look like longitude/latitude, assuming EPSG:4326"
        )
        return WGS84
    raise ParseError(
        "No .prj file and the coordinates do not look like longitude/latitude, "
        "so the coordinate system cannot be determined. Add the .prj file to the zip."
    )
