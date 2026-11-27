"use strict";

const byId = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
const query = byId("query");
const places = byId("places");
const scanButton = byId("scan-button");
const status = byId("status");
const progress = byId("progress");
const download = byId("download");
const filter = byId("filter");
let rows = [];
let busy = false;
let lookupPending = false;
let selectedQuery = "";
let pollTimer;

async function api(path, body) {
  const options = body ? {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-Local-Token": token},
    body: JSON.stringify(body)
  } : {};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

function ready() {
  const selectedType = document.querySelector('#scan input[name="kind"]:checked');
  scanButton.disabled = busy || lookupPending || !places.value || !selectedType || query.value.trim() !== selectedQuery;
}

function message(value, error = false) {
  const element = byId("search-message");
  element.textContent = value;
  element.classList.toggle("error", error);
}

byId("lookup").addEventListener("submit", async (event) => {
  event.preventDefault();
  const term = query.value.trim();
  if (term.length < 2) return;
  lookupPending = true;
  byId("lookup-button").disabled = true;
  query.readOnly = true;
  byId("location-picker").hidden = true;
  places.replaceChildren();
  ready();
  message("Looking up city boundaries…");
  try {
    const matches = await api("/api/locations", {query: term});
    selectedQuery = term;
    for (const match of matches) places.append(new Option(match.label, match.id));
    byId("location-picker").hidden = matches.length === 0;
    message(matches.length ? "" : "No searchable boundary found. Try a nearby municipality or add the country.");
  } catch (error) {
    message(error.message, true);
  } finally {
    lookupPending = false;
    byId("lookup-button").disabled = false;
    query.readOnly = false;
    ready();
  }
});

query.addEventListener("input", () => {
  byId("location-picker").hidden = !places.value || query.value.trim() !== selectedQuery;
  message("");
  ready();
});
byId("scan").addEventListener("change", ready);
byId("scan").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (scanButton.disabled) return;
  const kinds = [...document.querySelectorAll('#scan input[name="kind"]:checked')].map((item) => item.value);
  busy = true;
  ready();
  try {
    await api("/api/scan", {place: Number(places.value), kinds});
    byId("results").hidden = false;
    status.textContent = "Finding matching clinic listings…";
    await poll();
  } catch (error) {
    busy = false;
    message(error.message, true);
    ready();
  }
});

function addLink(parent, url, label) {
  if (!url) return;
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) return;
    const anchor = document.createElement("a");
    anchor.href = parsed.href;
    anchor.textContent = label;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    parent.append(anchor);
  } catch {
    // Ignore malformed URLs in source records.
  }
}

function cell(row, value, source) {
  const td = row.insertCell();
  td.textContent = value || "—";
  if (source) addLink(td.appendChild(document.createElement("small")), source, "Source");
  return td;
}

function render() {
  const term = filter.value.trim().toLowerCase();
  const matched = rows.filter((row) =>
    [row.name, row.type, row.address, row.email, row.phone, row.fax].join(" ").toLowerCase().includes(term));
  const fragment = document.createDocumentFragment();
  for (const clinic of matched.slice(0, 500)) {
    const row = document.createElement("tr");
    const name = cell(row, clinic.name);
    const address = document.createElement("small");
    address.textContent = [clinic.type, clinic.address].filter(Boolean).join(" · ");
    name.append(address);
    addLink(name.appendChild(document.createElement("small")), clinic.website, "Clinic website");
    cell(row, clinic.phone, clinic.phone_source);
    cell(row, clinic.fax, clinic.fax_source);
    cell(row, clinic.email, clinic.email_source);
    const scan = cell(row, clinic.scan_status);
    addLink(scan.appendChild(document.createElement("small")), clinic.contact_form, "Contact form");
    fragment.append(row);
  }
  byId("rows").replaceChildren(fragment);
  byId("visible").textContent = matched.length > 500
    ? `Showing 500 of ${matched.length} matches. The CSV includes all results.`
    : `${matched.length} of ${rows.length} clinics shown.`;
  byId("table-wrap").hidden = matched.length === 0;
}

filter.addEventListener("input", render);

async function poll() {
  clearTimeout(pollTimer);
  try {
    const job = await api("/api/status");
    if (!job) return;
    byId("results").hidden = false;
    busy = ["discovering", "scanning"].includes(job.phase);
    progress.hidden = !busy;
    download.hidden = job.phase !== "done" || job.total === 0;
    byId("tools").hidden = download.hidden;
    byId("table-wrap").hidden = download.hidden;
    status.classList.toggle("error", job.phase === "error");
    if (job.phase === "discovering") {
      progress.removeAttribute("value");
      status.textContent = job.discovery_attempt > 1
        ? `Trying the backup map server for ${job.place}…`
        : `Finding matching clinics in ${job.place}…`;
    } else if (job.phase === "scanning") {
      progress.max = job.total || 1;
      progress.value = job.completed;
      status.textContent = `Checked ${job.completed} of ${job.total} clinics in ${job.place}.`;
    } else if (job.phase === "done") {
      status.textContent = job.total ? `${job.total} clinics found in ${job.place}.` : `No matching clinics were listed in ${job.place}.`;
      rows = job.rows;
      if (rows.length) render();
    } else {
      status.textContent = job.error;
    }
    ready();
    if (busy) pollTimer = setTimeout(poll, 1000);
  } catch {
    if (busy) {
      status.textContent = "Connection interrupted. Reconnecting…";
      pollTimer = setTimeout(poll, 3000);
    }
  }
}
poll();
