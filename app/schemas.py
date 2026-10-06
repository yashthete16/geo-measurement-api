"""Pydantic response models."""

from datetime import UTC, datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None
    status: str
    error_message: str | None = None
    warnings: list[str] = []
    size_bytes: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops tzinfo; everything we store is UTC.
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class FileList(BaseModel):
    total: int
    limit: int
    offset: int
    files: list[FileOut]


class Pagination(BaseModel):
    total: int
    limit: int
    offset: int


class MeasurementOut(BaseModel):
    """Only the fields relevant to the geometry are populated (``None`` fields are omitted)."""

    @model_serializer(mode="wrap")
    def _omit_none(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}

    status: str
    message: str | None = None
    projected_crs: str | None = None
    area_sq_m: float | None = None
    area_hectares: float | None = None
    area_sq_km: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    length_km: float | None = None
    geodesic_area_sq_m: float | None = None
    geodesic_length_m: float | None = None


class FeatureMeasurementOut(BaseModel):
    id: int
    index: int
    geometry_type: str | None
    measurement: MeasurementOut


class MeasurementSummary(BaseModel):
    total_features: int
    by_status: dict[str, int]
    by_geometry_type: dict[str, int]
    total_area_sq_m: float
    total_area_hectares: float
    total_area_sq_km: float
    total_length_m: float
    total_length_km: float


class MeasurementsResponse(BaseModel):
    file_id: str
    filename: str
    crs: str | None
    summary: MeasurementSummary
    pagination: Pagination
    features: list[FeatureMeasurementOut]


class FeatureOut(BaseModel):
    id: int
    index: int
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str | None
    properties: dict[str, Any]
    measurement_status: str


class FeaturesResponse(BaseModel):
    file_id: str
    crs: str | None
    pagination: Pagination
    features: list[FeatureOut]
