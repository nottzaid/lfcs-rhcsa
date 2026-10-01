from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sysadmin_lab.application.image_acquisition import HttpsDownloader, ImageAcquirer
from sysadmin_lab.application.image_building import resolve_built_image
from sysadmin_lab.application.scenario_sessions import LearnerCheckReport, StartedScenario
from sysadmin_lab.catalog import (
    CatalogError,
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
from sysadmin_lab.domain.rehearsals import RehearsalScore, score_rehearsal
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


def _manifest_signature(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """What changes when any manifest in a directory is added, removed, or edited."""
    stats = (
        (path.name, path.stat()) for path in (*directory.glob("*.yaml"), *directory.glob("*.yml"))
    )
    return tuple(sorted((name, stat.st_mtime_ns, stat.st_size) for name, stat in stats))


class LabWorkspace:
    """High-level learner operations shared by local user interfaces."""

    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths
        # Validating every manifest takes about 0.4 s, so pages reuse the last result until
        # a manifest file changes; authors still see their edits on the next request.
        self._scenarios: tuple[object, tuple[ScenarioManifest, ...]] | None = None
        self._mock_exams: tuple[object, tuple[MockExamManifest, ...]] | None = None

    def scenarios(self) -> tuple[ScenarioManifest, ...]:
        signature = _manifest_signature(self.paths.scenario_directory)
        if self._scenarios is None or self._scenarios[0] != signature:
            self._scenarios = (signature, load_catalog(self.paths.scenario_directory))
        return self._scenarios[1]

    def scenario(self, scenario_id: str) -> ScenarioManifest:
        try:
            return next(s for s in self.scenarios() if s.scenario_id == scenario_id)
        except StopIteration as exc:
            raise CatalogError(f"scenario does not exist: {scenario_id}") from exc

    def curriculum(self) -> CurriculumManifest:
        return load_curriculum_manifest(self.paths.curriculum)

    def mock_exams(self) -> tuple[MockExamManifest, ...]:
        signature = _manifest_signature(self.paths.mock_exam_directory)
        if self._mock_exams is None or self._mock_exams[0] != signature:
            self._mock_exams = (signature, load_mock_catalog(self.paths.mock_exam_directory))
        return self._mock_exams[1]

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

    def start_rehearsal(self, mock_id: str) -> datetime:
        self.mock_exam(mock_id)
        started_at = datetime.now(UTC)
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            runtime.rehearsals.start(mock_id, started_at)
        return started_at

    def rehearsal(self, mock_id: str) -> RehearsalScore | None:
        """The current rehearsal of a mock exam, scored, or None if it was never started."""
        mock = self.mock_exam(mock_id)
        with open_vm_runtime(self.paths.runtime_root) as runtime:
            started_at = runtime.rehearsals.started_at(mock_id)
            attempts = runtime.progress.attempts()
        if started_at is None:
            return None
        versions = {scenario.scenario_id: scenario.version for scenario in self.scenarios()}
        return score_rehearsal(
            mock.tasks,
            versions,
            attempts,
            started_at,
            self.curriculum().exam.passing_score_percent,
        )

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
