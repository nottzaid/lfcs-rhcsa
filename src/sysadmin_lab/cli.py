from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from sysadmin_lab.application.image_acquisition import HttpsDownloader, ImageAcquirer
from sysadmin_lab.catalog import CatalogError, load_catalog, load_image_manifest
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.images import ImageVerificationError, verify_installation_source

app = typer.Typer(help="Operate and verify the local Linux administration lab.")
catalog_app = typer.Typer(help="Inspect and validate scenario catalogs.")
app.add_typer(catalog_app, name="catalog")
image_app = typer.Typer(help="Build and verify immutable guest images.")
app.add_typer(image_app, name="image")
session_app = typer.Typer(help="Operate low-level lab VM sessions.")
app.add_typer(session_app, name="session")


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


if __name__ == "__main__":  # pragma: no cover - console-script entry point
    app()
