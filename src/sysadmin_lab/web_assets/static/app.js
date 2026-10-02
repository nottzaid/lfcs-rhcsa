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

function rememberedHints(key) {
  try {
    return Number.parseInt(window.localStorage.getItem(key) || "0", 10) || 0;
  } catch {
    return 0;
  }
}

function rememberHints(key, count) {
  try {
    window.localStorage.setItem(key, String(count));
  } catch {
    // Revealed hints are a convenience; the page works without storage.
  }
}

for (const card of document.querySelectorAll("[data-hints]")) {
  const hints = [...card.querySelectorAll(".hint-list > li")];
  const button = card.querySelector("[data-next-hint]");
  const key = `lab-hints:${card.dataset.hints}`;
  const show = (count) => {
    hints.forEach((hint, index) => { hint.hidden = index >= count; });
    if (count >= hints.length) {
      button.hidden = true;
    } else {
      button.textContent = `Show hint ${count + 1} of ${hints.length}`;
    }
  };
  let revealed = Math.min(rememberedHints(key), hints.length);
  show(revealed);
  button.addEventListener("click", () => {
    revealed += 1;
    rememberHints(key, revealed);
    show(revealed);
  });
}

function revealDebrief({scroll}) {
  const debrief = document.querySelector("#debrief");
  if (!debrief) return;
  debrief.hidden = false;
  for (const offer of document.querySelectorAll("[data-debrief-offer]")) offer.hidden = true;
  if (scroll) debrief.scrollIntoView({behavior: "smooth", block: "start"});
}

for (const button of document.querySelectorAll("[data-reveal-debrief]")) {
  button.addEventListener("click", () => revealDebrief({scroll: true}));
}
document.addEventListener("lab:solved", () => revealDebrief({scroll: false}));

// A timed rehearsal's countdown. The server decides what counts; when time is up the page
// reloads so the score and the "time was up" notice come from it, not from this clock.
for (const clock of document.querySelectorAll("[data-deadline]")) {
  const deadline = Date.parse(clock.dataset.deadline);
  const tick = () => {
    const left = Math.round((deadline - Date.now()) / 1000);
    if (left <= 0) {
      window.location.reload();
      return;
    }
    const hours = Math.floor(left / 3600);
    const minutes = String(Math.floor((left % 3600) / 60)).padStart(2, "0");
    const seconds = String(left % 60).padStart(2, "0");
    clock.textContent = `${hours}:${minutes}:${seconds}`;
    window.setTimeout(tick, 1000);
  };
  tick();
}
