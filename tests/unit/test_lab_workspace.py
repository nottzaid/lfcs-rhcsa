from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from sysadmin_lab.application.lab_workspace import LabWorkspace, WorkspacePaths
from sysadmin_lab.catalog import load_catalog, load_image_manifest
from sysadmin_lab.domain.session_machines import SessionMachine
from sysadmin_lab.domain.sessions import SessionState, SessionStatus
from sysadmin_lab.domain.virtual_machines import domain_identity

SESSION_ID = UUID("10000000-0000-0000-0000-000000000001")


class FakeRuntime:
    def __init__(self, tmp_path: Path, scenario_id: str) -> None:
        self.state = (
            SessionState.declared(scenario_id, SESSION_ID)
            .transition(SessionStatus.PROVISIONING)
            .transition(SessionStatus.READY)
        )
        self.machine = SessionMachine(
            SESSION_ID,
            "node1",
            domain_identity(scenario_id, SESSION_ID, "node1"),
            "labadmin",
            "secret",
            (tmp_path / "key").resolve(),
            "192.0.2.10",
        )
        self.sessions = SimpleNamespace(
            list_all=lambda **_kwargs: (self.state,),
            get=lambda _session_id: self.state,
        )
        self.machines = SimpleNamespace(list=lambda _session_id: (self.machine,))
        self.progress = SimpleNamespace(list_all=lambda: ("progress",))
        self.start_args: tuple | None = None
        self.reset_args: tuple | None = None
        self.scenarios = SimpleNamespace(
            start=self.start,
            check=lambda _session_id, manifest, **_options: ("report", manifest.scenario_id),
            reset=self.reset,
            destroy=lambda _session_id: self.state.transition(SessionStatus.DESTROYING).transition(
                SessionStatus.DESTROYED
            ),
        )

    def __enter__(self) -> FakeRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def start(self, manifest, setup, images):
        self.start_args = (manifest, setup, images)
        return "started"

    def reset(self, session_id, manifest, setup, images):
        self.reset_args = (session_id, manifest, setup, images)
        return "reset"


def test_workspace_paths_and_catalog_access(tmp_path: Path) -> None:
    paths = WorkspacePaths.under(tmp_path / "project")
    assert paths.project_root == (tmp_path / "project").resolve()
    assert paths.scenario_directory == paths.project_root / "scenarios"
    assert paths.runtime_root == paths.project_root / "runtime"

    root = Path(__file__).parents[2]
    workspace = LabWorkspace(WorkspacePaths.under(root))
    expected = load_catalog(root / "scenarios")
    assert workspace.scenarios() == expected
    assert workspace.scenario(expected[0].scenario_id) == expected[0]


def test_workspace_delegates_complete_learner_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[2]
    manifest = load_catalog(root / "scenarios")[0]
    image_manifest = load_image_manifest(root / "images" / "rocky-10.2" / "manifest.yaml")
    paths = WorkspacePaths(
        project_root=root,
        scenario_directory=root / "scenarios",
        image_manifest=root / "images" / "rocky-10.2" / "manifest.yaml",
        image_cache=tmp_path / "cache",
        runtime_root=tmp_path / "runtime",
        mock_exam_directory=root / "mock-exams",
    )
    runtime = FakeRuntime(tmp_path, manifest.scenario_id)
    image = tmp_path / "base.qcow2"
    monkeypatch.setattr(
        "sysadmin_lab.application.lab_workspace.open_vm_runtime", lambda _path: runtime
    )
    monkeypatch.setattr(
        "sysadmin_lab.application.lab_workspace.ImageAcquirer.acquire",
        lambda _self, _manifest, _cache: image,
    )
    workspace = LabWorkspace(paths)

    snapshots = workspace.sessions(limit=1)
    assert snapshots[0].state == runtime.state
    assert snapshots[0].machines == (runtime.machine,)
    assert workspace.session(SESSION_ID) == snapshots[0]
    assert workspace.progress() == ("progress",)  # type: ignore[comparison-overlap]

    assert workspace.start(manifest.scenario_id) == "started"  # type: ignore[comparison-overlap]
    assert runtime.start_args is not None
    assert runtime.start_args[2] == {image_manifest.image_id: image.resolve()}
    assert workspace.check(SESSION_ID) == ("report", manifest.scenario_id)  # type: ignore[comparison-overlap]
    assert workspace.reset(SESSION_ID) == "reset"  # type: ignore[comparison-overlap]
    assert runtime.reset_args is not None
    assert runtime.reset_args[0] == SESSION_ID
    assert workspace.destroy(SESSION_ID).status is SessionStatus.DESTROYED
