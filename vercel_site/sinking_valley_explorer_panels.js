(function () {
  const STATUS_COLORS = {
    approved: "#004655",
    under_review: "#c8922a",
    inadequate: "#c0392b",
    inadequate_under_review: "#c0392b",
    state_intervention: "#6b1d1d",
    incomplete: "#888888",
    pre_sgma: "#cccccc",
    unknown: "#bbbbbb",
  };

  const METRIC_LABELS = {
    gwe_cumulative_drop: "Water table vs pre-2016 baseline",
    gwe_trend_4yr_ft_yr: "Water table trend (4-yr, ft/yr)",
    fallow_pct: "Fallowed land (%)",
    well_reports: "Dry wells (cumulative)",
    total_ag_acres: "Cropland acres (Land IQ)",
    large_farm_share: "Large-farm share (NASS %)",
  };

  const CLOSE_METRIC_ORDER = [
    "gwe_cumulative_drop",
    "gwe_trend_4yr_ft_yr",
    "fallow_pct",
    "well_reports",
    "total_ag_acres",
    "large_farm_share",
  ];

  const CLOSE_METRIC_HIDE = new Set(["gwe_trend_ft_yr", "gwe_trend_5yr_ft_yr"]);

  const METRIC_HINTS = {
    gwe_cumulative_drop: "Is the table higher or lower than its pre-2016 average? Above baseline = higher (better). Below = deeper (worse). Not pumping volume.",
    gwe_trend_4yr_ft_yr: "+ = table falling · − = table rising. Compares 4-yr average rates in each year.",
  };

  function formatGweVsBaseline(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const n = Math.abs(v);
    const s = n >= 100 ? n.toFixed(0) : n.toFixed(1);
    if (v > 0) return `${s} ft below baseline`;
    if (v < 0) return `${s} ft above baseline`;
    return "At pre-2016 baseline";
  }

  function isTrendKey(k) {
    return k === "gwe_trend_4yr_ft_yr" || k === "gwe_trend_5yr_ft_yr" || k === "gwe_trend_ft_yr";
  }

  function formatTrendValue(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const n = Math.abs(v);
    const s = n >= 100 ? n.toFixed(0) : n.toFixed(1);
    if (v > 0.05) return `falling ${s} ft/yr`;
    if (v < -0.05) return `rising ${s} ft/yr`;
    return "near flat";
  }

  function formatMetricValue(k, v) {
    if (k === "gwe_cumulative_drop") return formatGweVsBaseline(v);
    if (isTrendKey(k)) return formatTrendValue(v);
    return fmt(v, k);
  }

  function formatTrendDelta(a0, b, d) {
    const val = fmt(Math.abs(d), "gwe_trend_4yr_ft_yr");
    if (a0 != null && b != null) {
      const aRise = a0 < -0.05;
      const aFall = a0 > 0.05;
      const bRise = b < -0.05;
      const bFall = b > 0.05;
      if (aRise && bRise) {
        if (d > 0) return `Rise slowed by ${val} ft/yr — still recovering in 2024`;
        if (d < 0) return `Rising ${val} ft/yr faster than in 2016`;
        return "Same recovery rate";
      }
      if (aFall && bFall) {
        if (d > 0) return `Falling ${val} ft/yr faster than in 2016`;
        if (d < 0) return `Falling ${val} ft/yr less than in 2016`;
        return "Same decline rate";
      }
      if (aFall && bRise) return `Shifted from falling to rising (${val} ft/yr change)`;
      if (aRise && bFall) return `Shifted from rising to falling (${val} ft/yr change)`;
    }
    if (d > 0) return `+${val} ft/yr vs 2016`;
    if (d < 0) return `${val} ft/yr vs 2016`;
    return "No change vs 2016";
  }

  function trendDeltaClass(a0, b, d) {
    if (d == null || d === 0) return "neutral";
    if (a0 == null || b == null) return d < 0 ? "good" : "bad";
    const aRise = a0 < -0.05;
    const aFall = a0 > 0.05;
    const bRise = b < -0.05;
    const bFall = b > 0.05;
    if (aRise && bRise) return d < 0 ? "good" : "neutral";
    if (aFall && bFall) return d < 0 ? "good" : "bad";
    if (aFall && bRise) return "good";
    if (aRise && bFall) return "bad";
    if (bRise) return "good";
    if (bFall) return "bad";
    return "neutral";
  }

  function formatMetricDelta(k, d, endVal, startVal) {
    if (d == null) return "—";
    const val = fmt(Math.abs(d), k);
    if (k === "gwe_cumulative_drop") {
      if (d > 0) return `Water table ${val} ft lower in 2024 than in 2016`;
      if (d < 0) return `Water table ${val} ft higher in 2024 than in 2016`;
      return "No change vs 2016";
    }
    if (isTrendKey(k)) return formatTrendDelta(startVal, endVal, d);
    return `Δ ${val}`;
  }

  function formatGweChange(d) {
    if (d == null || Number.isNaN(d)) return "—";
    const n = Math.abs(d);
    const s = n >= 100 ? n.toFixed(0) : n.toFixed(1);
    if (d > 0) return `${s} ft lower in 2024 than in 2016`;
    if (d < 0) return `${s} ft higher in 2024 than in 2016`;
    return "No change vs 2016";
  }

  function relPairedHint(vdef) {
    if (!vdef) return "";
    if (vdef.chart_mode === "wells_vs_gwe") {
      return "Each dot = one GSP. Upper-right = more reports where groundwater remains depleted.";
    }
    if (vdef.id === "gwe_cumulative_drop") {
      return "Dashed line = no change. Below the line = water table higher in 2024. Axis: + = below baseline (deeper), − = above (higher).";
    }
    return "Dashed line = no change. Axis labels on chart.";
  }

  function relSummaryLines(cat, varId, vdef) {
    let improved = 0;
    let worsened = 0;
    cat.forEach((g) => {
      const good = metricImproved(g, varId, vdef);
      if (good === true) improved += 1;
      else if (good === false) worsened += 1;
    });
    const approved = cat.filter((g) => g.compliant);
    let ap = 0;
    approved.forEach((g) => {
      if (metricImproved(g, varId, vdef) === true) ap += 1;
    });

    const metricName = vdef?.label || "this metric";
    let countLine;
    if (vdef?.id === "gwe_cumulative_drop") {
      countLine = `<span class="good-text">${improved} higher in 2024</span> · <span class="bad-text">${worsened} lower in 2024</span> (vs 2016 water table).`;
    } else {
      countLine = `<span class="good-text">${improved} improved</span> · <span class="bad-text">${worsened} worsened</span> (2016→2024, ${metricName}).`;
    }

    return `
      <p><strong>${cat.length}</strong> GSPs with data · ${countLine}</p>
      <p>Among <strong>${approved.length} approved</strong> GSPs, <strong>${ap}</strong> show improvement on this metric.
      This is descriptive only — not causal proof of SGMA impact.</p>`;
  }

  let DATA = null;
  let chartHits = { paired: [], delta: [] };

  function waitForData(cb) {
    if (window.SinkingValleyExplorer?.DATA) {
      DATA = window.SinkingValleyExplorer.DATA;
      cb();
      return;
    }
    setTimeout(() => waitForData(cb), 100);
  }

  function fmt(v, key) {
    if (v == null || Number.isNaN(v)) return "—";
    if (key === "total_ag_acres") return Math.round(v).toLocaleString();
    if (key === "well_reports") return Math.round(v).toLocaleString();
    return typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)) : String(v);
  }

  function switchTab(tabId) {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tabId}`));
    if (tabId === "relationships") drawRelationshipCharts();
    if (tabId === "explorer") window.SinkingValleyExplorer?.resizeMaps?.();
  }

  function initTabs() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    document.querySelectorAll("[data-goto-tab]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        switchTab(el.dataset.gotoTab);
      });
    });
  }

  function renderIntroPage() {
    const intro = DATA.intro_page;
    if (!intro) return;
    const stats = intro.stats || {};

    const hero = document.querySelector(".intro-hero-photo");
    if (hero && intro.hero_image) {
      hero.style.backgroundImage = `url('${intro.hero_image}')`;
    }

    const stakesEl = document.getElementById("intro-sgma-stakes");
    if (stakesEl && intro.sgma_stakes) stakesEl.textContent = intro.sgma_stakes;

    const subsCallout = document.getElementById("intro-subsidence-callout");
    const callout = intro.subsidence_callout;
    if (subsCallout && callout) {
      if (typeof callout === "object") {
        subsCallout.innerHTML = `
          <p class="intro-subsidence-callout-head">${callout.headline || ""}</p>
          <p class="intro-subsidence-callout-body">${callout.body || ""}</p>`;
      } else {
        subsCallout.innerHTML = callout;
      }
    }

    const mechanismEl = document.getElementById("intro-subsidence-mechanism");
    const mechanism = intro.subsidence_mechanism;
    if (mechanismEl && mechanism?.src) {
      mechanismEl.innerHTML = `
        <img src="${mechanism.src}" alt="${mechanism.alt || ""}" loading="lazy"/>
        ${mechanism.caption ? `<figcaption>${mechanism.caption}</figcaption>` : ""}`;
    }

    const metricsEl = document.getElementById("intro-metrics");
    if (metricsEl) {
      const boxes = intro.stat_boxes || [];
      metricsEl.innerHTML = boxes.map((m) => `
        <div class="intro-metric">
          <span class="val">${m.val}</span>
          <span class="lbl">${m.lbl}</span>
        </div>`).join("");
    }

    const figEl = document.getElementById("intro-subsidence-fig");
    const fig = intro.subsidence_figure;
    if (figEl && fig?.src) {
      figEl.innerHTML = `
        <img src="${fig.src}" alt="${fig.alt || ""}" loading="lazy"/>
        ${fig.caption ? `<figcaption>${fig.caption}</figcaption>` : ""}`;
    }

    const tilesEl = document.getElementById("intro-impact-tiles");
    if (tilesEl) {
      tilesEl.innerHTML = (intro.impact_tiles || []).map((t) => `
        <article class="impact-tile"><h3>${t.title}</h3><p>${t.text}</p></article>`).join("");
    }

    initQuoteSlideshow(intro.quotes || []);

    const smcEl = document.getElementById("intro-smc");
    if (smcEl) {
      smcEl.innerHTML = (intro.smc || []).map((s) => `
        <div class="smc-bubble"><span class="smc-num">${s.label}</span><span class="smc-desc">${s.desc}</span></div>`).join("");
    }

    const glossEl = document.getElementById("intro-glossary");
    if (glossEl) {
      glossEl.innerHTML = (intro.glossary || []).map((g) => `
        <dt>${g.term}</dt><dd>${g.def}</dd>`).join("");
    }
  }

  function formatQuoteParas(text) {
    const paras = String(text || "").split("\n\n").map((p) => p.trim()).filter(Boolean);
    if (!paras.length) return "";
    const opensQuote = (s) => s.startsWith('"') || s.startsWith("\u201c");
    const closesQuote = (s) => s.endsWith('"') || s.endsWith("\u201d");
    if (opensQuote(paras[0]) && closesQuote(paras[paras.length - 1])) {
      return paras.map((t) => `<p>${t}</p>`).join("");
    }
    if (paras.length === 1) {
      return `<p>&ldquo;${paras[0]}&rdquo;</p>`;
    }
    return paras.map((t, i) => {
      if (i === 0) return `<p>&ldquo;${t}</p>`;
      if (i === paras.length - 1) return `<p>${t}&rdquo;</p>`;
      return `<p>${t}</p>`;
    }).join("");
  }

  function initQuoteSlideshow(quotes) {
    const root = document.getElementById("intro-quotes-slideshow");
    if (!root || !quotes.length) return;
    let idx = 0;

    root.innerHTML = `
      <button type="button" class="quote-nav quote-nav-prev" aria-label="Previous quote">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      <div class="quote-slideshow-stage" aria-live="polite"></div>
      <button type="button" class="quote-nav quote-nav-next" aria-label="Next quote">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>`;
    const stage = root.querySelector(".quote-slideshow-stage");
    const prevBtn = root.querySelector(".quote-nav-prev");
    const nextBtn = root.querySelector(".quote-nav-next");

    function render() {
      const q = quotes[idx];
      const paras = formatQuoteParas(q.text);
      stage.innerHTML = `
        <article class="quote-slide ${q.type || ""}">
          <img class="quote-slide-img" src="${q.image || ""}" alt="" loading="lazy"/>
          <div class="quote-slide-copy">
            <blockquote>${paras}</blockquote>
            <cite>${q.author}${q.source_url ? ` · <a href="${q.source_url}" target="_blank" rel="noopener noreferrer">Source</a>` : ""}</cite>
          </div>
        </article>`;
    }

    function go(delta) {
      idx = (idx + delta + quotes.length) % quotes.length;
      stage.classList.add("is-fading");
      window.setTimeout(() => {
        render();
        stage.classList.remove("is-fading");
      }, 180);
    }

    prevBtn.addEventListener("click", () => go(-1));
    nextBtn.addEventListener("click", () => go(1));
    root.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    });
    render();
  }

  function renderSourcesPage() {
    const page = DATA.sources_page;
    if (!page) return;
    const introEl = document.getElementById("sources-intro");
    const listEl = document.getElementById("sources-list");
    if (introEl) introEl.textContent = page.intro || "";
    if (listEl) {
      listEl.innerHTML = (page.items || []).map((item) => `
        <li><a href="${item.url}" target="_blank" rel="noopener">${item.label}</a></li>`).join("");
    }
  }

  function renderAnalysisPage() {
    const page = DATA.econometrics_page;
    if (!page) return;
    const ledeEl = document.getElementById("analysis-lede");
    const caveatEl = document.getElementById("analysis-caveat");
    const highlightsEl = document.getElementById("analysis-highlights");
    const figuresEl = document.getElementById("analysis-figures");
    if (ledeEl) ledeEl.textContent = page.lede || "";
    if (caveatEl) caveatEl.textContent = page.caveat || "";
    if (highlightsEl) {
      highlightsEl.innerHTML = (page.highlights || []).map((h) => `
        <div class="analysis-highlight">
          <span class="hl-label">${h.label || ""}</span>
          <p class="hl-text">${h.text || ""}</p>
        </div>`).join("");
      highlightsEl.hidden = !(page.highlights || []).length;
    }
    if (figuresEl) {
      figuresEl.innerHTML = (page.figures || []).map((f) => `
        <figure class="analysis-figure" id="analysis-${f.id || ""}">
          <div class="analysis-figure-head">
            <h2>${f.title || ""}</h2>
            ${f.tag ? `<span class="analysis-figure-tag">${f.tag}</span>` : ""}
          </div>
          <img src="${f.src}" alt="${f.title || "Analysis chart"}" loading="lazy"/>
          <figcaption>${f.caption || ""}</figcaption>
        </figure>`).join("");
    }
    document.querySelectorAll("#tab-analysis [data-goto-tab]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        switchTab(el.dataset.gotoTab);
      });
    });
  }

  function renderTakeawaysPage() {
    const page = DATA.takeaways_page;
    if (!page) return;
    const ledeEl = document.getElementById("takeaways-lede");
    const answerEl = document.getElementById("takeaways-sgma-answer");
    const sectionsEl = document.getElementById("takeaways-sections");
    if (ledeEl) ledeEl.textContent = page.lede || "";
    if (answerEl) {
      const ans = page.sgma_answer;
      if (ans?.text) {
        answerEl.hidden = false;
        answerEl.innerHTML = ans.question
          ? `<strong>${ans.question}</strong> ${ans.text}`
          : ans.text;
      } else {
        answerEl.hidden = true;
        answerEl.textContent = "";
      }
    }
    if (!sectionsEl) return;
    sectionsEl.innerHTML = (page.sections || []).map((s) => `
      <section class="takeaway-section" id="takeaway-${s.id || ""}">
        <h2>${s.title || ""}</h2>
        ${s.focus ? `<p class="takeaway-focus">${s.focus}</p>` : ""}
        ${s.body ? `<p class="takeaway-body">${s.body}</p>` : ""}
        ${(s.stats || []).length ? `
          <div class="takeaway-stats">
            ${(s.stats || []).map((m) => `
              <div class="takeaway-stat${m.compliant ? " takeaway-stat-compliant" : ""}">
                <span class="val">${m.val}</span>
                <span class="lbl">${m.lbl}</span>
              </div>`).join("")}
          </div>` : ""}
        ${s.sgma_takeaway ? `<p class="takeaway-sgma-verdict">${s.sgma_takeaway}</p>` : ""}
        ${(s.bullets || []).length ? `
          <ul class="takeaway-bullets">
            ${(s.bullets || []).map((b) => `<li>${b}</li>`).join("")}
          </ul>` : ""}
        ${s.explore_tab ? `<p class="takeaway-explore"><a href="#" class="intro-cta inline-cta" data-goto-tab="${s.explore_tab}">Explore in ${s.explore_tab === "explorer" ? "map" : s.explore_tab.replace(/_/g, " ")} →</a></p>` : ""}
      </section>`).join("");
    sectionsEl.querySelectorAll("[data-goto-tab]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        switchTab(el.dataset.gotoTab);
      });
    });
  }

  function sjvCatalog() {
    return (DATA.gsp_catalog || []).filter((g) => g.is_sjv !== false);
  }

  function filteredCatalog(approvedOnly) {
    const cat = sjvCatalog();
    if (approvedOnly) return cat.filter((g) => g.compliant);
    return cat;
  }

  function populateCloseSelect() {
    const sel = document.getElementById("close-gsp-select");
    const approvedOnly = document.getElementById("close-approved-only")?.checked;
    const list = filteredCatalog(approvedOnly);
    const prev = sel.value;
    sel.innerHTML = "";
    list.forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g.gsp_id;
      opt.textContent = `${g.label} (GSP ${g.gsp_id}) — ${g.status_2024}`;
      sel.appendChild(opt);
    });
    if (list.some((g) => g.gsp_id === prev)) sel.value = prev;
    else if (list.length) sel.value = list[0].gsp_id;
    renderCloseView();
  }

  function renderCloseView() {
    const gid = document.getElementById("close-gsp-select")?.value;
    const g = sjvCatalog().find((x) => x.gsp_id === gid);
    const meta = document.getElementById("close-gsp-meta");
    const det = document.getElementById("close-assessment");
    const grid = document.getElementById("close-metrics");
    if (!g || !grid) return;

    const sc = STATUS_COLORS[g.status_2024] || STATUS_COLORS.unknown;
    meta.innerHTML = `
      <span class="status-pill" style="background:${sc}">${g.status_2024.replace(/_/g, " ")}</span>
      ${g.status_note ? `<span class="close-status-date">${g.status_note}</span>` : ""}
      <span>GSP ${g.gsp_id}</span>
      <span>${formatGweChange(g.sgma_era_gwe_drop_ft)}</span>
    `;

    const a = g.assessment || {};
    const tones = a.tones || {};
    const verdict = document.getElementById("close-verdict");
    if (verdict) {
      verdict.textContent = a.sgma_help || "Unclear";
      verdict.className = `verdict-text verdict-${a.sgma_tone || "neutral"}`;
    }
    if (det) {
      det.innerHTML = `
        <div class="assess-chip tone-${tones.overdraft || "neutral"}"><span class="assess-k">Water table</span><span class="assess-v">${a.overdraft || "—"}</span></div>
        <div class="assess-chip tone-${tones.trend || "neutral"}"><span class="assess-k">4-yr trend</span><span class="assess-v">${a.trend || "—"}</span></div>
        <div class="assess-chip"><span class="assess-k">Ag</span><span class="assess-v">${a.ag || "—"}</span></div>
        <div class="assess-chip"><span class="assess-k">Residents</span><span class="assess-v">${a.residents || "—"}</span></div>
      `;
    }

    const keys = CLOSE_METRIC_ORDER.filter((k) => METRIC_LABELS[k] && g.metrics[k] && !CLOSE_METRIC_HIDE.has(k));
    grid.innerHTML = keys.map((k) => {
      const m = g.metrics[k] || {};
      const a0 = m["2016"];
      const b = m["2024"];
      const d = m.delta;
      const vdef = (DATA.relationship_variables || []).find((v) => (v.metric_key || v.id) === k || v.id === k);
      const lowerBetter = vdef ? vdef.lower_better : ["gwe_cumulative_drop", "gwe_trend_4yr_ft_yr", "gwe_trend_ft_yr", "gwe_trend_5yr_ft_yr", "well_reports", "large_farm_share"].includes(k);
      let deltaClass = "neutral";
      if (d != null && d !== 0) {
        if (isTrendKey(k)) {
          deltaClass = trendDeltaClass(a0, b, d);
        } else {
          const improved = lowerBetter ? d < 0 : d > 0;
          deltaClass = improved ? "good" : "bad";
        }
      }
      const maxVal = Math.max(Math.abs(a0 || 0), Math.abs(b || 0), 1);
      const pctA = a0 != null ? (Math.abs(a0) / maxVal) * 100 : 0;
      const pctB = b != null ? (Math.abs(b) / maxVal) * 100 : 0;
      return `
        <div class="metric-card">
          <h4>${METRIC_LABELS[k]}</h4>
          ${METRIC_HINTS[k] ? `<p class="metric-hint">${METRIC_HINTS[k]}</p>` : ""}
          <div class="metric-compare">
            <div class="metric-row">
              <span class="metric-yr">2016</span>
              <div class="metric-row-main">
                <div class="metric-bar"><div class="bar-a" style="width:${pctA}%"></div></div>
                <span class="metric-val">${formatMetricValue(k, a0)}</span>
              </div>
            </div>
            <div class="metric-row">
              <span class="metric-yr">2024</span>
              <div class="metric-row-main">
                <div class="metric-bar"><div class="bar-b" style="width:${pctB}%"></div></div>
                <span class="metric-val">${formatMetricValue(k, b)}</span>
              </div>
            </div>
          </div>
          <p class="metric-delta ${deltaClass}">${formatMetricDelta(k, d, b, a0)}</p>
        </div>`;
    }).join("");
  }

  function populateRelSelect() {
    const sel = document.getElementById("rel-variable-select");
    if (!sel || sel.options.length) return;
    (DATA.relationship_variables || []).forEach((v) => {
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.textContent = v.label;
      sel.appendChild(opt);
    });
  }

  function bindPanelEvents() {
    document.getElementById("close-gsp-select")?.addEventListener("change", renderCloseView);
    document.getElementById("close-approved-only")?.addEventListener("change", populateCloseSelect);
    document.getElementById("rel-variable-select")?.addEventListener("change", drawRelationshipCharts);
    document.getElementById("rel-filter-select")?.addEventListener("change", drawRelationshipCharts);

    ["rel-paired-canvas", "rel-delta-canvas"].forEach((id) => {
      const canvas = document.getElementById(id);
      if (!canvas) return;
      canvas.addEventListener("mousemove", (e) => showChartTooltip(e, id === "rel-paired-canvas" ? "paired" : "delta"));
      canvas.addEventListener("mouseleave", hideChartTooltip);
    });
  }

  function relCatalog() {
    const f = document.getElementById("rel-filter-select")?.value || "all";
    let cat = sjvCatalog();
    if (f === "approved") cat = cat.filter((g) => g.compliant);
    else if (f === "regulated") cat = cat.filter((g) => g.regulated);
    return cat;
  }

  function metricKey(vdef) {
    return vdef?.metric_key || vdef?.id;
  }

  function hideChartTooltip() {
    const tip = document.getElementById("rel-tooltip");
    if (tip) tip.classList.add("hidden");
  }

  function showChartTooltip(evt, kind) {
    const tip = document.getElementById("rel-tooltip");
    const hits = chartHits[kind] || [];
    const rect = evt.target.getBoundingClientRect();
    const x = evt.clientX - rect.left;
    const y = evt.clientY - rect.top;
    const hit = hits.find((h) => Math.hypot(h.x - x, h.y - y) <= (h.r || 8));
    if (!tip || !hit) {
      hideChartTooltip();
      return;
    }
    tip.textContent = hit.label;
    tip.style.left = `${evt.clientX + 12}px`;
    tip.style.top = `${evt.clientY + 12}px`;
    tip.classList.remove("hidden");
  }

  function drawRelationshipCharts() {
    const varId = document.getElementById("rel-variable-select")?.value;
    const vdef = (DATA.relationship_variables || []).find((v) => v.id === varId);
    const note = document.getElementById("rel-variable-note");
    const caveat = document.getElementById("rel-caveat");
    const gweNote = document.getElementById("rel-gwe-note");
    const pairedTitle = document.getElementById("rel-paired-title");
    const pairedHint = document.getElementById("rel-paired-hint");
    const deltaTitle = document.getElementById("rel-delta-title");

    if (note && vdef) note.textContent = vdef.note || "";
    if (caveat) {
      caveat.textContent = vdef?.caveat || "";
      caveat.classList.toggle("hidden", !vdef?.caveat);
    }
    if (gweNote) {
      const show = vdef?.gwe_context;
      gweNote.textContent = show
        ? "Groundwater context: check each GSP's cumulative GWE drop in Close view — adjustment metrics mean different things depending on whether overdraft is easing."
        : "";
      gweNote.classList.toggle("hidden", !show);
    }
    if (pairedTitle && vdef) {
      pairedTitle.textContent = vdef.chart_mode === "wells_vs_gwe"
        ? "Dry wells vs groundwater stress (2024)"
        : "2016 vs 2024 (paired)";
    }
    if (pairedHint && vdef) pairedHint.textContent = relPairedHint(vdef);
    const goodLabel = document.getElementById("rel-good-label");
    const badLabel = document.getElementById("rel-bad-label");

    if (deltaTitle && vdef) {
      deltaTitle.textContent = vdef.id === "gwe_cumulative_drop"
        ? "Water table change (2024 − 2016)"
        : "SGMA-era change (2024 − 2016)";
    }
    if (goodLabel && vdef) {
      goodLabel.textContent = vdef.id === "gwe_cumulative_drop"
        ? "BETTER: water table higher in 2024 — below diagonal"
        : `BETTER: ${vdef.good_short || "—"}`;
    }
    if (badLabel && vdef) {
      badLabel.textContent = vdef.id === "gwe_cumulative_drop"
        ? "WORSE: water table lower in 2024 — above diagonal"
        : `WORSE: ${vdef.bad_short || "—"}`;
    }

    const mk = metricKey(vdef);
    const cat = relCatalog().filter((g) => {
      if (vdef?.chart_mode === "wells_vs_gwe") {
        const w = g.metrics.well_reports?.["2024"];
        const gw = g.metrics.gwe_cumulative_drop?.["2024"];
        return w != null && gw != null;
      }
      const m = g.metrics[mk];
      return m && m["2016"] != null && m["2024"] != null;
    });

    if (vdef?.chart_mode === "wells_vs_gwe") drawWellsGweScatter(cat);
    else drawPairedScatter(cat, mk, vdef);
    drawDeltaBars(cat, mk, vdef);
    renderRelSummary(cat, mk, vdef);
  }

  function drawWellsGweScatter(cat) {
    const canvas = document.getElementById("rel-paired-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    chartHits.paired = [];
    const pad = { l: 52, r: 16, t: 24, b: 48 };

    if (!cat.length) {
      ctx.fillStyle = "#888";
      ctx.font = "13px sans-serif";
      ctx.fillText("No GSPs with data.", pad.l, h / 2);
      return;
    }

    const xs = cat.map((g) => g.metrics.gwe_cumulative_drop["2024"]);
    const ys = cat.map((g) => g.metrics.well_reports["2024"]);
    const xmin = Math.min(...xs);
    const xmax = Math.max(...xs);
    const ymin = Math.min(...ys);
    const ymax = Math.max(...ys);
    const xspan = Math.max(xmax - xmin, 1e-6);
    const yspan = Math.max(ymax - ymin, 1e-6);
    const sx = (v) => pad.l + ((v - xmin) / xspan) * (w - pad.l - pad.r);
    const sy = (v) => h - pad.b - ((v - ymin) / yspan) * (h - pad.t - pad.b);

    ctx.strokeStyle = "#ccc";
    ctx.beginPath();
    ctx.moveTo(pad.l, pad.t);
    ctx.lineTo(pad.l, h - pad.b);
    ctx.lineTo(w - pad.r, h - pad.b);
    ctx.stroke();

    ctx.fillStyle = "#444";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("2024 water table vs baseline (ft) →", pad.l + (w - pad.l - pad.r) / 2, h - 8);
    ctx.save();
    ctx.translate(14, pad.t + (h - pad.t - pad.b) / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("2024 dry-well reports →", 0, 0);
    ctx.restore();

    cat.forEach((g) => {
      const x = g.metrics.gwe_cumulative_drop["2024"];
      const y = g.metrics.well_reports["2024"];
      const px = sx(x);
      const py = sy(y);
      ctx.fillStyle = STATUS_COLORS[g.status_2024] || STATUS_COLORS.unknown;
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fill();
      chartHits.paired.push({ x: px, y: py, r: 8, label: `${g.label} (GSP ${g.gsp_id})` });
    });
  }

  function drawPairedScatter(cat, varId, vdef) {
    const canvas = document.getElementById("rel-paired-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    chartHits.paired = [];
    const pad = { l: 52, r: 16, t: 24, b: 48 };

    if (!cat.length) {
      ctx.fillStyle = "#888";
      ctx.font = "13px sans-serif";
      ctx.fillText("No GSPs with data for this filter.", pad.l, h / 2);
      return;
    }

    const xs = cat.map((g) => g.metrics[varId]["2016"]);
    const ys = cat.map((g) => g.metrics[varId]["2024"]);
    const lo = Math.min(...xs, ...ys);
    const hi = Math.max(...xs, ...ys);
    const span = Math.max(hi - lo, 1e-6);
    const sx = (v) => pad.l + ((v - lo) / span) * (w - pad.l - pad.r);
    const sy = (v) => h - pad.b - ((v - lo) / span) * (h - pad.t - pad.b);

    ctx.strokeStyle = "#ccc";
    ctx.beginPath();
    ctx.moveTo(pad.l, pad.t);
    ctx.lineTo(pad.l, h - pad.b);
    ctx.lineTo(w - pad.r, h - pad.b);
    ctx.stroke();

    ctx.strokeStyle = "#999";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(sx(lo), sy(lo));
    ctx.lineTo(sx(hi), sy(hi));
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = "#444";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "center";
    const xLab = varId === "gwe_cumulative_drop"
      ? "2016 water table vs baseline (ft; + deeper, − higher)"
      : (vdef?.x_label || "2016 →");
    const yLab = varId === "gwe_cumulative_drop"
      ? "2024 water table vs baseline (ft; + deeper, − higher)"
      : (vdef?.y_label || "2024 →");
    ctx.fillText(xLab, pad.l + (w - pad.l - pad.r) / 2, h - 8);
    ctx.save();
    ctx.translate(14, pad.t + (h - pad.t - pad.b) / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(yLab, 0, 0);
    ctx.restore();

    if (vdef?.lower_better) {
      ctx.fillStyle = "rgba(30, 132, 73, 0.07)";
      ctx.beginPath();
      ctx.moveTo(sx(lo), sy(lo));
      ctx.lineTo(sx(hi), sy(lo));
      ctx.lineTo(sx(hi), sy(hi));
      ctx.closePath();
      ctx.fill();
    }

    cat.forEach((g) => {
      const x = g.metrics[varId]["2016"];
      const y = g.metrics[varId]["2024"];
      const px = sx(x);
      const py = sy(y);
      ctx.fillStyle = STATUS_COLORS[g.status_2024] || STATUS_COLORS.unknown;
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fill();
      chartHits.paired.push({ x: px, y: py, r: 8, label: `${g.label} (GSP ${g.gsp_id})` });
    });
  }

  function drawDeltaBars(cat, varId, vdef) {
    const canvas = document.getElementById("rel-delta-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    chartHits.delta = [];
    const sorted = [...cat].sort((a, b) => (b.metrics[varId].delta || 0) - (a.metrics[varId].delta || 0));
    if (!sorted.length) return;

    const deltas = sorted.map((g) => g.metrics[varId].delta || 0);
    const maxAbs = Math.max(...deltas.map(Math.abs), 1e-6);
    const pad = { t: 28, b: 36, l: 48, r: 12 };
    const barW = Math.max(4, (w - pad.l - pad.r) / Math.max(sorted.length, 1) - 2);
    const midY = pad.t + (h - pad.t - pad.b) / 2;

    ctx.strokeStyle = "#ddd";
    ctx.beginPath();
    ctx.moveTo(pad.l, midY);
    ctx.lineTo(w - pad.r, midY);
    ctx.stroke();

    ctx.fillStyle = "#444";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(vdef?.delta_label || "2024 − 2016", pad.l + (w - pad.l - pad.r) / 2, 14);

    sorted.forEach((g, i) => {
      const d = g.metrics[varId].delta || 0;
      const x = pad.l + i * (barW + 2);
      const barH = (Math.abs(d) / maxAbs) * ((h - pad.t - pad.b) / 2 - 4);
      ctx.fillStyle = STATUS_COLORS[g.status_2024] || STATUS_COLORS.unknown;
      if (d >= 0) ctx.fillRect(x, midY - barH, barW, barH);
      else ctx.fillRect(x, midY, barW, barH);
      chartHits.delta.push({
        x: x + barW / 2,
        y: d >= 0 ? midY - barH / 2 : midY + barH / 2,
        r: Math.max(barW / 2 + 2, 6),
        label: `${g.label} (GSP ${g.gsp_id})`,
      });
    });
  }

  function metricImproved(g, varId, vdef) {
    const d = g.metrics[varId]?.delta;
    if (d == null || d === 0) return null;
    if (varId === "fallow_pct") {
      const gweD = g.metrics.gwe_cumulative_drop?.delta;
      if (gweD == null) return null;
      if (d > 0 && gweD < 0) return true;
      if (d > 0 && gweD > 0) return false;
      if (d <= 0 && gweD < 0) return true;
      return false;
    }
    if (varId === "total_ag_acres") {
      const gweD = g.metrics.gwe_cumulative_drop?.delta;
      if (gweD == null) return null;
      if (gweD < 0) return true;
      if (gweD > 0 && Math.abs(d) < 5000) return false;
      if (gweD > 0) return false;
      return null;
    }
    if (isTrendKey(varId)) {
      const a0 = g.metrics[varId]?.["2016"];
      const b = g.metrics[varId]?.["2024"];
      if (a0 == null || b == null) return null;
      if (a0 > 0.05 && b > 0.05) return d < 0;
      if (a0 < -0.05 && b < -0.05) return d < 0;
      if (a0 > 0.05 && b < -0.05) return true;
      if (a0 < -0.05 && b > 0.05) return false;
      if (b < -0.05) return true;
      if (b > 0.05) return false;
      return null;
    }
    const lb = vdef?.lower_better;
    return lb ? d < 0 : d > 0;
  }

  function renderRelSummary(cat, varId, vdef) {
    const el = document.getElementById("rel-summary");
    if (!el || !vdef) return;
    el.innerHTML = relSummaryLines(cat, varId, vdef);
  }

  waitForData(() => {
    initTabs();
    renderIntroPage();
    renderTakeawaysPage();
    renderAnalysisPage();
    renderSourcesPage();
    populateCloseSelect();
    populateRelSelect();
    bindPanelEvents();
  });
})();
