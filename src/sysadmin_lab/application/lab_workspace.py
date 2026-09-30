from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sysadmin_lab.application.image_acquisition import HttpsDownloader, ImageAcquirer
from sysadmin_lab.application.image_building import resolve_built_image
from sysadmin_lab.application.scenario_sessions import LearnerCheckReport, StartedScenario
from sysadmin_lab.catalog import (
    find_scenario,
    load_action_manifest,
    load_catalog,
    load_curriculum_manifest,
    load_image_manifest,
    load_mock_catalog,
)
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.curricula import CurriculumManifest
from sysadmin_lab.domain.mock_exams import MockExamManifest
from sysadmin_lab.domain.models import ScenarioManifest
from sysadmin_lab.domain.progress import ScenarioProgress
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    project_root: Path
    scenario_directory: Path
    image_manifest: Path
    image_cache: Path
    runtime_root: Path
    mock_exam_directory: Path
    curriculum: Path

    @classmethod
    def under(cls, project_root: Path) -> WorkspacePaths:
        root = project_root.resolve()
        return cls(
            project_root=root,
            scenario_directory=root / "scenarios",
            image_manifest=root / "images" / "rocky-10.2" / "iso-manifest.yaml",
            image_cache=root / "runtime" / "cache" / "images",
            runtime_root=root / "runtime",
            mock_exam_directory=root / "mock-exams",
            curriculum=root / "curricula" / "lfcs-2026-08.yaml",
        )


@dataclass(frozen=True, slots=True)
class ScenarioSessionSnapshot:
    state: SessionState
    machines: tuple[SessionMachine, ...]


class LabWorkspace:
    """High-level learner operations shared by local user interfaces."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def scenarios(self) -> tuple[ScenarioManifest, ...]:
        return load_catalog(self.paths.scenario_directory)

    def scenario(self, scenario_id: str) -> ScenarioManifest:
        return find_scenario(self.paths.scenario_directory, scenario_id)

    def curriculum(self) -> CurriculumManifest:
        return load_curriculum_manifest(self.paths.curriculum)

    def mock_exams(self) -> tuple[MockExamManifest, ...]:
        return load_mock_catalog(self.paths.mock_exam_directory)

    def mock_exam(self, mock_id: str) -> MockExamManifest:
        try:
            return next(mock for mock in self.mock_exams() if mock.mock_id == mock_id)
        except StopIteration as exc:
            raise LookupError(f"mock exam does not exist: {mock_id}") from exc

    def sessions(self, *, limit: int = 100) -> tuple[ScenarioSessionSnapshot, ...]:
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            return tuple(
                ScenarioSessionSnapshot(state, runtime.machines.list(state.session_id))
                for state in runtime.sessions.list_all(limit=limit)
            )

    def session(self, session_id: UUID) -> ScenarioSessionSnapshot:
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            state = runtime.sessions.get(session_id)
            return ScenarioSessionSnapshot(state, runtime.machines.list(session_id))

    def progress(self) -> tuple[ScenarioProgress, ...]:
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            return runtime.progress.list_all()

    def start(self, scenario_id: str) -> StartedScenario:
        manifest = self.scenario(scenario_id)
        setup = load_action_manifest(self.paths.scenario_directory / manifest.setup)
        images = self._acquire_images()
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            return runtime.scenarios.start(manifest, setup, images)

    def check(self, session_id: UUID, *, prove_persistence: bool = True) -> LearnerCheckReport:
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            state = runtime.sessions.get(session_id)
            manifest = self.scenario(state.scenario_id)
            return runtime.scenarios.check(
                session_id, manifest, prove_persistence=prove_persistence
            )

    def reset(self, session_id: UUID) -> StartedScenario:
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            state = runtime.sessions.get(session_id)
        manifest = self.scenario(state.scenario_id)
        setup = load_action_manifest(self.paths.scenario_directory / manifest.setup)
        images = self._acquire_images()
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            return runtime.scenarios.reset(session_id, manifest, setup, images)

    def destroy(self, session_id: UUID) -> SessionState:
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            return runtime.scenarios.destroy(session_id)

    def _acquire_images(self) -> dict[str, Path]:
        image_manifest = load_image_manifest(self.paths.image_manifest)
        if image_manifest.build.method == "kickstart":
            image_path = resolve_built_image(
                image_manifest, self.paths.image_manifest, self.paths.image_cache
            ).artifact
        else:
            image_path = ImageAcquirer(HttpsDownloader()).acquire(
                image_manifest, self.paths.image_cache
            )
        return {image_manifest.image_id: image_path.resolve()}
