# Geospatial File Measurement API

A FastAPI backend that accepts a **KML** file or a **zipped Shapefile**, extracts every feature,
and returns **area** for polygons and **length** for lines.

The part that matters most: coordinates in degrees are **never** measured directly. Each feature is
transformed to a metric projected CRS (the UTM zone it sits in) before any area or length is
calculated, and every result is cross-checked against an independent ellipsoidal (geodesic)
calculation.

- Pure-Python geospatial stack (`shapely`, `pyproj`, `pyshp`, `defusedxml`) — **no GDAL install needed**
- Interactive docs at `/docs` (Swagger UI) and `/redoc`
- 71 automated tests, lint-clean, CI workflow and Dockerfile included

---

## Setup

Requires Python **3.11+**.

```bash
git clone <this-repo-url> && cd geo-measurement-api

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload
```

The API is now on <http://127.0.0.1:8000> and the interactive docs on <http://127.0.0.1:8000/docs>.
The SQLite database is created automatically at `./data/geo.db`.

Try it with the bundled sample files:

```bash
curl -X POST http://127.0.0.1:8000/api/files/ -F "file=@sample_data/survey.kml"
curl -X POST http://127.0.0.1:8000/api/files/ -F "file=@sample_data/parcels_utm43n.zip"
```

| Sample file | What it demonstrates |
|---|---|
| `sample_data/survey.kml` | Polygon, polygon with a hole, line, point, a mixed `MultiGeometry` (unsupported → handled gracefully), a placemark with no geometry |
| `sample_data/parcels_wgs84.zip` | Shapefile in EPSG:4326 |
| `sample_data/parcels_utm43n.zip` | The *same* parcels in projected metres (EPSG:32643) — yields identical areas |
| `sample_data/roads_wgs84.zip` | Polyline shapefile |

(Regenerate them with `python scripts/make_sample_data.py`.)

### Run the tests

```bash
pytest            # 71 tests, ~1 s
ruff check . && ruff format --check .
```

### Docker

```bash
docker build -t geo-measurement-api .
docker run -p 8000:8000 -v geo-data:/app/data geo-measurement-api
```

### Configuration

All settings are optional environment variables (or a `.env` file):

| Variable | Default | Meaning |
|---|---|---|
| `GEO_DATABASE_URL` | `sqlite:///./data/geo.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `GEO_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Reject larger uploads with `413` |
| `GEO_MAX_UNCOMPRESSED_BYTES` | `262144000` (250 MB) | Zip-bomb guard: max total size a zip may expand to |
| `GEO_MAX_ZIP_MEMBERS` | `200` | Max files inside a zip |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a `.kml` or a `.zip` (Shapefile) |
| `GET` | `/api/files/{id}/` | File information |
| `GET` | `/api/files/{id}/measurements/` | Per-feature measurements + file-level totals |
| `GET` | `/api/files/{id}/features/` | Per-feature index, geometry type, geometry, CRS, properties |
| `GET` | `/api/files/` | List uploaded files |
| `DELETE` | `/api/files/{id}/` | Delete a file and its features |
| `GET` | `/health` | Liveness probe |

`measurements` and `features` accept `limit` (1–1000, default 100), `offset`, and
`geometry_type` (e.g. `Polygon`) query parameters.

### `POST /api/files/`

```bash
curl -X POST http://127.0.0.1:8000/api/files/ -F "file=@sample_data/survey.kml"
```

```json
{
  "id": "7d5ddd15e25c429f9bcd53b90f9fa16d",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 6,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": null,
  "warnings": [],
  "size_bytes": 2299,
  "created_at": "2026-10-06T18:39:30.413613Z"
}
```

