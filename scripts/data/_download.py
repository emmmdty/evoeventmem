from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

DatasetRole = Literal["mainline", "optional"]
JsonShape = Literal["array", "object"]

_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_EAGER_JSON_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class JsonValidationResult:
    shape: JsonShape
    record_count: int | None
    size_bytes: int
    sha256: str | None = None


@dataclass(frozen=True)
class DatasetFileSpec:
    variant: str
    filename: str
    url: str
    expected_shape: JsonShape
    expected_records: int | None = None
    expected_size_bytes: int | None = None
    sha256: str | None = None
    source_etag: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any], dataset_id: str) -> DatasetFileSpec:
        variant = _required_str(data, "variant", dataset_id)
        filename = _required_str(data, "filename", f"{dataset_id}.{variant}")
        url = _required_str(data, "url", f"{dataset_id}.{variant}")
        expected_shape = _required_shape(data, "expected_shape", f"{dataset_id}.{variant}")
        expected_records = _optional_positive_int(
            data,
            "expected_records",
            f"{dataset_id}.{variant}",
        )
        expected_size_bytes = _optional_positive_int(
            data,
            "expected_size_bytes",
            f"{dataset_id}.{variant}",
        )
        sha256 = _optional_checksum(data, "sha256", f"{dataset_id}.{variant}")
        source_etag = _optional_str(data, "source_etag", f"{dataset_id}.{variant}")
        if "/" in filename or filename in {"", ".", ".."}:
            raise ValueError(f"{dataset_id}.{variant}.filename must be a plain file name")
        if not url.startswith("https://"):
            raise ValueError(f"{dataset_id}.{variant}.url must start with https://")
        return cls(
            variant=variant,
            filename=filename,
            url=url,
            expected_shape=expected_shape,
            expected_records=expected_records,
            expected_size_bytes=expected_size_bytes,
            sha256=sha256,
            source_etag=source_etag,
        )


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    role: DatasetRole
    homepage: str
    dataset_version: str
    dataset_date: str
    license_reference: str
    files: tuple[DatasetFileSpec, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> DatasetSpec:
        dataset_id = _required_str(data, "id", "dataset")
        role = _required_role(data, "role", dataset_id)
        homepage = _required_str(data, "homepage", dataset_id)
        dataset_version = _required_str(data, "dataset_version", dataset_id)
        dataset_date = _required_date(data, "dataset_date", dataset_id)
        license_reference = _required_str(data, "license_reference", dataset_id)
        raw_files = data.get("files", [])
        if not isinstance(raw_files, list):
            raise ValueError(f"{dataset_id}.files must be a list")
        files = tuple(DatasetFileSpec.from_mapping(item, dataset_id) for item in raw_files)
        _ensure_unique(
            (file.variant for file in files),
            f"{dataset_id}.files[].variant",
        )
        _ensure_unique(
            (file.filename for file in files),
            f"{dataset_id}.files[].filename",
        )
        if not homepage.startswith("https://"):
            raise ValueError(f"{dataset_id}.homepage must start with https://")
        if not license_reference.startswith("https://"):
            raise ValueError(f"{dataset_id}.license_reference must start with https://")
        return cls(
            id=dataset_id,
            role=role,
            homepage=homepage,
            dataset_version=dataset_version,
            dataset_date=dataset_date,
            license_reference=license_reference,
            files=files,
        )

    def file(self, variant: str) -> DatasetFileSpec:
        for file in self.files:
            if file.variant == variant:
                return file
        raise KeyError(f"{self.id} has no file variant {variant!r}")


@dataclass(frozen=True)
class DatasetManifest:
    schema_version: int
    datasets: tuple[DatasetSpec, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> DatasetManifest:
        schema_version = data.get("schema_version")
        if schema_version != 1:
            raise ValueError("schema_version must be 1")
        raw_datasets = data.get("datasets")
        if not isinstance(raw_datasets, list) or not raw_datasets:
            raise ValueError("datasets must be a non-empty list")
        datasets = tuple(DatasetSpec.from_mapping(item) for item in raw_datasets)
        _ensure_unique((dataset.id for dataset in datasets), "datasets[].id")
        return cls(schema_version=schema_version, datasets=datasets)

    def dataset(self, dataset_id: str) -> DatasetSpec:
        for dataset in self.datasets:
            if dataset.id == dataset_id:
                return dataset
        raise KeyError(f"manifest has no dataset {dataset_id!r}")


def load_manifest(path: Path) -> DatasetManifest:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return DatasetManifest.from_mapping(data)


def download_json(
    url: str,
    destination: Path,
    force: bool = False,
    *,
    expected_shape: JsonShape | None = None,
    expected_records: int | None = None,
    expected_size_bytes: int | None = None,
    sha256: str | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        validate_json(
            destination,
            expected_shape=expected_shape,
            expected_records=expected_records,
            expected_size_bytes=expected_size_bytes,
            sha256=sha256,
        )
        print(f"exists: {destination}")
        return destination

    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "EvoEventMem/0.1"})
    try:
        try:
            response_context = urllib.request.urlopen(request, timeout=120)  # noqa: S310
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code} while downloading {url}") from exc
        with response_context as response:
            status = getattr(response, "status", None)
            if status is not None and status >= 400:
                reason = getattr(response, "reason", "")
                raise RuntimeError(f"HTTP {status} {reason} while downloading {url}".strip())
            content_type = response.headers.get("Content-Type", "")
            if "html" in content_type.lower():
                raise RuntimeError(f"unexpected HTML response from {url}")
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        validate_json(
            temporary,
            expected_shape=expected_shape,
            expected_records=expected_records,
            expected_size_bytes=expected_size_bytes,
            sha256=sha256,
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"downloaded: {destination}")
    return destination


