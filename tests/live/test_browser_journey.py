"""A learner's whole journey, driven through a real browser against a real lab.

`labctl up` serves the site from a scratch project root that shares only the scenarios and
the verified image cache. Chromium launches a scenario, the test repairs it over the SSH
details the page shows, and the page's Check proves the repair across a reboot, opens the
debrief, and finally destroys the machines.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from sysadmin_lab.adapters.ssh_guest import BoundedSubprocessRunner, SshGuestExecutor
from sysadmin_lab.application.actions import ActionRunner
from sysadmin_lab.application.guest_execution import GuestEndpoint
from sysadmin_lab.catalog import load_action_manifest, load_manifest
from sysadmin_lab.composition import open_vm_runtime
from sysadmin_lab.domain.sessions import SessionStatus

pytestmark = pytest.mark.live

ROOT = Path(__file__).parents[2]
SCENARIO = "runaway-service-recovery"
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
CHROMEDRIVER = shutil.which("chromedriver")


def wait_for_operation(browser: Any, done: Any, seconds: float) -> None:
    """Wait until done(browser) holds, failing at once with the page's error if it reports one."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    def finished(driver: Any) -> bool:
        panel = driver.find_element(By.ID, "operation-panel")
        return bool(done(driver)) or "operation-failed" in (panel.get_attribute("class") or "")

    WebDriverWait(browser, seconds).until(finished)
    panel = browser.find_element(By.ID, "operation-panel")
    if "operation-failed" in (panel.get_attribute("class") or ""):
        pytest.fail(f"the page reported a failed operation: {panel.text}")


@pytest.fixture
def lab_site() -> Iterator[tuple[str, Path]]:
    # qemu runs as its own user and must reach the session disks, so the scratch project
    # lives where real lab sessions do, not in pytest's private temporary directory.
    project = ROOT / "runtime" / "browser-journey" / uuid4().hex
    project.mkdir(parents=True)
    for shared in ("scenarios", "images", "curricula", "mock-exams"):
        (project / shared).symlink_to(ROOT / shared)
    (project / "runtime").mkdir()
    (project / "runtime" / "cache").symlink_to((ROOT / "runtime" / "cache").resolve())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    labctl = Path(sys.executable).with_name("labctl")
    log = project / "labctl-up.log"
    server = subprocess.Popen(
        [str(labctl), "up", "--no-browser", "--port", str(port), "--project-root", str(project)],
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 900  # the first start verifies the image checksum
        while True:
            if server.poll() is not None:
                pytest.fail(f"labctl up exited: {log.read_text()}")
            try:
                with urllib.request.urlopen(f"{url}/scenarios/topic/lfcs", timeout=5) as page:
                    if page.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("labctl up did not serve the site in time")
            time.sleep(2)
        yield url, project / "runtime"
    finally:
        server.terminate()
        server.wait(timeout=30)
        with open_vm_runtime(project / "runtime") as runtime:
            for state in runtime.sessions.list_all():
                if state.status is not SessionStatus.DESTROYED:
                    runtime.scenarios.destroy(state.session_id)
        shutil.rmtree(project)


@pytest.mark.skipif(
    os.environ.get("LAL_RUN_BROWSER_LIVE") != "1" or not (CHROMIUM and CHROMEDRIVER),
    reason="set LAL_RUN_BROWSER_LIVE=1 with chromium and chromedriver installed",
)
def test_a_learner_launches_repairs_checks_and_destroys_a_scenario(
    lab_site: tuple[str, Path],
) -> None:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as condition
    from selenium.webdriver.support.ui import WebDriverWait

    url, runtime_root = lab_site
    options = Options()
    options.binary_location = str(CHROMIUM)
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,1600")
    browser = webdriver.Chrome(service=Service(str(CHROMEDRIVER)), options=options)
    try:
        browser.get(f"{url}/")
        assert browser.current_url.endswith("/scenarios/topic/lfcs")
        manifest = load_manifest(ROOT / "scenarios" / f"{SCENARIO}.yaml")
        assert manifest.title in browser.page_source

        browser.find_element(
            By.CSS_SELECTOR, f'[data-job-endpoint="/api/scenarios/{SCENARIO}/sessions"]'
        ).click()
        wait_for_operation(browser, lambda driver: "/sessions/" in driver.current_url, 900)
        assert "Done means" in browser.page_source

        ssh = browser.find_element(By.ID, "ssh-1").text
        match = re.fullmatch(r"ssh -i (\S+) (\S+)@(\S+)", ssh)
        assert match, ssh
        key, user, address = match.groups()
        endpoint = GuestEndpoint(address, user, Path(key))
        repair = load_action_manifest(ROOT / "scenarios" / manifest.reference_solution)
        ActionRunner(SshGuestExecutor(BoundedSubprocessRunner())).run(
            repair, {host.name: endpoint for host in manifest.topology.hosts}
        )

        hints = browser.find_elements(By.CSS_SELECTOR, ".hint-list li")
        assert hints and not hints[0].is_displayed()
        browser.find_element(By.CSS_SELECTOR, "[data-next-hint]").click()
        assert hints[0].is_displayed() and not hints[1].is_displayed()

        debrief = browser.find_element(By.ID, "debrief")
        assert not debrief.is_displayed()
        browser.find_element(By.XPATH, '//button[text()="Check solution"]').click()
        results = browser.find_element(By.ID, "check-results")
        wait_for_operation(browser, lambda _driver: results.is_displayed(), 900)
        summary = results.find_element(By.CSS_SELECTOR, "strong")
        assert summary.text == (
            "Solved. Every requirement holds, and it survived rebooting node2."
        ), browser.find_element(By.ID, "check-results").text
        WebDriverWait(browser, 10).until(condition.visibility_of(debrief))
        assert "## What was wrong" not in debrief.text and "What was wrong" in debrief.text

        session_id = browser.current_url.rsplit("/", 1)[-1]
        browser.find_element(By.XPATH, '//button[text()="Destroy"]').click()
        WebDriverWait(browser, 10).until(condition.alert_is_present()).accept()
        WebDriverWait(browser, 300).until_not(condition.url_contains(session_id))
    finally:
        browser.quit()

    with open_vm_runtime(runtime_root) as runtime:
        states = {str(state.session_id): state.status for state in runtime.sessions.list_all()}
        progress = {entry.scenario_id: entry for entry in runtime.progress.list_all()}
    assert states[session_id] is SessionStatus.DESTROYED
    assert progress[SCENARIO].solved
