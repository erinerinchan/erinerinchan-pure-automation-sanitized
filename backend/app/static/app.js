const API = "";

const queryInput = document.getElementById("queryInput");
const lookupBtn = document.getElementById("lookupBtn");
const statusText = document.getElementById("statusText");
const metadataPanel = document.getElementById("metadataPanel");
const metaFullscreenBtn = document.getElementById("metaFullscreenBtn");
const metaModal = document.getElementById("metaModal");
const metaModalClose = document.getElementById("metaModalClose");
const metaModalBody = document.getElementById("metaModalBody");

const lnkScopus = document.getElementById("lnkScopus");
const lnkWos = document.getElementById("lnkWos");
const lnkOpenAlex = document.getElementById("lnkOpenAlex");

lookupBtn.addEventListener("click", fetchLookup);
metaFullscreenBtn.addEventListener("click", openMetadataModal);
metaModalClose.addEventListener("click", closeMetadataModal);
metaModal.addEventListener("click", (event) => {
  if (event.target === metaModal) closeMetadataModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !metaModal.hidden) {
    closeMetadataModal();
  }
});

queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    fetchLookup();
  }
});

function selectedQueryType() {
  if (document.getElementById("qtDoi").checked) return "doi";
  if (document.getElementById("qtTitle").checked) return "title";
  return "auto";
}

async function fetchLookup() {
  hideError();

  const query = (queryInput.value || "").trim();
  if (!query) {
    showError("Enter a DOI or title.");
    return;
  }

  statusText.textContent = "Fetching metadata...";
  metadataPanel.innerHTML = "";
  setMetadataExpandable(false);

  const payload = {
    query,
    query_type: selectedQueryType(),
  };

  const res = await fetch(`${API}/api/lookup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const text = await res.text();
    showError(`Lookup failed: ${text}`);
    statusText.textContent = "Lookup failed";
    return;
  }

  const data = await res.json();

  if (data.requires_confirmation) {
    renderCandidates(data.candidates || []);
    statusText.textContent = data.message || "Please confirm candidate";
    renderLinks({});
    return;
  }

  statusText.textContent = `Resolved DOI: ${data.resolved.doi} | Title: ${data.resolved.title} | Journal: ${data.resolved.journal} | Year: ${data.resolved.year}`;
  renderMetadataTable(data.structured_output || "Not available");

  const links = data.links || {};
  renderLinks(links);
}

function renderCandidates(candidates) {
  if (!candidates.length) {
    metadataPanel.innerHTML = '<div class="metaItem">No candidates found.</div>';
    setMetadataExpandable(true);
    return;
  }
  const rows = candidates
    .map((c, idx) => {
      return `${idx + 1}. DOI: ${c.doi || "Not available"}\n   Title: ${c.title || "Not available"}\n   Journal: ${c.journal || "Not available"}\n   Year: ${c.year || "Not available"}\n   Match score: ${c.score}`;
    })
    .join("\n\n");
  metadataPanel.innerHTML = `<pre class="report">${escapeHtml(rows)}</pre>`;
  setMetadataExpandable(true);
}

function renderMetadataTable(reportText) {
  const normalized = (reportText || "").trim();
  if (!normalized || normalized.toLowerCase() === "not available") {
    metadataPanel.innerHTML = '<div class="metaItem">No metadata report available.</div>';
    setMetadataExpandable(true);
    return;
  }

  const rows = parseStructuredReport(normalized);
  if (!rows.length) {
    metadataPanel.innerHTML = `<pre class="report">${escapeHtml(normalized)}</pre>`;
    setMetadataExpandable(true);
    return;
  }

  metadataPanel.innerHTML = buildMetadataTable(rows);
  setMetadataExpandable(true);
}

function parseStructuredReport(text) {
  const lines = text.split("\n");
  const rows = [];
  let section = "Overview";
  let lastRow = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (/^[=-]{5,}$/.test(line)) continue;

    const isLikelySectionHeader =
      line.endsWith(":") &&
      !line.startsWith("-") &&
      !line.startsWith("\u2192") &&
      !/^\d+\./.test(line);

    if (isLikelySectionHeader) {
      section = line.slice(0, -1).trim() || section;
      lastRow = null;
      continue;
    }

    const bulletPair = line.match(/^(?:-|\u2192)\s*([^:]+):\s*(.*)$/);
    if (bulletPair) {
      const row = { section, field: bulletPair[1].trim(), value: bulletPair[2].trim() || "-" };
      rows.push(row);
      lastRow = row;
      continue;
    }

    const pair = line.match(/^([^:]{2,}):\s*(.*)$/);
    if (pair) {
      const row = { section, field: pair[1].trim(), value: pair[2].trim() || "-" };
      rows.push(row);
      lastRow = row;
      continue;
    }

    const orderedItem = line.match(/^\d+\.\s*(.+)$/);
    if (orderedItem) {
      const row = { section, field: "Item", value: orderedItem[1].trim() };
      rows.push(row);
      lastRow = row;
      continue;
    }

    if (lastRow) {
      if (lastRow.value === "-" || !lastRow.value) {
        lastRow.value = line;
      } else {
        lastRow.value = `${lastRow.value}\n${line}`;
      }
    } else {
      const row = { section, field: "Note", value: line };
      rows.push(row);
      lastRow = row;
    }
  }

  return rows;
}

function buildMetadataTable(rows) {
  let section = "";
  const body = rows
    .map((row) => {
      let out = "";
      if (row.section !== section) {
        section = row.section;
        out += `<tr class="metaSectionRow"><th colspan="2">${escapeHtml(section)}</th></tr>`;
      }
      out += `<tr><th scope="row">${escapeHtml(row.field)}</th><td>${escapeHtml(row.value)}</td></tr>`;
      return out;
    })
    .join("");

  return `<div class="metaTableWrap"><table class="metaTable"><tbody>${body}</tbody></table></div>`;
}

function setMetadataExpandable(enabled) {
  metaFullscreenBtn.disabled = !enabled;
}

function openMetadataModal() {
  if (metaFullscreenBtn.disabled || !metadataPanel.innerHTML.trim()) return;
  metaModalBody.innerHTML = metadataPanel.innerHTML;
  metaModal.hidden = false;
  document.body.classList.add("modalOpen");
}

function closeMetadataModal() {
  metaModal.hidden = true;
  document.body.classList.remove("modalOpen");
}

function renderLinks(links) {
  setLink(lnkScopus, links.scopus);
  setLink(lnkWos, links.web_of_science);
  setLink(lnkOpenAlex, links.openalex);
}

function setLink(element, url) {
  if (url) {
    element.href = url;
    element.style.opacity = "1";
    element.style.pointerEvents = "auto";
  } else {
    element.href = "#";
    element.style.opacity = "0.45";
    element.style.pointerEvents = "none";
  }
}

function showError(msg) {
  const box = document.getElementById("errorBox");
  box.hidden = false;
  box.textContent = msg;
}

function hideError() {
  const box = document.getElementById("errorBox");
  box.hidden = true;
  box.textContent = "";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
