"""``/api/files`` endpoints."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Feature, FileRecord, FileStatus
from app.schemas import (
    FeatureMeasurementOut,
    FeatureOut,
    FeaturesResponse,
    FileList,
    FileOut,
    MeasurementOut,
    MeasurementsResponse,
    MeasurementSummary,
    Pagination,
)
from app.services.processor import UnsupportedFileTypeError, process_upload

router = APIRouter(prefix="/api/files", tags=["files"])

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000

Limit = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Page size")
Offset = Query(0, ge=0, description="Number of items to skip")


def _get_file_or_404(db: Session, file_id: str) -> FileRecord:
    record = db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return record


def _require_completed(record: FileRecord) -> None:
    if record.status == FileStatus.FAILED:
        raise HTTPException(
            status_code=409, detail=f"File processing failed: {record.error_message}"
        )
    if record.status != FileStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="File is still being processed")


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _measurement_out(f: Feature) -> MeasurementOut:
    area = f.area_sq_m
    length = f.length_m
    return MeasurementOut(
        status=f.measurement_status,
        message=f.measurement_message,
        projected_crs=f.projected_crs,
        area_sq_m=_r(area, 3),
        area_hectares=_r(area / 10_000, 6) if area is not None else None,
        area_sq_km=_r(area / 1_000_000, 6) if area is not None else None,
        perimeter_m=_r(f.perimeter_m, 3),
        length_m=_r(length, 3),
        length_km=_r(length / 1000, 6) if length is not None else None,
        geodesic_area_sq_m=_r(f.geodesic_area_sq_m, 3),
        geodesic_length_m=_r(f.geodesic_length_m, 3),
    )


def _feature_query(file_id: str, geometry_type: str | None):
    stmt = select(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        stmt = stmt.where(Feature.geometry_type == geometry_type)
    return stmt


def _count(db: Session, file_id: str, geometry_type: str | None) -> int:
    stmt = select(func.count()).select_from(Feature).where(Feature.file_id == file_id)
    if geometry_type:
        stmt = stmt.where(Feature.geometry_type == geometry_type)
    return db.scalar(stmt) or 0


@router.post(
    "/",
    response_model=FileOut,
    status_code=201,
    summary="Upload a Shapefile (.zip) or KML and measure its features",
    responses={
        400: {"description": "Empty upload"},
        413: {"description": "File exceeds the upload size limit"},
        415: {"description": "Not a .kml or a .zip"},
        422: {"description": "File was received but could not be processed (stored as FAILED)"},
    },
)
def upload_file(
    request: Request,
    file: UploadFile = File(..., description="A .kml file, or a .zip containing a Shapefile"),
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    limit = settings.max_upload_bytes

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 1_048_576:
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit} byte limit")

    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=413, detail=f"File exceeds the {limit} byte limit")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        record = process_upload(db, file.filename or "upload", content, settings)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    out = FileOut.model_validate(record)
    if record.status == FileStatus.FAILED:
        return JSONResponse(
            status_code=422,
            content={"detail": record.error_message, "file": out.model_dump(mode="json")},
        )
    return out


@router.get("/", response_model=FileList, summary="List uploaded files (newest first)")
def list_files(limit: int = Limit, offset: int = Offset, db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(FileRecord)) or 0
    rows = db.scalars(
        select(FileRecord).order_by(FileRecord.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return FileList(
        total=total, limit=limit, offset=offset, files=[FileOut.model_validate(r) for r in rows]
    )


@router.get("/{file_id}/", response_model=FileOut, summary="File information")
def get_file(file_id: str, db: Session = Depends(get_db)):
    return FileOut.model_validate(_get_file_or_404(db, file_id))


@router.delete("/{file_id}/", status_code=204, summary="Delete a file and its features")
def delete_file(file_id: str, db: Session = Depends(get_db)):
    record = _get_file_or_404(db, file_id)
    db.delete(record)
    db.commit()
    return Response(status_code=204)


@router.get(
    "/{file_id}/features/",
    response_model=FeaturesResponse,
    summary="Features: index, geometry type, geometry, CRS and properties",
)
def get_features(
    file_id: str,
    geometry_type: str | None = Query(None, description="e.g. Polygon, LineString, Point"),
    limit: int = Limit,
    offset: int = Offset,
    db: Session = Depends(get_db),
):
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    rows = db.scalars(
        _feature_query(file_id, geometry_type)
        .order_by(Feature.feature_index)
        .limit(limit)
        .offset(offset)
    ).all()
    return FeaturesResponse(
        file_id=file_id,
        crs=record.crs,
        pagination=Pagination(total=_count(db, file_id, geometry_type), limit=limit, offset=offset),
        features=[
            FeatureOut(
                id=f.id,
                index=f.feature_index,
                geometry_type=f.geometry_type,
                geometry=f.geometry,
                crs=f.crs,
                properties=f.properties,
                measurement_status=f.measurement_status,
            )
            for f in rows
        ],
    )


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Area / length per feature, plus file-level totals",
)
def get_measurements(
    file_id: str,
    geometry_type: str | None = Query(None, description="e.g. Polygon, LineString, Point"),
    limit: int = Limit,
    offset: int = Offset,
    db: Session = Depends(get_db),
):
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    rows = db.scalars(
        _feature_query(file_id, geometry_type)
        .order_by(Feature.feature_index)
        .limit(limit)
        .offset(offset)
    ).all()

    return MeasurementsResponse(
        file_id=file_id,
        filename=record.filename,
        crs=record.crs,
        summary=_summary(db, file_id, record.feature_count),
        pagination=Pagination(total=_count(db, file_id, geometry_type), limit=limit, offset=offset),
        features=[
            FeatureMeasurementOut(
                id=f.id,
                index=f.feature_index,
                geometry_type=f.geometry_type,
                measurement=_measurement_out(f),
            )
            for f in rows
        ],
    )


def _summary(db: Session, file_id: str, total: int) -> MeasurementSummary:
    """File-wide totals, computed in SQL so they ignore pagination and filters."""
    by_status = dict(
        db.execute(
            select(Feature.measurement_status, func.count())
            .where(Feature.file_id == file_id)
            .group_by(Feature.measurement_status)
        ).all()
    )
    by_type = dict(
        db.execute(
            select(func.coalesce(Feature.geometry_type, "None"), func.count())
            .where(Feature.file_id == file_id)
            .group_by(Feature.geometry_type)
        ).all()
    )
    area, length = db.execute(
        select(
            func.coalesce(func.sum(Feature.area_sq_m), 0.0),
            func.coalesce(func.sum(Feature.length_m), 0.0),
        ).where(Feature.file_id == file_id)
    ).one()

    return MeasurementSummary(
        total_features=total,
        by_status=by_status,
        by_geometry_type=by_type,
        total_area_sq_m=round(area, 3),
        total_area_hectares=round(area / 10_000, 6),
        total_area_sq_km=round(area / 1_000_000, 6),
        total_length_m=round(length, 3),
        total_length_km=round(length / 1000, 6),
    )
