const panel = document.querySelector("#operation-panel");
const panelTitle = document.querySelector("#operation-title");
const panelMessage = document.querySelector("#operation-message");

function setOperation(title, message, failed = false) {
  panel.hidden = false;
  panel.classList.toggle("operation-failed", failed);
  panelTitle.textContent = title;
  panelMessage.textContent = message;
}

function clearOperation() {
  panel.hidden = true;
  panel.classList.remove("operation-failed");
}

function reportList(report) {
  const list = document.createElement("ul");
  for (const result of report.results) {
    const item = document.createElement("li");
    item.className = `result-${result.status}`;
    const label = document.createElement("strong");
    label.textContent = result.description;
    item.append(label);
    if (result.status !== "passed") {
      const message = document.createElement("span");
      message.textContent = result.message;
      item.append(message);
    }
    list.append(item);
  }
  return list;
}

function reportSection(title, report) {
  const section = document.createElement("section");
  section.className = "check-phase";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const score = document.createElement("span");
  score.textContent = `${report.earned_weight}/${report.available_weight}`;
  heading.append(score);
  section.append(heading, reportList(report));
  return section;
}

function checkSummary(result) {
  const hosts = result.reboot_hosts.join(", ");
  if (result.solved) {
    return hosts
      ? `Solved. Every requirement holds, and it survived rebooting ${hosts}.`
      : "Solved. Every requirement holds.";
  }
  if (result.unreachable_host) {
    return `${result.unreachable_host} did not come back after rebooting. Open its console in virt-manager to see why it cannot finish booting, fix it, and check again.`;
  }
  if (result.after_reboot) {
    return `The live state passed, but not everything survived rebooting ${hosts}.`;
  }
  return "Not finished yet.";
}

function renderChecks(result) {
  const target = document.querySelector("#check-results");
  if (!target) return;
  target.hidden = false;
  target.replaceChildren();

  const heading = document.createElement("div");
  heading.className = `check-summary ${result.solved ? "check-pass" : "check-fail"}`;
  const strong = document.createElement("strong");
  strong.textContent = checkSummary(result);
  heading.append(strong);
  target.append(heading);

  const persistent = result.reboot_hosts.length > 0;
  target.append(reportSection(persistent ? "Live state" : "Requirements", result.live));
  if (result.after_reboot) {
    target.append(
      reportSection(`After rebooting ${result.reboot_hosts.join(", ")}`, result.after_reboot),
    );
  }
  if (result.solved) document.dispatchEvent(new CustomEvent("lab:solved"));
}

async function pollJob(jobId, renderMode) {
  for (;;) {
    const response = await fetch(`/api/jobs/${jobId}`, {cache: "no-store"});
    if (!response.ok) throw new Error("The operation status could not be read.");
    const job = await response.json();
    if (job.status === "queued") {
      setOperation("Queued", "The lab is finishing the current local operation.");
    } else if (job.status === "running") {
      setOperation("Working", "Preparing or inspecting the virtual machine. This can take a minute.");
    } else if (job.status === "failed") {
      setOperation("Operation failed", job.error || "The operation failed.", true);
      return;
    } else if (job.status === "succeeded") {
      if (renderMode === "checks") {
        clearOperation();
        renderChecks(job.result);
      } else if (job.result.redirect_url) {
        window.location.assign(job.result.redirect_url);
      } else {
        clearOperation();
      }
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
}

async function runJob(button) {
  const confirmation = button.dataset.jobConfirm;
  if (confirmation && !window.confirm(confirmation)) return;
  const original = button.textContent;
  button.disabled = true;
  setOperation(button.dataset.jobLabel || "Working", "Submitting the operation to the local lab.");
  try {
    const response = await fetch(button.dataset.jobEndpoint, {
      method: "POST",
      headers: {"X-Lab-Request": "browser"},
    });
    const body = await response.json();
    if (response.status === 409 && body.job_id) {
      await pollJob(body.job_id, button.dataset.jobRender);
      return;
    }
    if (!response.ok) throw new Error(body.detail || "The operation could not be started.");
    await pollJob(body.job_id, button.dataset.jobRender);
  } catch (error) {
    setOperation("Operation failed", error.message || "Unexpected browser error.", true);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

for (const button of document.querySelectorAll("[data-job-endpoint]")) {
  button.addEventListener("click", () => runJob(button));
}
