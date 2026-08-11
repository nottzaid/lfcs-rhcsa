from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from sysadmin_lab.domain.actions import ActionManifest
from sysadmin_lab.domain.curricula import CurriculumManifest
from sysadmin_lab.domain.images import ImageManifest
from sysadmin_lab.domain.models import ScenarioManifest


class CatalogError(ValueError):
    pass


def load_manifest(path: Path) -> ScenarioManifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ScenarioManifest.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"invalid scenario manifest {path}: {exc}") from exc


def load_catalog(path: Path) -> tuple[ScenarioManifest, ...]:
    candidates = sorted((*path.glob("*.yaml"), *path.glob("*.yml")))
    if not candidates:
        raise CatalogError(f"no scenario manifests found in {path}")
    manifests = tuple(load_manifest(candidate) for candidate in candidates)
    ids = [manifest.scenario_id for manifest in manifests]
    duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    if duplicates:
        raise CatalogError(f"duplicate scenario identifiers: {', '.join(duplicates)}")
    return manifests


def load_image_manifest(path: Path) -> ImageManifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ImageManifest.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"invalid image manifest {path}: {exc}") from exc


def load_curriculum_manifest(path: Path) -> CurriculumManifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return CurriculumManifest.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"invalid curriculum manifest {path}: {exc}") from exc


def load_action_manifest(path: Path) -> ActionManifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ActionManifest.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogError(f"invalid action manifest {path}: {exc}") from exc


def validate_objective_references(
    manifests: tuple[ScenarioManifest, ...], curricula: tuple[CurriculumManifest, ...]
) -> None:
    known_tracks = {curriculum.track for curriculum in curricula}
    indexes = {
        (curriculum.track, curriculum.curriculum_id): curriculum.objective_ids
        for curriculum in curricula
    }
    errors: list[str] = []
    for manifest in manifests:
        for reference in manifest.objectives:
            if reference.track not in known_tracks:
                continue
            objectives = indexes.get((reference.track, reference.version))
            if objectives is None:
                errors.append(
                    f"{manifest.scenario_id}: unknown {reference.track} curriculum "
                    f"{reference.version}"
                )
            elif reference.objective_id not in objectives:
                errors.append(
                    f"{manifest.scenario_id}: unknown objective {reference.objective_id} "
                    f"in {reference.version}"
                )
    if errors:
        raise CatalogError("invalid objective references: " + "; ".join(errors))
