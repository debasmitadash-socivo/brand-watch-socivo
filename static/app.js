/* Brand Watch — dashboard logic.
   Keys live in localStorage only and are sent per-request to the backend. */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const state = {
    profile: null,
    searchQueries: [],
    watchlist: [],     // [{platform, url, why, priority, enabled, custom}]
    results: null,
    charts: {},
    gateIntent: null,  // "run" | "pdf" | "blueprint"
    freemailWarned: false,
  };

  const FREE_MAIL = [
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "hotmail.co.uk", "outlook.com", "live.com", "live.co.uk", "msn.com",
    "aol.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.co.uk", "mail.com", "yandex.com", "zoho.com",
  ];

  /* ---------------------------------------------------------- config */

  const CFG_FIELDS = {
    "cfg-gemini": "bw_gemini_key",
    "cfg-reddit-id": "bw_reddit_id",
    "cfg-reddit-secret": "bw_reddit_secret",
    "cfg-reddit-user": "bw_reddit_user",
    "cfg-website": "bw_website",
    "cfg-industry": "bw_industry",
  };

  function loadConfig() {
    for (const [id, key] of Object.entries(CFG_FIELDS)) {
      $(id).value = localStorage.getItem(key) || "";
      $(id).addEventListener("change", () => localStorage.setItem(key, $(id).value.trim()));
    }
  }

  function keys() {
    return {
      gemini_key: $("cfg-gemini").value.trim(),
      reddit: {
        client_id: $("cfg-reddit-id").value.trim(),
        client_secret: $("cfg-reddit-secret").value.trim(),
        username: $("cfg-reddit-user").value.trim(),
      },
    };
  }

  /* ------------------------------------------------------ custom URLs */

  function addCustomUrlRow(value = "") {
    const row = document.createElement("div");
    row.className = "custom-url-row";
    row.innerHTML = `<input type="text" placeholder="https://www.trustpilot.com/review/…">
                     <button type="button" title="Remove">×</button>`;
    row.querySelector("input").value = value;
    row.querySelector("button").addEventListener("click", () => row.remove());
    $("custom-url-list").appendChild(row);
    return row;
  }

  function customUrls() {
    return [...document.querySelectorAll("#custom-url-list input")]
      .map((i) => i.value.trim())
      .filter(Boolean);
  }

  /* ---------------------------------------------------------- credits */

  async function refreshCredits() {
    try {
      const data = await (await fetch("/api/credits")).json();
      const badge = $("credits-badge");
      if (data.unlocked) {
        badge.textContent = "✓ Unlimited access unlocked";
        badge.classList.add("unlocked");
      } else {
        badge.textContent = `Free analysis runs remaining: ${data.remaining} of 2`;
        badge.classList.remove("unlocked");
      }
      return data;
    } catch {
      $("credits-badge").textContent = "Credits unavailable";
      return { unlocked: false, remaining: 0 };
    }
  }

  /* ------------------------------------------------------- discovery */

  async function discover() {
    const btn = $("btn-discover");
    const errBox = $("discover-error");
    errBox.hidden = true;
    const website = $("cfg-website").value.trim();
    if (!website) return showError(errBox, "Enter your company website URL first.");
    if (!$("cfg-gemini").value.trim())
      return showError(errBox,
        'A Gemini API key is required. <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Get a free key here</a>.');

    btn.disabled = true;
    btn.textContent = "Reading your website…";
    try {
      const resp = await fetch("/api/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...keys(),
          website_url: website,
          industry: $("cfg-industry").value.trim(),
        }),
      });
      const data = await resp.json();
      if (!resp.ok) return showError(errBox, friendlyError(data));
      state.profile = data.profile || {};
      state.searchQueries = data.search_queries || [];
      state.watchlist = (data.watchlist || []).map((w) => ({
        platform: w.platform || "Source",
        url: w.url || "",
        why: w.why || "",
        priority: (w.priority || "medium").toLowerCase(),
        enabled: true,
        custom: false,
      }));
      renderProfile(data.confidence_notes);
      renderWatchlist();
      $("hero-panel").hidden = true;
      $("profile-panel").hidden = false;
      $("watchlist-panel").hidden = false;
      $("watchlist-panel").scrollIntoView({ behavior: "smooth" });
    } catch (e) {
      showError(errBox, "Network error talking to the backend: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Discover my brand →";
    }
  }

  function renderProfile(confidenceNotes) {
    const p = state.profile;
    const items = [
      ["Brand", p.brand_name],
      ["Industry", p.industry],
      ["Business model", p.business_model],
      ["What they do", p.what_they_do],
      ["Target customer", p.target_customer],
      ["Geography", listish(p.geography)],
      ["Aliases", listish(p.aliases_and_misspellings)],
      ["Products", listish(p.products)],
    ].filter(([, v]) => v);
    $("profile-body").innerHTML =
      `<div class="profile-grid">` +
      items.map(([k, v]) =>
        `<div class="profile-item"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`
      ).join("") +
      `</div>`;
    const note = $("confidence-note");
    if (confidenceNotes && String(confidenceNotes).trim()) {
      note.textContent = "Confidence note: " + confidenceNotes;
      note.hidden = false;
    } else note.hidden = true;
  }

  /* ------------------------------------------------------- watchlist */

  function renderWatchlist() {
    const grid = $("watchlist-grid");
    grid.innerHTML = "";
    state.watchlist.forEach((item, idx) => {
      const card = document.createElement("label");
      card.className = "watch-card" + (item.enabled ? "" : " off");
      const prio = ["high", "medium", "low"].includes(item.priority) ? item.priority : "medium";
      card.innerHTML = `
        <input type="checkbox" ${item.enabled ? "checked" : ""}>
        <div>
          <div class="wc-platform">${esc(item.platform)}
            <span class="prio prio-${prio}">${prio}</span>
            ${item.custom ? '<span class="prio prio-low">custom</span>' : ""}
          </div>
          <div class="wc-why">${esc(item.why)}</div>
          <div class="wc-url">${esc(item.url)}</div>
        </div>`;
      card.querySelector("input").addEventListener("change", (e) => {
        item.enabled = e.target.checked;
        card.classList.toggle("off", !item.enabled);
      });
      grid.appendChild(card);
    });
  }

  function addWatchlistUrl() {
    const url = prompt("Paste the full URL of a page to monitor (e.g. your Trustpilot profile):");
    if (!url || !url.trim()) return;
    state.watchlist.push({
      platform: hostOf(url.trim()), url: url.trim(),
      why: "Added by you", priority: "high", enabled: true, custom: true,
    });
    renderWatchlist();
  }

  /* ------------------------------------------------------------- run */

  async function runMonitoring() {
    const errBox = $("run-error");
    errBox.hidden = true;
    const creditsInfo = await refreshCredits();
    if (!creditsInfo.unlocked && creditsInfo.remaining <= 0) {
      openGate("run");
      return;
    }

    // Merge sidebar custom URLs into the approved source list.
    const sources = state.watchlist.filter((w) => w.enabled)
      .map((w) => ({ platform: w.platform, url: w.url }));
    for (const url of customUrls()) {
      if (!sources.some((s) => s.url === url)) sources.push({ platform: hostOf(url), url });
    }

    $("watchlist-panel").hidden = true;
    $("results-panel").hidden = true;
    $("progress-panel").hidden = false;
    $("progress-text").textContent =
      `Monitoring ${state.profile.brand_name || "your brand"} across ${sources.length} approved sources, ` +
      `Reddit and ${state.searchQueries.length} search queries…`;

    try {
      const resp = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...keys(),
          profile: state.profile,
          search_queries: state.searchQueries,
          sources,
        }),
      });
      const data = await resp.json();
      if (resp.status === 402) { backToWatchlist(); openGate("run"); return; }
      if (!resp.ok) { backToWatchlist(); showError(errBox, friendlyError(data)); return; }
      state.results = data;
      renderResults(data);
    } catch (e) {
      backToWatchlist();
      showError(errBox, "The run failed with a network error: " + e.message);
    } finally {
      $("progress-panel").hidden = true;
      refreshCredits();
    }
  }

  function backToWatchlist() {
    $("progress-panel").hidden = true;
    $("watchlist-panel").hidden = false;
  }

  /* --------------------------------------------------------- results */

  const SENT_COLOURS = { positive: "#1d9a6c", neutral: "#9aa7b8", negative: "#d93a3a" };

  function countable(m) {
    return m.relevance !== "excluded" && !m.self_published;
  }

  function renderResults(data) {
    $("results-panel").hidden = false;
    $("watchlist-panel").hidden = false;

    const mentions = data.mentions || [];
    const score = data.score || {};

    // Gauge
    const value = clampScore(score.score);
    $("gauge-score").textContent = value === null ? "–" : Math.round(value);
    $("gauge-band").textContent = score.band || (value === null ? "No data" : bandFor(value));
    $("low-data-badge").hidden = !score.low_data;
    $("exec-summary").textContent = score.executive_summary || (mentions.length
      ? "" : "No mentions were found across the approved sources. Nothing has been fabricated — try adding more specific URLs (e.g. your Trustpilot or G2 profile) and run again.");
    drawGauge(value);

    // Sentiment + platform charts, computed from real mentions only.
    const counted = mentions.filter(countable);
    const sentCounts = { positive: 0, neutral: 0, negative: 0 };
    const platCounts = {};
    for (const m of counted) {
      sentCounts[m.sentiment] = (sentCounts[m.sentiment] || 0) + 1;
      platCounts[m.platform] = (platCounts[m.platform] || 0) + 1;
    }
    drawSentiment(sentCounts);
    drawPlatforms(platCounts);

    renderThemes("complaint-themes", score.complaint_themes, "No complaint themes — not enough negative mentions in the data.");
    renderThemes("praise-themes", score.praise_themes, "No praise themes — not enough positive mentions in the data.");
    renderSignals("urgent-flags", score.urgent_flags, "No urgent flags in this run.");
    renderSignals("quick-wins", score.quick_wins, "No evidence-backed quick wins from this sample.");
    renderExtraNotes(score);
    renderMentions(mentions);
    renderStatuses(data.source_status || [], data.parse_failures || []);

    // Print header lines
    $("print-brand-line").textContent =
      `${state.profile.brand_name || ""} — ${state.profile.industry || ""}`;
    $("print-date-line").textContent =
      "Generated " + new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });

    $("results-panel").scrollIntoView({ behavior: "smooth" });
  }

  function clampScore(v) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : null;
  }

  function bandFor(s) {
    if (s < 40) return "At Risk";
    if (s < 60) return "Mixed";
    if (s < 75) return "Healthy";
    if (s < 90) return "Strong";
    return "Exceptional";
  }

  function gaugeColour(s) {
    if (s === null) return "#9aa7b8";
    if (s < 40) return "#d93a3a";
    if (s < 60) return "#d97f0e";
    if (s < 75) return "#2f6fed";
    return "#1d9a6c";
  }

  function makeChart(id, config) {
    if (state.charts[id]) state.charts[id].destroy();
    config.options = Object.assign({ animation: false, responsive: true }, config.options || {});
    state.charts[id] = new Chart($(id), config);
  }

  function drawGauge(value) {
    const v = value === null ? 0 : value;
    makeChart("gauge-chart", {
      type: "doughnut",
      data: {
        datasets: [{
          data: [v, 100 - v],
          backgroundColor: [gaugeColour(value), "#e3e8ef"],
          borderWidth: 0,
        }],
      },
      options: {
        rotation: -90, circumference: 180, cutout: "72%",
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  }

  function drawSentiment(counts) {
    makeChart("sentiment-chart", {
      type: "doughnut",
      data: {
        labels: ["Positive", "Neutral", "Negative"],
        datasets: [{
          data: [counts.positive, counts.neutral, counts.negative],
          backgroundColor: [SENT_COLOURS.positive, SENT_COLOURS.neutral, SENT_COLOURS.negative],
          borderWidth: 1,
        }],
      },
      options: { plugins: { legend: { position: "bottom" } } },
    });
  }

  function drawPlatforms(counts) {
    const labels = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
    makeChart("platform-chart", {
      type: "bar",
      data: {
        labels,
        datasets: [{ data: labels.map((l) => counts[l]), backgroundColor: "#2f6fed", borderRadius: 5 }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: { x: { ticks: { precision: 0 } } },
      },
    });
  }

  function renderThemes(elId, themes, emptyText) {
    const el = $(elId);
    themes = Array.isArray(themes) ? themes.slice(0, 3) : [];
    if (!themes.length) {
      el.innerHTML = `<p class="empty-note">${esc(emptyText)}</p>`;
      return;
    }
    el.innerHTML = themes.map((t) => `
      <div class="theme-card">
        <div class="tc-head"><span>${esc(t.theme || "Theme")}</span>
          <span class="tc-count">${esc(t.count != null ? t.count + " mentions" : "")}</span></div>
        ${t.example ? `<blockquote>“${esc(t.example)}”</blockquote>` : ""}
        <div class="tc-platforms">${esc(listish(t.platforms))}</div>
      </div>`).join("");
  }

  function renderSignals(elId, items, emptyText) {
    const el = $(elId);
    items = Array.isArray(items) ? items : [];
    el.innerHTML = items.length
      ? items.map((i) => `<li>${esc(typeof i === "string" ? i : JSON.stringify(i))}</li>`).join("")
      : `<li class="empty-note" style="list-style:none">${esc(emptyText)}</li>`;
  }

  function renderExtraNotes(score) {
    const bits = [];
    if (score.employer_sentiment_note)
      bits.push(`<p><strong>Employer sentiment:</strong> ${esc(score.employer_sentiment_note)}</p>`);
    const comp = score.competitor_mentions;
    if (Array.isArray(comp) && comp.length) {
      bits.push("<p><strong>Competitor mentions:</strong></p><ul>" +
        comp.map((c) => `<li>${esc(typeof c === "string" ? c : `${c.competitor || c.name || ""} — ${c.context || ""}`)}</li>`).join("") +
        "</ul>");
    }
    $("extra-notes-panel").hidden = bits.length === 0;
    $("extra-notes").innerHTML = bits.join("");
  }

  function renderMentions(mentions) {
    const body = $("mentions-body");
    if (!mentions.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty-note">0 mentions found across all sources.</td></tr>`;
      return;
    }
    body.innerHTML = mentions.map((m) => {
      const tag = [m.facet !== "customer" ? m.facet : "", m.relevance !== "confirmed" ? m.relevance : ""]
        .filter(Boolean).join(" · ") || "customer";
      const link = m.link && /^https?:\/\//i.test(m.link)
        ? `<a class="view-link" href="${esc(m.link)}" target="_blank" rel="noopener">View original ↗</a>` : "";
      const snippet = m.text.length > 240 ? m.text.slice(0, 237) + "…" : m.text;
      return `<tr>
        <td><span class="platform-tag">${esc(m.platform)}</span></td>
        <td>${esc(snippet)}${m.date_hint ? `<div class="facet-tag">${esc(m.date_hint)}</div>` : ""}</td>
        <td><span class="sent-badge sent-${m.sentiment}">${m.sentiment}</span></td>
        <td><span class="facet-tag">${esc(tag)}</span></td>
        <td class="no-print">${link}</td>
      </tr>`;
    }).join("");
  }

  function renderStatuses(statuses, failures) {
    $("source-status").innerHTML = statuses.map((s) => `
      <div class="status-row">
        <span class="status-dot dot-${esc(s.status)}"></span>
        <span>${esc(s.source)}</span>
        <span class="status-detail">${s.status === "ok"
          ? `fetched ${Number(s.chars || 0).toLocaleString()} chars`
          : esc(s.detail || s.status)}</span>
      </div>`).join("") || `<p class="empty-note">No sources were attempted.</p>`;
    const pf = $("parse-failures");
    if (failures.length) {
      pf.hidden = false;
      pf.innerHTML = "<strong>Skipped chunks:</strong> " + failures.map(esc).join(" · ");
    } else pf.hidden = true;
  }

  /* ------------------------------------------------------- lead gate */

  function openGate(intent) {
    state.gateIntent = intent;
    state.freemailWarned = false;
    $("gate-warn").hidden = true;
    $("gate-error").hidden = true;
    $("gate-title").textContent = intent === "run"
      ? "Unlock unlimited runs" : "Unlock your downloads";
    $("gate-copy").textContent = intent === "run"
      ? "You've used your 2 free analysis runs. Tell us who you are and we'll unlock unlimited monitoring, plus the PDF report and the full source-code blueprint — free."
      : "The PDF report and the source-code blueprint are free — just tell us who you are and they unlock instantly, along with unlimited analysis runs.";
    $("gate-modal").hidden = false;
    $("gate-name").focus();
  }

  async function submitGate() {
    const errBox = $("gate-error");
    const warnBox = $("gate-warn");
    errBox.hidden = true;
    const name = $("gate-name").value.trim();
    const email = $("gate-email").value.trim().toLowerCase();
    const company = $("gate-company").value.trim();

    if (!name || !email || !company)
      return showError(errBox, "All three fields are required.");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email))
      return showError(errBox, "That email address doesn't look valid.");

    const domain = email.split("@")[1];
    if (FREE_MAIL.includes(domain) && !state.freemailWarned) {
      state.freemailWarned = true;
      warnBox.textContent =
        `${domain} looks like a personal address — a business email helps us tailor the report. ` +
        `Click “Unlock free access” again to continue anyway.`;
      warnBox.hidden = false;
      return;
    }

    const resp = await fetch("/api/lead", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name, business_email: email, company_name: company,
        monitored_brand: state.profile ? state.profile.brand_name || "" : "",
      }),
    });
    const data = await resp.json();
    if (!resp.ok) return showError(errBox, friendlyError(data));

    $("gate-modal").hidden = true;
    await refreshCredits();
    if (state.gateIntent === "run") runMonitoring();
    if (state.gateIntent === "pdf") window.print();
    if (state.gateIntent === "blueprint") downloadBlueprint();
    state.gateIntent = null;
  }

  /* ------------------------------------------------------- downloads */

  async function requirePdf() {
    const c = await refreshCredits();
    if (!c.unlocked) return openGate("pdf");
    window.print();
  }

  async function requireBlueprint() {
    const c = await refreshCredits();
    if (!c.unlocked) return openGate("blueprint");
    downloadBlueprint();
  }

  function downloadBlueprint() {
    window.location.href = "/api/blueprint";
  }

  /* ----------------------------------------------------------- utils */

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function listish(v) {
    if (Array.isArray(v)) return v.join(", ");
    if (v && typeof v === "object") return Object.values(v).join(", ");
    return v || "";
  }

  function hostOf(url) {
    try { return new URL(/^https?:/i.test(url) ? url : "https://" + url).hostname.replace(/^www\./, ""); }
    catch { return "Custom URL"; }
  }

  function showError(box, html) {
    box.innerHTML = html;
    box.hidden = false;
  }

  function friendlyError(data) {
    let msg = esc((data && data.error) || "Something went wrong.");
    if (data && data.code === "invalid_key") {
      msg += ' <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Get a free Gemini key →</a>';
    }
    return msg;
  }

  /* ------------------------------------------------------------ init */

  loadConfig();
  refreshCredits();
  $("btn-add-url").addEventListener("click", () => addCustomUrlRow());
  $("btn-discover").addEventListener("click", discover);
  $("btn-watch-add-url").addEventListener("click", addWatchlistUrl);
  $("btn-run").addEventListener("click", runMonitoring);
  $("btn-pdf").addEventListener("click", requirePdf);
  $("btn-blueprint").addEventListener("click", requireBlueprint);
  $("gate-submit").addEventListener("click", submitGate);
  $("gate-cancel").addEventListener("click", () => { $("gate-modal").hidden = true; });
})();
