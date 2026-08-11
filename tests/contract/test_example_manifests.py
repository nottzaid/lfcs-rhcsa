from pathlib import Path

from sysadmin_lab.catalog import (
    load_catalog,
    load_curriculum_manifest,
    validate_objective_references,
)


def test_all_example_manifests_satisfy_contract() -> None:
    root = Path(__file__).parents[2]
    manifests = load_catalog(root / "examples" / "scenarios")
    assert manifests
    curricula = (load_curriculum_manifest(root / "curricula" / "lfcs-2026-08.yaml"),)
    validate_objective_references(manifests, curricula)
