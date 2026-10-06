"""Orchestrates one upload: detect type -> parse -> measure -> persist.

All parsing and measuring happens *before* the database is touched, so a failure can never
leave half-written features behind.
"""

import logging
import uuid
from pathlib import PurePosixPath

from shapely.geometry import mapping
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Feature, FileKind, FileRecord, FileStatus, MeasurementStatus
from app.services.crs import crs_label
from app.services.measurement import MeasurementEngine, MeasurementResult
from app.services.parsers import ParsedFile, ParseError, parse_geo_file

logger = logging.getLogger(__name__)


class UnsupportedFileTypeError(ValueError):
    """The upload is neither a .kml nor a .zip."""


def detect_kind(filename: str, content: bytes) -> FileKind:
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix == ".kml":
        return FileKind.KML
    if suffix == ".zip":
        if not content.startswith(b"PK"):
            raise UnsupportedFileTypeError("File has a .zip extension but is not a zip archive")
        return FileKind.SHAPEFILE
    raise UnsupportedFileTypeError(
        "Unsupported file type. Upload a .kml file, or a .zip containing a Shapefile "
        "(.shp/.shx/.dbf/.prj)."
    )


def _build_features(file_id: str, parsed: ParsedFile) -> tuple[list[Feature], list[str]]:
    engine = MeasurementEngine()
    label = crs_label(parsed.crs)
    rows: list[Feature] = []
    parse_errors = 0

    for pf in parsed.features:
        if pf.error:
            parse_errors += 1
            result = MeasurementResult.of(
                MeasurementStatus.ERROR, f"Could not read geometry: {pf.error}"
            )
        else:
            result = engine.measure(pf.geometry, parsed.crs)

        geom = pf.geometry
        rows.append(
            Feature(
                file_id=file_id,
                feature_index=pf.index,
                geometry_type=geom.geom_type if geom is not None else None,
                geometry=mapping(geom) if geom is not None else None,
                crs=label,
                properties=pf.properties,
                measurement_status=result.status.value,
                measurement_message=result.message,
                projected_crs=result.projected_crs,
                area_sq_m=result.area_sq_m,
                perimeter_m=result.perimeter_m,
                length_m=result.length_m,
                geodesic_area_sq_m=result.geodesic_area_sq_m,
                geodesic_length_m=result.geodesic_length_m,
            )
        )

    warnings = list(parsed.warnings)
    if parse_errors:
        warnings.append(f"{parse_errors} feature(s) had unreadable geometry and were not measured")
    return rows, warnings


def process_upload(db: Session, filename: str, content: bytes, settings: Settings) -> FileRecord:
    """Parse + measure ``content`` and persist the result. Returns the stored record.

    Raises :class:`UnsupportedFileTypeError` for wrong file types (nothing is stored).
    Unreadable files are stored with ``status=FAILED`` and an ``error_message``.
    """
    kind = detect_kind(filename, content)
    file_id = uuid.uuid4().hex

    features: list[Feature] = []
    warnings: list[str] = []
    crs: str | None = None
    error: str | None = None

    try:
        parsed = parse_geo_file(kind, content, settings)
        crs = crs_label(parsed.crs)
        features, warnings = _build_features(file_id, parsed)
    except ParseError as exc:
        error = str(exc)
    except Exception:  # noqa: BLE001 - never leak internals to the client
        logger.exception("Unexpected failure while processing %s", filename)
        error = "Unexpected error while processing the file"

    record = FileRecord(
        id=file_id,
        filename=PurePosixPath(filename.replace("\\", "/")).name[:255],
        file_type=kind.value,
        status=(FileStatus.FAILED if error else FileStatus.COMPLETED).value,
        crs=crs,
        feature_count=len(features),
        size_bytes=len(content),
        error_message=error,
        warnings=warnings,
    )
    db.add(record)
    db.add_all(features)
    db.commit()
    db.refresh(record)
    return record
