from __future__ import annotations

import webbrowser
from pathlib import Path
from socket import AF_INET, AF_INET6, SOCK_STREAM, socket
from threading import Timer
from typing import Annotated
from uuid import UUID

import typer

from sysadmin_lab.application.checking import CheckReport
from sysadmin_lab.application.image_acquisition import HttpsDownloader, ImageAcquirer
from sysadmin_lab.application.scenario_sessions import StartedScenario
from sysadmin_lab.application.verification import ScenarioVerifier
from sysadmin_lab.application.vm_verification import VmScenarioDriver
from sysadmin_lab.catalog import (
    CatalogError,
    find_scenario,
    load_action_manifest,
    load_catalog,
    load_image_manifest,
)
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.actions import ActionManifest
from sysadmin_lab.domain.images import ImageVerificationError, verify_installation_source
from sysadmin_lab.domain.models import ScenarioManifest

app = typer.Typer(help="Operate and verify the local Linux administration lab.")
catalog_app = typer.Typer(help="Inspect and validate scenario catalogs.")
app.add_typer(catalog_app, name="catalog")
image_app = typer.Typer(help="Build and verify immutable guest images.")
app.add_typer(image_app, name="image")
session_app = typer.Typer(help="Operate low-level lab VM sessions.")
app.add_typer(session_app, name="session")
scenario_app = typer.Typer(help="Start, check, reset, and destroy learner scenarios.")
app.add_typer(scenario_app, name="scenario")


def _loopback_port_available(host: str, port: int) -> bool:
    family = AF_INET6 if host == "::1" else AF_INET
    bind_host = "::1" if family == AF_INET6 else "127.0.0.1"
    with socket(family, SOCK_STREAM) as probe:
        try:
            probe.bind((bind_host, port))
        except OSError:
            return False
    return True


