from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from sysadmin_lab.catalog import (
    CatalogError,
    find_scenario,
    load_action_manifest,
    load_catalog,
    load_curriculum_manifest,
    load_image_manifest,
    load_manifest,
    load_mock_catalog,
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


def test_mock_exam_and_action_manifests_are_refused_with_their_path(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="no mock exam manifests"):
        load_mock_catalog(tmp_path)
    broken = tmp_path / "mock.yaml"
    broken.write_text("mock_id: [unterminated", encoding="utf-8")
    with pytest.raises(CatalogError, match=f"invalid mock exam manifest {re.escape(str(broken))}"):
        load_mock_catalog(tmp_path)
    with pytest.raises(CatalogError, match=r"invalid action manifest .*missing\.yaml"):
        load_action_manifest(tmp_path / "missing.yaml")


def test_the_real_mock_exams_load_and_duplicates_are_refused(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    mocks = load_mock_catalog(root / "mock-exams")
    assert len({mock.mock_id for mock in mocks}) == len(mocks) >= 3
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text((root / "mock-exams" / "lfcs-mock-a.yaml").read_text())
    with pytest.raises(CatalogError, match="duplicate mock exam identifiers"):
        load_mock_catalog(tmp_path)


def test_a_scenario_is_found_by_id_or_refused_by_name(tmp_path: Path) -> None:
    write_manifest(tmp_path / "one.yaml", "known-scenario")
    assert find_scenario(tmp_path, "known-scenario").scenario_id == "known-scenario"
    with pytest.raises(CatalogError, match="scenario does not exist: other-scenario"):
        find_scenario(tmp_path, "other-scenario")
