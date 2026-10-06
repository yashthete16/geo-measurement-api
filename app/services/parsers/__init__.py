from app.config import Settings
from app.models import FileKind
from app.services.parsers.base import ParsedFeature, ParsedFile, ParseError
from app.services.parsers.kml import parse_kml
from app.services.parsers.shapefile_zip import parse_shapefile_zip

__all__ = ["ParseError", "ParsedFeature", "ParsedFile", "parse_geo_file"]


def parse_geo_file(kind: FileKind, content: bytes, settings: Settings) -> ParsedFile:
    if kind == FileKind.KML:
        return parse_kml(content)
    return parse_shapefile_zip(
        content,
        max_uncompressed_bytes=settings.max_uncompressed_bytes,
        max_members=settings.max_zip_members,
    )
