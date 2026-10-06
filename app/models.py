"""ORM models: one ``files`` row per upload, one ``features`` row per feature inside it."""

import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FileKind(enum.StrEnum):
    KML = "kml"
    SHAPEFILE = "shapefile"


class FileStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(enum.StrEnum):
    OK = "OK"  # a measurement was calculated
    NOT_APPLICABLE = "NOT_APPLICABLE"  # e.g. points: nothing to measure
    UNSUPPORTED = "UNSUPPORTED"  # geometry type / empty geometry we deliberately skip
    ERROR = "ERROR"  # something went wrong for this one feature


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    crs: Mapped[str | None] = mapped_column(String(255))
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", passive_deletes=True, order_by="Feature.feature_index"
    )


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (Index("ix_features_file_index", "file_id", "feature_index", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    feature_index: Mapped[int] = mapped_column(Integer)  # 0-based position in the source file

    geometry_type: Mapped[str | None] = mapped_column(String(32), index=True)
    geometry: Mapped[dict | None] = mapped_column(JSON)  # GeoJSON geometry, source CRS, 2D
    crs: Mapped[str | None] = mapped_column(String(255))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)

    measurement_status: Mapped[str] = mapped_column(String(16))
    measurement_message: Mapped[str | None] = mapped_column(Text)
    projected_crs: Mapped[str | None] = mapped_column(String(32))
    area_sq_m: Mapped[float | None] = mapped_column(Float)
    perimeter_m: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    # Independent ellipsoidal (geodesic) result, kept as an accuracy cross-check.
    geodesic_area_sq_m: Mapped[float | None] = mapped_column(Float)
    geodesic_length_m: Mapped[float | None] = mapped_column(Float)

    file: Mapped[FileRecord] = relationship(back_populates="features")