def validate_json(
    path: Path,
    *,
    expected_shape: JsonShape | None = None,
    expected_records: int | None = None,
    expected_size_bytes: int | None = None,
    sha256: str | None = None,
) -> JsonValidationResult:
    size_bytes = path.stat().st_size
    if expected_size_bytes is not None and size_bytes != expected_size_bytes:
        raise ValueError(
            f"{path} has {size_bytes} bytes; expected {expected_size_bytes} bytes",
        )
    if sha256 is not None and not _CHECKSUM_RE.match(sha256):
        raise ValueError(f"expected sha256 for {path} must be 64 lowercase hex characters")

    _reject_html_prefix(path)
    digest = _sha256(path) if sha256 is not None else None
    if sha256 is not None and digest != sha256:
        raise ValueError(f"{path} sha256={digest}; expected {sha256}")

    if size_bytes <= _MAX_EAGER_JSON_BYTES:
        result = _load_small_json(path, digest)
    else:
        result = _scan_large_json(path, digest)

    if expected_shape is not None and result.shape != expected_shape:
        raise ValueError(f"{path} expected JSON {expected_shape}; found {result.shape}")
    if expected_records is not None and result.record_count != expected_records:
        raise ValueError(
            f"{path} has {result.record_count} records; expected {expected_records} records",
        )
    return result


def _load_small_json(path: Path, digest: str | None) -> JsonValidationResult:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if isinstance(payload, list):
        return JsonValidationResult("array", len(payload), path.stat().st_size, digest)
    if isinstance(payload, dict):
        return JsonValidationResult("object", len(payload), path.stat().st_size, digest)
    raise ValueError(f"{path} must contain a top-level JSON array or object")


def _scan_large_json(path: Path, digest: str | None) -> JsonValidationResult:
    shape: JsonShape | None = None
    depth = 0
    in_string = False
    escape = False
    root_closed = False
    expecting_array_value = False
    array_count = 0
    with path.open("r", encoding="utf-8") as handle:
        while chunk := handle.read(1024 * 1024):
            for char in chunk:
                if root_closed:
                    if not char.isspace():
                        raise ValueError(f"{path} has trailing content after the root JSON value")
                    continue
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char.isspace():
                    continue
                if shape is None:
                    if char == "[":
                        shape = "array"
                        depth = 1
                        expecting_array_value = True
                        continue
                    if char == "{":
                        shape = "object"
                        depth = 1
                        continue
                    raise ValueError(f"{path} must contain a top-level JSON array or object")
                if shape == "array" and depth == 1:
                    if char == "]":
                        if expecting_array_value and array_count > 0:
                            raise ValueError(f"{path} has a trailing comma in the top-level array")
                        depth = 0
                        root_closed = True
                        continue
                    if char == ",":
                        if expecting_array_value:
                            raise ValueError(f"{path} has an empty item in the top-level array")
                        expecting_array_value = True
                        continue
                    if expecting_array_value:
                        array_count += 1
                        expecting_array_value = False
                if char == '"':
                    in_string = True
                    continue
                if char in "[{":
                    depth += 1
                    continue
                if char in "]}":
                    depth -= 1
                    if depth < 0:
                        raise ValueError(f"{path} has an unexpected closing bracket")
                    if depth == 0:
                        root_closed = True
    if in_string:
        raise ValueError(f"{path} has an unterminated string")
    if shape is None:
        raise ValueError(f"{path} is empty")
    if depth != 0 or not root_closed:
        raise ValueError(f"{path} has an incomplete JSON document")
    return JsonValidationResult(
        shape=shape,
        record_count=array_count if shape == "array" else None,
        size_bytes=path.stat().st_size,
        sha256=digest,
    )


def _reject_html_prefix(path: Path) -> None:
    with path.open("rb") as handle:
        prefix = handle.read(512).lstrip().lower()
    if prefix.startswith(b"<"):
        raise RuntimeError(f"{path} contains unexpected HTML/error page, not JSON")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_str(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value


def _optional_str(data: dict[str, Any], key: str, context: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context}.{key} must be a non-empty string when set")
    return value


def _required_role(data: dict[str, Any], key: str, context: str) -> DatasetRole:
    value = _required_str(data, key, context)
    if value not in {"mainline", "optional"}:
        raise ValueError(f"{context}.{key} must be 'mainline' or 'optional'")
    return value


def _required_shape(data: dict[str, Any], key: str, context: str) -> JsonShape:
    value = _required_str(data, key, context)
    if value not in {"array", "object"}:
        raise ValueError(f"{context}.{key} must be 'array' or 'object'")
    return value


def _required_date(data: dict[str, Any], key: str, context: str) -> str:
    value = _required_str(data, key, context)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{context}.{key} must be an ISO date") from exc
    return value


def _optional_positive_int(data: dict[str, Any], key: str, context: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{context}.{key} must be a positive integer when set")
    return value


def _optional_checksum(data: dict[str, Any], key: str, context: str) -> str | None:
    value = _optional_str(data, key, context)
    if value is not None and not _CHECKSUM_RE.match(value):
        raise ValueError(f"{context}.{key} must be 64 lowercase hex characters")
    return value


def _ensure_unique(values: object, context: str) -> None:
    seen: set[object] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{context} contains duplicate value {value!r}")
        seen.add(value)
