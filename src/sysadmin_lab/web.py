from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sysadmin_lab.application.background_jobs import (
    BackgroundJobQueue,
    JobConflictError,
    JobNotFoundError,
    JobSnapshot,
)
from sysadmin_lab.application.checking import CheckReport
from sysadmin_lab.application.lab_workspace import (
    LabWorkspace,
    ScenarioSessionSnapshot,
    WorkspacePaths,
)
from sysadmin_lab.application.scenario_sessions import StartedScenario
from sysadmin_lab.domain.models import ScenarioManifest, ScenarioStatus
from sysadmin_lab.domain.progress import ScenarioProgress
from sysadmin_lab.domain.sessions import SessionState

ASSET_ROOT = Path(__file__).with_name("web_assets")


def _manifest_json(manifest: ScenarioManifest) -> dict[str, Any]:
    return {
        "scenario_id": manifest.scenario_id,
        "version": manifest.version,
        "status": manifest.status.value,
        "title": manifest.title,
        "summary": manifest.summary,
        "estimated_minutes": manifest.estimated_minutes,
        "task_type": manifest.task_type.value,
        "difficulty": manifest.difficulty.value,
        "task": manifest.task,
        "objectives": [
            {
                "track": objective.track.value,
                "version": objective.version,
                "objective_id": objective.objective_id,
                "title": objective.title,
            }
            for objective in manifest.objectives
        ],
        "hosts": [
            {
                "name": host.name,
                "image": host.image,
                "memory_mib": host.memory_mib,
                "vcpus": host.vcpus,
            }
            for host in manifest.topology.hosts
        ],
    }


def _session_json(snapshot: ScenarioSessionSnapshot) -> dict[str, Any]:
    state = snapshot.state
    return {
        "session_id": str(state.session_id),
        "scenario_id": state.scenario_id,
        "status": state.status.value,
        "generation": state.generation,
        "error": state.error,
        "machines": [
            {
                "host_name": machine.host_name,
                "domain_name": machine.identity.name,
                "address": machine.address,
                "username": machine.username,
                "password": machine.password,
                "private_key": str(machine.private_key),
                "ssh_command": (
                    f"ssh -i {machine.private_key} {machine.username}@{machine.address}"
                    if machine.address
                    else None
                ),
            }
            for machine in snapshot.machines
        ],
    }


def _report_json(report: CheckReport) -> dict[str, Any]:
    return {
        "required_passed": report.required_passed,
        "has_errors": report.has_errors,
        "earned_weight": report.earned_weight,
        "available_weight": report.available_weight,
        "results": [
            {
                "check_id": result.check.check_id,
                "description": result.check.description,
                "status": (
                    "error"
                    if result.observation.error
                    else "passed"
                    if result.observation.passed
                    else "failed"
                ),
                "message": result.observation.message,
            }
            for result in report.results
        ],
    }


def _job_json(job: JobSnapshot) -> dict[str, Any]:
    payload = asdict(job)
    payload["job_id"] = str(job.job_id)
    payload["status"] = job.status.value
    payload["created_at"] = job.created_at.isoformat()
    payload["updated_at"] = job.updated_at.isoformat()
    return payload


def _progress_json(progress: ScenarioProgress) -> dict[str, Any]:
    return {
        "scenario_id": progress.scenario_id,
        "scenario_version": progress.scenario_version,
        "attempts": progress.attempts,
        "solved": progress.solved,
        "best_earned_weight": progress.best_earned_weight,
        "best_available_weight": progress.best_available_weight,
        "last_checked_at": progress.last_checked_at.isoformat(),
    }


def _started_result(started: StartedScenario) -> dict[str, Any]:
    session_id = started.provisioned.state.session_id
    return {
        "session_id": str(session_id),
        "redirect_url": f"/sessions/{session_id}",
    }


def _destroyed_result(state: SessionState) -> dict[str, Any]:
    return {
        "session_id": str(state.session_id),
        "status": state.status.value,
        "redirect_url": "/",
    }


