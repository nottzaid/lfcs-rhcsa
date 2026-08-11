from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sysadmin_lab.catalog import (
    CatalogError,
    load_catalog,
    load_curriculum_manifest,
    load_image_manifest,
    load_manifest,
)
from tests.unit.test_models import minimal_manifest


def write_manifest(path: Path, scenario_id: str = "valid-scenario") -> None:
    raw = minimal_manifest()
    raw["scenario_id"] = scenario_id
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_empty_catalog_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no scenario manifests"):
        load_catalog(tmp_path)


def test_invalid_manifest_is_wrapped_with_its_path(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("scenario_id: INVALID", encoding="utf-8")
    with pytest.raises(CatalogError, match=str(path)):
        load_manifest(path)


def test_duplicate_scenario_identifiers_are_rejected(tmp_path: Path) -> None:
    write_manifest(tmp_path / "first.yaml")
    write_manifest(tmp_path / "second.yml")
    with pytest.raises(CatalogError, match="duplicate scenario identifiers"):
        load_catalog(tmp_path)


def test_invalid_image_manifest_is_wrapped_with_its_path(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("image_id: INVALID", encoding="utf-8")
    with pytest.raises(CatalogError, match=str(path)):
        load_image_manifest(path)


def test_invalid_curriculum_manifest_is_wrapped_with_its_path(tmp_path: Path) -> None:
    path = tmp_path / "curriculum.yaml"
    path.write_text("curriculum_id: INVALID", encoding="utf-8")
    with pytest.raises(CatalogError, match=str(path)):
        load_curriculum_manifest(path)
