import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DATA = Path(__file__).resolve().parents[1] / "scripts" / "data"
if str(SCRIPTS_DATA) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DATA))

import _download  # noqa: E402


def test_dataset_manifest_has_unique_ids_and_https_urls() -> None:
    manifest_path = Path("benchmarks/manifests/datasets.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = [dataset["id"] for dataset in data["datasets"]]
    assert len(ids) == len(set(ids))
    for dataset in data["datasets"]:
        assert dataset["homepage"].startswith("https://")
        for file in dataset.get("files", []):
            assert file["url"].startswith("https://")


def test_dataset_manifest_validates_against_typed_schema() -> None:
    manifest = _download.load_manifest(Path("benchmarks/manifests/datasets.json"))

    assert manifest.schema_version == 1
    assert {dataset.id for dataset in manifest.datasets} >= {
        "longmemeval-cleaned",
        "locomo",
    }
    for dataset in manifest.datasets:
        assert dataset.role in {"mainline", "optional"}
        assert dataset.dataset_version
        assert dataset.dataset_date
        assert dataset.license_reference.startswith("https://")
        for file in dataset.files:
            assert file.filename.endswith(".json")
            assert file.url.startswith("https://")
            assert file.expected_shape in {"array", "object"}
            assert file.expected_records is None or file.expected_records > 0
            assert file.expected_size_bytes is None or file.expected_size_bytes > 0
            assert file.sha256 is None or len(file.sha256) == 64


def test_validate_json_checks_shape_and_top_level_record_count(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text('[{"id": 1}, {"id": 2}]', encoding="utf-8")

    result = _download.validate_json(
        path,
        expected_shape="array",
        expected_records=2,
    )

    assert result.shape == "array"
    assert result.record_count == 2

    with pytest.raises(ValueError, match="expected 3 records"):
        _download.validate_json(path, expected_shape="array", expected_records=3)

    with pytest.raises(ValueError, match="expected JSON object"):
        _download.validate_json(path, expected_shape="object")


def test_downloader_refuses_html_body_even_with_json_content_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HtmlResponse:
        headers = {"Content-Type": "application/json"}
        status = 200
        reason = "OK"

        def __init__(self) -> None:
            self._body = memoryview(b"<!doctype html><title>error</title>")
            self._offset = 0

        def read(self, size: int = -1) -> bytes:
            if size == -1:
                size = len(self._body) - self._offset
            chunk = self._body[self._offset : self._offset + size].tobytes()
            self._offset += len(chunk)
            return chunk

        def __enter__(self) -> "HtmlResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(_download.urllib.request, "urlopen", lambda *args, **kwargs: HtmlResponse())
    destination = tmp_path / "dataset.json"

    with pytest.raises(RuntimeError, match="unexpected HTML"):
        _download.download_json("https://example.test/dataset.json", destination)

    assert not destination.exists()
    assert not destination.with_suffix(".json.part").exists()


def test_downloader_refuses_error_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class ErrorResponse:
        headers = {"Content-Type": "application/json"}
        status = 503
        reason = "Service Unavailable"

        def __init__(self) -> None:
            self._read = False

        def read(self, size: int = -1) -> bytes:
            if self._read:
                return b""
            self._read = True
            return b"[]"

        def __enter__(self) -> "ErrorResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(_download.urllib.request, "urlopen", lambda *args, **kwargs: ErrorResponse())

    with pytest.raises(RuntimeError, match="HTTP 503"):
        _download.download_json("https://example.test/dataset.json", tmp_path / "dataset.json")


def test_existing_valid_file_is_not_redownloaded_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "dataset.json"
    destination.write_text("[]", encoding="utf-8")

    def fail_urlopen(*args: object, **kwargs: object) -> object:
        raise AssertionError("existing valid files must not be re-downloaded")

    monkeypatch.setattr(_download.urllib.request, "urlopen", fail_urlopen)

    assert _download.download_json("https://example.test/dataset.json", destination) == destination