def create_app(
    project_root: Path | None = None,
    *,
    workspace: LabWorkspace | None = None,
    jobs: BackgroundJobQueue | None = None,
) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()
    active_workspace = workspace or LabWorkspace(WorkspacePaths.under(root))
    active_jobs = jobs or BackgroundJobQueue()
    owns_jobs = jobs is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_jobs:
            active_jobs.close()

    app = FastAPI(
        title="Linux Admin Lab",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    app.mount("/static", StaticFiles(directory=ASSET_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ASSET_ROOT / "templates")

    @app.middleware("http")
    async def private_responses(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def require_local_action(request: Request) -> None:
        if request.headers.get("X-Lab-Request") != "browser":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="missing local action header",
            )

    def scenario_or_404(scenario_id: str) -> ScenarioManifest:
        try:
            return active_workspace.scenario(scenario_id)
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def session_or_404(session_id: UUID) -> ScenarioSessionSnapshot:
        try:
            return active_workspace.session(session_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def submit_job(*, kind: str, resource_key: str, operation: Any) -> JSONResponse:
        try:
            job = active_jobs.submit(kind=kind, resource_key=resource_key, operation=operation)
        except JobConflictError as exc:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": str(exc), "job_id": str(exc.job_id)},
            )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=_job_json(job))

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/scenarios/topic/lfcs", status_code=307)

    @app.get("/scenarios/topic/lfcs", response_class=HTMLResponse)
    def catalog_page(request: Request) -> HTMLResponse:
        scenarios = tuple(
            scenario
            for scenario in active_workspace.scenarios()
            if scenario.status is ScenarioStatus.VERIFIED
        )
        sessions = active_workspace.sessions(limit=20)
        progress = active_workspace.progress()
        active_by_scenario = {
            snapshot.state.scenario_id: snapshot
            for snapshot in sessions
            if snapshot.state.status.value == "ready"
        }
        progress_by_scenario = {
            item.scenario_id: item
            for item in progress
            if any(
                scenario.scenario_id == item.scenario_id
                and scenario.version == item.scenario_version
                for scenario in scenarios
            )
        }
        return templates.TemplateResponse(
            request=request,
            name="catalog.html",
            context={
                "scenarios": scenarios,
                "sessions": sessions,
                "active_by_scenario": active_by_scenario,
                "progress_by_scenario": progress_by_scenario,
            },
        )

    @app.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
    def scenario_page(request: Request, scenario_id: str) -> HTMLResponse:
        manifest = scenario_or_404(scenario_id)
        return templates.TemplateResponse(
            request=request,
            name="scenario.html",
            context={"scenario": manifest},
        )

    @app.get("/sessions/{session_id}", response_class=HTMLResponse)
    def session_page(request: Request, session_id: UUID) -> HTMLResponse:
        snapshot = session_or_404(session_id)
        manifest = scenario_or_404(snapshot.state.scenario_id)
        return templates.TemplateResponse(
            request=request,
            name="session.html",
            context={"scenario": manifest, "session": snapshot},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict[str, Any]]:
        return [
            _manifest_json(manifest)
            for manifest in active_workspace.scenarios()
            if manifest.status is ScenarioStatus.VERIFIED
        ]

    @app.get("/api/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str) -> dict[str, Any]:
        return _manifest_json(scenario_or_404(scenario_id))

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        return [_session_json(snapshot) for snapshot in active_workspace.sessions(limit=100)]

    @app.get("/api/progress")
    def list_progress() -> list[dict[str, Any]]:
        return [_progress_json(progress) for progress in active_workspace.progress()]

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: UUID) -> dict[str, Any]:
        return _session_json(session_or_404(session_id))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: UUID) -> dict[str, Any]:
        try:
            return _job_json(active_jobs.get(job_id))
        except JobNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/scenarios/{scenario_id}/sessions",
        dependencies=[Depends(require_local_action)],
    )
    def start_scenario(scenario_id: str) -> JSONResponse:
        scenario_or_404(scenario_id)
        return submit_job(
            kind="start",
            resource_key=f"scenario:{scenario_id}",
            operation=lambda: _started_result(active_workspace.start(scenario_id)),
        )

    @app.post(
        "/api/sessions/{session_id}/check",
        dependencies=[Depends(require_local_action)],
    )
    def check_scenario(session_id: UUID) -> JSONResponse:
        session_or_404(session_id)
        return submit_job(
            kind="check",
            resource_key=f"session:{session_id}",
            operation=lambda: _report_json(active_workspace.check(session_id)),
        )

    @app.post(
        "/api/sessions/{session_id}/reset",
        dependencies=[Depends(require_local_action)],
    )
    def reset_scenario(session_id: UUID) -> JSONResponse:
        session_or_404(session_id)
        return submit_job(
            kind="reset",
            resource_key=f"session:{session_id}",
            operation=lambda: _started_result(active_workspace.reset(session_id)),
        )

    @app.post(
        "/api/sessions/{session_id}/destroy",
        dependencies=[Depends(require_local_action)],
    )
    def destroy_scenario(session_id: UUID) -> JSONResponse:
        session_or_404(session_id)
        return submit_job(
            kind="destroy",
            resource_key=f"session:{session_id}",
            operation=lambda: _destroyed_result(active_workspace.destroy(session_id)),
        )

    return app