| Status | When |
|---|---|
| `201` | File processed (`status: COMPLETED`) |
| `400` | Empty upload |
| `413` | Larger than `GEO_MAX_UPLOAD_BYTES` |
| `415` | Not a `.kml` / `.zip` (or a `.zip` that isn't actually a zip) |
| `422` | Received but unusable: malformed XML, zip with no `.shp`, unknown CRS, … The attempt is stored with `status: FAILED` and the response includes the reason and the file record |

```json
{
  "detail": "Invalid KML (not well-formed XML): no element found: line 1, column 11",
  "file": { "id": "a8c6f7dd…", "status": "FAILED", "error_message": "Invalid KML …", "…": "…" }
}
```

### `GET /api/files/{id}/`

```json
{
  "id": "7d5ddd15e25c429f9bcd53b90f9fa16d",
  "filename": "survey.kml",
  "file_type": "kml",
  "feature_count": 6,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error_message": null,
  "warnings": [],
  "size_bytes": 2299,
  "created_at": "2026-10-06T18:39:30.413613Z"
}
```

`warnings` carries non-fatal notes, e.g. *"No .prj found; coordinates look like longitude/latitude,
assuming EPSG:4326"*. `404` if the id is unknown.

### `GET /api/files/{id}/measurements/`

```bash
curl "http://127.0.0.1:8000/api/files/7d5ddd15e25c429f9bcd53b90f9fa16d/measurements/?limit=3"
```

```json
{
  "file_id": "7d5ddd15e25c429f9bcd53b90f9fa16d",
  "filename": "survey.kml",
  "crs": "EPSG:4326",
  "summary": {
    "total_features": 6,
    "by_status": { "NOT_APPLICABLE": 1, "OK": 3, "UNSUPPORTED": 2 },
    "by_geometry_type": { "None": 1, "GeometryCollection": 1, "LineString": 1, "Point": 1, "Polygon": 2 },
    "total_area_sq_m": 2149598.139,
    "total_area_hectares": 214.959814,
    "total_area_sq_km": 2.149598,
    "total_length_m": 2294.169,
    "total_length_km": 2.294169
  },
  "pagination": { "total": 6, "limit": 3, "offset": 0 },
  "features": [
    {
      "id": 1,
      "index": 0,
      "geometry_type": "Polygon",
      "measurement": {
        "status": "OK",
        "projected_crs": "EPSG:32643",
        "area_sq_m": 1168327.418,
        "area_hectares": 116.832742,
        "area_sq_km": 1.168327,
        "perimeter_m": 4324.764,
        "geodesic_area_sq_m": 1168840.234
      }
    },
    {
      "id": 3,
      "index": 2,
      "geometry_type": "LineString",
      "measurement": {
        "status": "OK",
        "projected_crs": "EPSG:32643",
        "length_m": 2294.169,
        "length_km": 2.294169,
        "geodesic_length_m": 2294.675
      }
    }
  ]
}
```

(Trimmed to two of the three returned features.) The `summary` always covers the **whole file**,
independent of pagination and filters.

Every feature has a measurement `status`:

| Status | Meaning |
|---|---|
| `OK` | Area (polygons) or length (lines) calculated |
| `NOT_APPLICABLE` | Points — nothing to measure |
| `UNSUPPORTED` | Geometry type we deliberately don't measure (`GeometryCollection`), empty geometry, or a placemark with no geometry. A `message` explains |
| `ERROR` | This one feature failed (unreadable coordinates, out-of-range values…). The rest of the file is unaffected |

`geodesic_*` is an independent ellipsoidal cross-check. `message` may also contain notes, such as
*"Self-intersecting polygon was repaired before measuring"*.
Fields that don't apply to a geometry are omitted rather than returned as `null`.

### `GET /api/files/{id}/features/`

Returns the extracted features exactly as stored: 0-based `index`, `geometry_type`, `geometry`
(GeoJSON, in the file's own CRS), `crs` and `properties` (KML `name`/`description`/`ExtendedData`, or
the Shapefile's DBF attributes).

```json
{
  "file_id": "7d5ddd15…",
  "crs": "EPSG:4326",
  "pagination": { "total": 6, "limit": 1, "offset": 0 },
  "features": [
    {
      "id": 1,
      "index": 0,
      "geometry_type": "Polygon",
      "geometry": { "type": "Polygon", "coordinates": [[[73.85, 18.5], [73.86, 18.5], [73.86, 18.51], [73.85, 18.51], [73.85, 18.5]]] },
      "crs": "EPSG:4326",
      "properties": { "name": "Park boundary", "description": "Public park, outer boundary only", "owner": "PMC", "zone": "A" },
      "measurement_status": "OK"
    }
  ]
}
```

Measurements for a `FAILED` file return `409`.

---

## Architecture

### Structure

```
app/
├── main.py                  # app factory, lifespan (creates tables), /health
├── config.py                # env-driven settings
├── database.py              # engine + per-request session dependency
├── models.py                # FileRecord, Feature (+ status enums)
├── schemas.py               # Pydantic response models
├── api/files.py             # HTTP layer: validation, status codes, pagination, summaries
└── services/
    ├── processor.py         # orchestration: detect → parse → measure → persist
    ├── crs.py               # CRS labelling, UTM zone selection
    ├── measurement.py       # MeasurementEngine (projection + area/length + geodesic check)
    └── parsers/
        ├── base.py          # ParsedFile / ParsedFeature / ParseError
        ├── kml.py           # KML → shapely
        └── shapefile_zip.py # zipped Shapefile → shapely
tests/                       # 71 tests: CRS maths, parsers, API behaviour
scripts/make_sample_data.py  # regenerates sample_data/
```

The layers only point downwards: routes know about services, services know nothing about HTTP.
Parsers return plain `shapely` geometries, so the measurement code is format-agnostic, and adding a
format (GeoJSON, KMZ, …) means adding one parser.

### File-processing flow

```
POST /api/files/
   │  size / empty checks (400, 413)
   ▼
detect_kind()            .kml → KML │ .zip → Shapefile │ else 415
   ▼
parser                   KML:  defusedxml → Placemarks → shapely geometries + properties
                         ZIP:  members read in memory under a byte budget → pyshp → geometries + DBF attrs
                               CRS from .prj (or assumed EPSG:4326 *only* if no .prj and coords look like lon/lat)
   ▼
MeasurementEngine        one result per feature (see below)
   ▼
persist                  1 files row + N features rows, one transaction, only after everything succeeded
   ▼
201 + file info          (unreadable file → stored as FAILED, 422 with reason)
```

Nothing touches the database until parsing and measuring are finished, so a failure can never leave
half-written features behind. Failures are handled at two levels: a **bad file** fails as a whole
(`422`, stored as `FAILED`), while a **bad feature** only gets its own `ERROR` status.

### Measurement-calculation flow

For each feature:

1. **Classify.** `Polygon`/`MultiPolygon` → area, `LineString`/`MultiLineString` → length,
   `Point`/`MultiPoint` → `NOT_APPLICABLE`, anything else or empty → `UNSUPPORTED`.
2. **Normalise to WGS84** lon/lat (no-op for EPSG:4326) and sanity-check the ranges.
3. **Repair** self-intersecting polygons with `make_valid`, keep only the area-bearing parts, add a note.
4. **Pick the UTM zone** from the centre of the feature's bounding box and project to it.
5. **Measure** `area` / `length` / `perimeter` in the projected CRS (metres).
6. **Cross-check** with a geodesic calculation on the WGS84 ellipsoid; if the two disagree by more than
   0.5 % a note recommends the geodesic figure (large features far from a zone centre).

### CRS handling

- **KML** is always WGS84 by specification.
- **Shapefile** CRS is read from the `.prj`. Any geographic or projected CRS works; compound CRSs use
  their horizontal part. The label reported is `EPSG:xxxx` when identifiable, otherwise the CRS name.
- Source CRS is **not trusted for measuring**. A file in Web Mercator (EPSG:3857) would otherwise
  inflate areas at Pune's latitude by ~12 %, so every feature is routed through WGS84 into its own UTM
  zone. There's a test for exactly this.
- **UTM zone** = `floor((lon + 180) / 6) + 1`, EPSG:326xx (north) / 327xx (south); UPS (EPSG:32661 /
  32761) beyond 84°N / 80°S.
- Zones are chosen **per feature**, so one file can legitimately span several zones.

Sanity check on the sample data: the 0.01° × 0.01° parcel near Pune measures 1,168,327 m² in UTM 43N
vs 1,168,840 m² geodesically (0.04 % apart). Measured in raw degrees it would "be" 0.0001.

---

## Design decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| **FastAPI** | Typed request/response models, automatic OpenAPI docs, small surface | Django + DRF: heavier than needed for a stateless processing API (but would give admin/auth for free) |
| **Pure-Python geo stack** (`shapely` + `pyproj` + `pyshp` + own KML parser) | `pip install` works everywhere; no GDAL/system libraries; easy Docker image | `geopandas`/`fiona`/GDAL: more formats for free, but notoriously painful to install; much heavier |
| **UTM zone per feature** as the projected CRS | Metric, conformal, <0.1 % distortion inside a zone, trivially derived from coordinates, easy to explain and verify | Local equal-area projection per feature (exact area, but less familiar and harder to cross-check); a single fixed CRS (breaks for global data); pure geodesic (more accurate but doesn't satisfy "project first") |
| **Always go via WGS84 → UTM**, even if the file is already projected | A file's CRS may be Web Mercator or a distorted grid; normalising makes results independent of the file's projection | Measuring directly in the source CRS when it's metric: faster, but silently wrong for 3857 |
| **Geodesic cross-check** stored with each result | Independent evidence the projection step is right; flags large features automatically | Omit it: less data, but no way to detect distortion |
| **Own ring-by-ring geodesic area** | `pyproj.Geod.geometry_area_perimeter` *adds* interior rings instead of subtracting them (verified on pyproj 3.8) | Using the helper directly: wrong for any polygon with a hole |
| **Synchronous processing in the request**, with a `status` field | Simple and deterministic for the file sizes expected (50 MB cap); API shape already supports async | Celery/RQ background jobs: the right call for very large files, see Future scope |
| **SQLite by default, SQLAlchemy** | Zero setup; portable (JSON columns work on PostgreSQL too) | PostgreSQL + PostGIS: better for spatial queries, but a heavy requirement for a reviewer to run |
| **Features stored as rows** (geometry as GeoJSON JSON) | Cheap pagination, filtering and SQL aggregates for totals | One JSON blob per file: simpler but no pagination or aggregates |
| **Bad file → stored as `FAILED` + `422`**; **bad feature → `ERROR` on that feature** | Clients can always look the upload up later; one corrupt placemark never loses a whole survey | Fail fast and store nothing |
| **Zip read in memory under a byte budget** | No path-traversal ("zip slip") or temp-file cleanup issues; bounds zip-bomb impact even if headers lie | `extractall` to a temp dir |
| **`defusedxml`** for KML | Blocks entity-expansion ("billion laughs") attacks | stdlib `xml.etree`: vulnerable by default |
| **Exactly one shapefile per zip** | Unambiguous response shape; clear error otherwise | Process every layer and tag features by layer |
| **Z/M values dropped** | Area and length here are planar/2D; also avoids 4-D coordinate surprises from DBF-style M values | Keep Z for storage: possible, but then geometry and measurement disagree about dimensionality |
| **Mixed `GeometryCollection` → `UNSUPPORTED`** | "Area of a polygon + length of a line" has no single meaningful measurement | Summing parts: possible future option |

### Assumptions and known limitations

- A Shapefile with no `.prj` is accepted only when its coordinates fit within lon/lat bounds
  (assumed EPSG:4326, with a warning in the response); otherwise it's rejected rather than guessed.
- Polygon/hole grouping relies on `pyshp` and the Shapefile spec's ring orientation (exterior =
  clockwise). Files that violate the spec can produce odd polygons; they're then caught by the
  validity check and repaired or flagged.
- Features crossing the antimeridian (±180°) are not specially handled.
- UTM scale distortion grows for features hundreds of kilometres wide; the geodesic figure and the
  automatic divergence note exist for that case.
- Uploaded files themselves are not retained, only the parsed features.
- No authentication or rate limiting. Both belong in front of this service (gateway/middleware).
- A reverse proxy should also enforce the upload limit, since the framework buffers the multipart
  body before the endpoint sees it.

---

## Learnings

- **Check helper functions on the edge cases.** `pyproj`'s geodesic polygon helper silently *adds*
  holes; a test with a courtyard polygon caught it. Cross-checking two independent calculation paths
  turned out to be the best test strategy for geospatial code, because plausible-looking numbers are
  easy to get wrong.
- **CRS bugs hide in "reasonable" numbers.** Measuring in EPSG:3857 produces areas that are
  plausible but ~12 % too big at this latitude. The only defence is making CRS handling explicit and
  writing a test that would fail if it regressed.
- **Decide failure granularity up front.** Separating "this file is unusable" (whole-file `FAILED`)
  from "this one feature is odd" (per-feature status) made the API predictable and the requirement to
  "handle unsupported geometries gracefully" fall out naturally.
- **Untrusted uploads need defensive parsing**: bounded zip reads, hardened XML parsing, and size
  limits all had to be designed in, not bolted on.
- **Real-world formats are messy**: KML namespaces vary by version, coordinates sometimes contain
  spaces after commas, Placemarks can have no geometry, `__MACOSX` junk ends up in zips, and
  Shapefiles come without `.prj` files.
- A pure-Python stack was worth the trade-off: installation just works and tests run in about a second.

## Future scope

- **Async processing** (Celery/RQ or FastAPI background tasks) with polling via the existing
  `status` field and webhooks for very large files; streaming uploads to object storage (S3/GCS).
- **More formats:** KMZ, GeoJSON, GeoPackage, multi-layer shapefiles, `.shp` bundles uploaded as
  separate parts.
- **PostgreSQL + PostGIS** for spatial queries (bbox filter, intersects), and a GeoJSON
  `FeatureCollection` export endpoint.
- **Better measurement for edge cases:** summing parts of `GeometryCollection`s, antimeridian
  handling, optional equal-area projections, 3D lengths using Z, and a `?method=geodesic` switch.
- **User-selectable units** (acres, sq ft, miles) and a CRS override parameter for files with a
  wrong or missing `.prj`.
- **Production hardening:** authentication, rate limiting, request IDs / structured logging,
  Prometheus metrics, Alembic migrations, and a retention policy for old uploads.
- **Test depth:** property-based tests (Hypothesis) comparing projected vs geodesic results over random
  polygons around the globe, and a larger corpus of real-world files.