@app.command("up")
def up(
    host: Annotated[
        str, typer.Option(help="Loopback address for the local website.")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8787,
    open_browser: Annotated[
        bool, typer.Option("--open-browser/--no-browser", help="Open the local site automatically.")
    ] = True,
    project_root: Annotated[Path, typer.Option(file_okay=False)] = Path("."),
) -> None:
    """Start the local scenario website and background lab controller."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        typer.echo("labctl up only binds to a loopback address", err=True)
        raise typer.Exit(code=2)
    root = project_root.resolve()
    required = (root / "scenarios", root / "images" / "rocky-10.2" / "manifest.yaml")
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        typer.echo(f"project root is missing required paths: {', '.join(missing)}", err=True)
        raise typer.Exit(code=2)
    if not _loopback_port_available(host, port):
        typer.echo(f"loopback port {port} is already in use; choose another with --port", err=True)
        raise typer.Exit(code=2)

    import uvicorn

    from sysadmin_lab.web import create_app

    display_host = "[::1]" if host == "::1" else host
    url = f"http://{display_host}:{port}/scenarios/topic/lfcs"
    typer.echo(f"Linux Admin Lab: {url}")
    typer.echo("Press Ctrl+C to stop the website. Running scenario VMs remain under your control.")
    if open_browser:
        opener = Timer(0.8, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    uvicorn.run(create_app(root), host=host, port=port, log_level="info")


@catalog_app.command("validate")
def validate_catalog(
    path: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
) -> None:
    """Validate every scenario manifest in a directory."""
    try:
        manifests = load_catalog(path)
    except CatalogError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"validated {len(manifests)} scenario manifest(s)")


@image_app.command("verify-source")
def verify_image_source(
    manifest_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    source_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Verify installation media against a pinned image manifest."""
    try:
        manifest = load_image_manifest(manifest_path)
        digest = verify_installation_source(manifest, source_path)
    except (CatalogError, ImageVerificationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"verified {manifest.image_id}: sha256:{digest}")


@image_app.command("fetch")
def fetch_image_source(
    manifest_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    cache_directory: Annotated[Path, typer.Argument(file_okay=False)],
) -> None:
    """Download or reuse a manifest-pinned installation source."""
    try:
        manifest = load_image_manifest(manifest_path)
        path = ImageAcquirer(HttpsDownloader()).acquire(manifest, cache_directory)
    except (CatalogError, ImageVerificationError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"ready {manifest.image_id}: {path}")


@session_app.command("start")
def start_session(
    base_image: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    scenario_id: Annotated[str, typer.Option()] = "infrastructure-smoke",
    host_name: Annotated[str, typer.Option()] = "node1",
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Start one persistent project-owned VM through the production session service."""
    try:
        with open_vm_runtime(runtime_root) as runtime:
            provisioned = runtime.vm_sessions.provision(
                scenario_id=scenario_id,
                host_name=host_name,
                base_image=base_image.resolve(),
            )
    except Exception as exc:
        typer.echo(f"session start failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    machine = provisioned.machine
    typer.echo(f"session: {provisioned.state.session_id}")
    typer.echo(f"domain: {machine.identity.name}")
    typer.echo(f"address: {machine.address}")
    typer.echo(f"console username: {machine.username}")
    typer.echo(f"console password: {machine.password}")
    typer.echo(f"ssh: ssh -i {machine.private_key} {machine.username}@{machine.address}")


@session_app.command("status")
def session_status(
    session_id: UUID,
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Show durable session and machine state."""
    try:
        with open_vm_runtime(runtime_root) as runtime:
            state = runtime.sessions.get(session_id)
            machines = runtime.machines.list(session_id)
    except Exception as exc:
        typer.echo(f"session status failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"session: {state.session_id}")
    typer.echo(f"scenario: {state.scenario_id}")
    typer.echo(f"status: {state.status.value}")
    typer.echo(f"generation: {state.generation}")
    for machine in machines:
        typer.echo(f"machine: {machine.host_name} {machine.identity.name} {machine.address or '-'}")


@session_app.command("destroy")
def destroy_session(
    session_id: UUID,
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Destroy the exact registered resources for one session."""
    try:
        with open_vm_runtime(runtime_root) as runtime:
            state = runtime.vm_sessions.destroy(session_id)
    except Exception as exc:
        typer.echo(f"session destroy failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"session {state.session_id}: {state.status.value}")


@session_app.command("check")
def check_session(
    session_id: UUID,
    manifest_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Evaluate a scenario manifest against one running session."""
    try:
        manifest = load_catalog(manifest_path.parent)
        selected = next(
            candidate for candidate in manifest if candidate.scenario_id == manifest_path.stem
        )
        with open_vm_runtime(runtime_root) as runtime:
            report = runtime.checks.run(session_id, selected)
    except Exception as exc:
        typer.echo(f"session check failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for result in report.results:
        status = (
            "ERROR" if result.observation.error else "PASS" if result.observation.passed else "FAIL"
        )
        typer.echo(f"{status} {result.check.check_id}: {result.observation.message}")
    typer.echo(f"score: {report.earned_weight}/{report.available_weight}")
    if report.has_errors or not report.required_passed:
        raise typer.Exit(code=1)


def _scenario_launch_inputs(
    scenario_id: str,
    scenario_directory: Path,
    image_manifest_path: Path,
    image_cache: Path,
) -> tuple[ScenarioManifest, ActionManifest, dict[str, Path]]:
    manifest = find_scenario(scenario_directory, scenario_id)
    setup = load_action_manifest(scenario_directory / manifest.setup)
    image_manifest = load_image_manifest(image_manifest_path)
    image_path = ImageAcquirer(HttpsDownloader()).acquire(image_manifest, image_cache)
    return manifest, setup, {image_manifest.image_id: image_path.resolve()}


def _show_started(started: StartedScenario) -> None:
    machine = started.provisioned.machine
    typer.echo(f"session: {started.provisioned.state.session_id}")
    typer.echo(f"domain: {machine.identity.name}")
    typer.echo(f"address: {machine.address}")
    typer.echo(f"console username: {machine.username}")
    typer.echo(f"console password: {machine.password}")
    typer.echo(f"ssh: ssh -i {machine.private_key} {machine.username}@{machine.address}")


def _show_report(report: CheckReport) -> None:
    for result in report.results:
        status = (
            "ERROR" if result.observation.error else "PASS" if result.observation.passed else "FAIL"
        )
        typer.echo(f"{status} {result.check.check_id}: {result.observation.message}")
    typer.echo(f"score: {report.earned_weight}/{report.available_weight}")


@scenario_app.command("start")
def start_scenario(
    scenario_id: str,
    scenario_directory: Annotated[Path, typer.Option(file_okay=False)] = Path("scenarios"),
    image_manifest_path: Annotated[Path, typer.Option(dir_okay=False)] = Path(
        "images/rocky-10.2/manifest.yaml"
    ),
    image_cache: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime/cache/images"),
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Provision a verified scenario and apply its reproducible broken state."""
    try:
        manifest, setup, images = _scenario_launch_inputs(
            scenario_id, scenario_directory, image_manifest_path, image_cache
        )
        with open_vm_runtime(runtime_root) as runtime:
            started = runtime.scenarios.start(manifest, setup, images)
    except Exception as exc:
        typer.echo(f"scenario start failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _show_started(started)
    typer.echo(f"task: {manifest.task}")


@scenario_app.command("check")
def check_scenario(
    session_id: UUID,
    scenario_directory: Annotated[Path, typer.Option(file_okay=False)] = Path("scenarios"),
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Check the resulting machine state for one learner scenario."""
    try:
        with open_vm_runtime(runtime_root) as runtime:
            state = runtime.sessions.get(session_id)
            manifest = find_scenario(scenario_directory, state.scenario_id)
            report = runtime.scenarios.check(session_id, manifest)
    except Exception as exc:
        typer.echo(f"scenario check failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    _show_report(report)
    if report.has_errors or not report.required_passed:
        raise typer.Exit(code=1)


@scenario_app.command("status")
def scenario_status(
    session_id: UUID,
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Show the durable state and connection details for a scenario."""
    session_status(session_id, runtime_root)


@scenario_app.command("reset")
def reset_scenario(
    session_id: UUID,
    scenario_directory: Annotated[Path, typer.Option(file_okay=False)] = Path("scenarios"),
    image_manifest_path: Annotated[Path, typer.Option(dir_okay=False)] = Path(
        "images/rocky-10.2/manifest.yaml"
    ),
    image_cache: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime/cache/images"),
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Replace one scenario with a fresh reproducible broken session."""
    try:
        with open_vm_runtime(runtime_root) as runtime:
            state = runtime.sessions.get(session_id)
            manifest, setup, images = _scenario_launch_inputs(
                state.scenario_id,
                scenario_directory,
                image_manifest_path,
                image_cache,
            )
            started = runtime.scenarios.reset(session_id, manifest, setup, images)
    except Exception as exc:
        typer.echo(f"scenario reset failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"replaced session: {session_id}")
    _show_started(started)


@scenario_app.command("destroy")
def destroy_scenario(
    session_id: UUID,
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime"),
) -> None:
    """Destroy one exact scenario session and its disposable artifacts."""
    try:
        with open_vm_runtime(runtime_root) as runtime:
            state = runtime.scenarios.destroy(session_id)
    except Exception as exc:
        typer.echo(f"scenario destroy failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"scenario session {state.session_id}: {state.status.value}")


@scenario_app.command("verify")
def verify_scenario(
    scenario_id: str,
    scenario_directory: Annotated[Path, typer.Option(file_okay=False)] = Path("scenarios"),
    image_manifest_path: Annotated[Path, typer.Option(dir_okay=False)] = Path(
        "images/rocky-10.2/manifest.yaml"
    ),
    image_cache: Annotated[Path, typer.Option(file_okay=False)] = Path("runtime/cache/images"),
    runtime_root: Annotated[Path, typer.Option(file_okay=False)] = Path(
        "runtime/acceptance"
    ),
) -> None:
    """Destructively replay a scenario's complete disposable-VM acceptance contract."""
    try:
        manifest, _setup, images = _scenario_launch_inputs(
            scenario_id, scenario_directory, image_manifest_path, image_cache
        )
        with open_vm_runtime(runtime_root) as runtime:
            driver = VmScenarioDriver(
                scenario_directory=scenario_directory,
                base_images=images,
                sessions=runtime.sessions,
                machines=runtime.machines,
                vm_sessions=runtime.vm_sessions,
                checks=runtime.checks,
                scenarios=runtime.scenarios,
                actions=runtime.actions,
            )
            report = ScenarioVerifier(driver).verify(manifest)
    except Exception as exc:
        typer.echo(f"scenario verification failed: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for phase in report.phases:
        expects_broken = phase.phase.value in {"initial", "reset"}
        accepted = phase.passed is not expects_broken
        status = "PASS" if accepted else "FAIL"
        outcome = "checks fail as designed" if expects_broken else "checks pass"
        solution = f" ({phase.solution})" if phase.solution else ""
        typer.echo(f"{status} {phase.phase.value}{solution}: {outcome}")
    if not report.passed:
        raise typer.Exit(code=1)
    typer.echo(f"verified acceptance contract: {report.scenario_id}")


if __name__ == "__main__":  # pragma: no cover - console-script entry point
    app()
