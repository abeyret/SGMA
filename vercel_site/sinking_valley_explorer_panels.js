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
    gwe_cumulative_drop: "Water table vs pre-SGMA average",
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
    gwe_cumulative_drop: "Is the table higher or lower than the pre-SGMA average? Above = higher (better). Below = deeper (worse). Not pumping volume.",
    gwe_trend_4yr_ft_yr: "+ = table falling · − = table rising. Compares 4-yr average rates in each year.",
  };

  function formatGweVsBaseline(v) {
    if (v == null || Number.isNaN(v)) return "—";
    const n = Math.abs(v);
    const s = n >= 100 ? n.toFixed(0) : n.toFixed(1);
    if (v > 0) return `${s} ft below baseline`;
    if (v < 0) return `${s} ft above baseline`;
    return "At pre-SGMA average";
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

    renderOrientSection(intro.orient);
  }


  function buildOrientFlowHtml() {
    const chain = [
      { id: "ag", title: "Agriculture / Farms", sub: "San Joaquin Valley production", fill: "url(#flow-ag)" },
      { id: "pump", title: "Groundwater pumping", sub: "irrigation & supply", fill: "url(#flow-pump)" },
      { id: "gwe", title: "Declining water table", sub: "overdraft & depletion", fill: "url(#flow-gwe)" },
      { id: "sub", title: "Land subsidence", sub: "permanent aquifer compaction", fill: "url(#flow-sub)" },
      { id: "infra", title: "Infrastructure damage", sub: "canals, roads, levees", fill: "url(#flow-infra)" },
    ];
    const links = [
      "high irrigation demand",
      "groundwater overdraft",
      "permanent aquifer compaction",
      "land elevation change",
    ];
    const impacts = [
      { from: "Agriculture", label: "jobs & local economy" },
      { from: "Water table", label: "dry wells & low water access" },
      { from: "Subsidence", label: "taxes & repair costs" },
    ];

    const W = 848;
    const pad = 14;
    const chainY = 58;
    const boxH = 78;
    const boxW = 112;
    const arrowY = chainY + boxH / 2;
    const slotW = (W - pad * 2) / chain.length;
    const boxX = (i) => pad + i * slotW + (slotW - boxW) / 2;
    const boxCx = (i) => boxX(i) + boxW / 2;
    const chainBottom = chainY + boxH;
    const impactY = chainBottom + 28;
    const impactH = 56;
    const impactW = 138;
    const impactCx = [W / 6, W / 2, (W * 5) / 6];
    const braceY0 = impactY + impactH;
    const braceY1 = braceY0 + 14;
    const braceY2 = braceY1 + 22;
    const resW = 292;
    const resH = 46;
    const resX = W / 2 - resW / 2;
    const resY = braceY2 + 2;
    const viewH = resY + resH + 8;
    const viewTop = 24;

    const connLabelSvg = (label, mid) => {
      const words = label.split(" ");
      if (words.length >= 2) {
        const splitAt = Math.ceil(words.length / 2);
        const line1 = words.slice(0, splitAt).join(" ");
        const line2 = words.slice(splitAt).join(" ");
        return `<text x="${mid}" y="${arrowY - 16}" text-anchor="middle" class="oe-flow-conn"><tspan x="${mid}" dy="0">${line1}</tspan><tspan x="${mid}" dy="6.5">${line2}</tspan></text>`;
      }
      return `<text x="${mid}" y="${arrowY - 7}" text-anchor="middle" class="oe-flow-conn">${label}</text>`;
    };

    const chainBoxes = chain
      .map((step, i) => {
        const x = boxX(i);
        const titleLines = step.title.split(" / ");
        const titleSvg =
          titleLines.length > 1
            ? `<tspan x="${boxCx(i)}" dy="0">${titleLines[0]} /</tspan><tspan x="${boxCx(i)}" dy="11">${titleLines[1]}</tspan>`
            : `<tspan x="${boxCx(i)}" dy="0">${step.title}</tspan>`;
        return `
          <rect x="${x}" y="${chainY}" width="${boxW}" height="${boxH}" rx="5" fill="${step.fill}" stroke="#c5d9e8" stroke-width="0.75"/>
          <text x="${boxCx(i)}" y="${chainY + 24}" text-anchor="middle" class="oe-flow-title">${titleSvg}</text>
          <text x="${boxCx(i)}" y="${chainY + 58}" text-anchor="middle" class="oe-flow-sub">${step.sub}</text>`;
      })
      .join("");

    const connLabels = links
      .map((label, i) => {
        const mid = (boxX(i) + boxW + boxX(i + 1)) / 2;
        return connLabelSvg(label, mid);
      })
      .join("");

    const connArrows = links
      .map((_, i) => {
        const x1 = boxX(i) + boxW + 2;
        const x2 = boxX(i + 1) - 8;
        return `<line x1="${x1}" y1="${arrowY}" x2="${x2}" y2="${arrowY}" stroke="#64748b" stroke-width="0.75" marker-end="url(#flow-arrow)"/>`;
      })
      .join("");

    const impactsSvg = impacts
      .map((imp, i) => {
        const cx = impactCx[i];
        const x = cx - impactW / 2;
        return `
          <rect x="${x}" y="${impactY}" width="${impactW}" height="${impactH}" rx="5" fill="#fff" stroke="#c5d9e8" stroke-width="0.75"/>
          <text x="${cx}" y="${impactY + 16}" text-anchor="middle" class="oe-flow-impact-from">${imp.from}</text>
          <text x="${cx}" y="${impactY + 30}" text-anchor="middle" class="oe-flow-arrow">↓</text>
          <text x="${cx}" y="${impactY + 46}" text-anchor="middle" class="oe-flow-impact-label">${imp.label}</text>`;
      })
      .join("");

    return `
      <figure class="orient-editorial orient-flow-editorial orient-diagram-interactive" aria-label="Causal chain from agriculture through groundwater pumping and subsidence to infrastructure damage, with resident impacts on jobs, wells, and taxes.">
        <svg class="orient-editorial-svg orient-flow-svg" viewBox="0 ${viewTop} ${W} ${viewH - viewTop}" preserveAspectRatio="xMidYMid meet" role="img">
          <defs>
            <linearGradient id="flow-canvas" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#fcfcfb"/>
              <stop offset="100%" stop-color="#f6f5f2"/>
            </linearGradient>
            <linearGradient id="flow-ag" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#ffffff"/>
              <stop offset="100%" stop-color="#f9fcfe"/>
            </linearGradient>
            <linearGradient id="flow-pump" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#fcfdfe"/>
              <stop offset="100%" stop-color="#f5f9fc"/>
            </linearGradient>
            <linearGradient id="flow-gwe" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#f9fbfd"/>
              <stop offset="100%" stop-color="#f0f6fa"/>
            </linearGradient>
            <linearGradient id="flow-sub" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#f6f9fc"/>
              <stop offset="100%" stop-color="#ebf2f8"/>
            </linearGradient>
            <linearGradient id="flow-infra" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#f3f7fb"/>
              <stop offset="100%" stop-color="#e6eef5"/>
            </linearGradient>
            <marker id="flow-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" fill="#64748b"/>
            </marker>
          </defs>

          <rect width="${W}" height="${viewH}" fill="url(#flow-canvas)"/>
          <rect x="${pad - 4}" y="${viewTop + 4}" width="${W - pad * 2 + 8}" height="${viewH - viewTop - 8}" rx="4" fill="#fff" stroke="#e2e8f0" stroke-width="0.75"/>
          <text x="${pad + 4}" y="${viewTop + 16}" class="oe-section-label">Causal chain</text>
          <text x="${W - pad - 4}" y="${viewTop + 16}" text-anchor="end" class="oe-micro oe-muted">Left to right</text>

          <g class="diagram-layer diagram-layer-chain">
            ${chainBoxes}
            ${connLabels}
            ${connArrows}
          </g>

          <g class="diagram-layer diagram-layer-spine">
            <line x1="${W / 2}" y1="${chainBottom + 4}" x2="${W / 2}" y2="${impactY - 6}" stroke="#cbd5e1" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.75"/>
          </g>

          <g class="diagram-layer diagram-layer-impacts">
            ${impactsSvg}
          </g>

          <g class="diagram-layer diagram-layer-brace">
            <path d="M ${impactCx[0]} ${braceY0} L ${impactCx[0]} ${braceY1} M ${impactCx[1]} ${braceY0} L ${impactCx[1]} ${braceY1} M ${impactCx[2]} ${braceY0} L ${impactCx[2]} ${braceY1} M ${impactCx[0]} ${braceY1} L ${impactCx[2]} ${braceY1} M ${impactCx[1]} ${braceY1} L ${impactCx[1]} ${braceY2}" fill="none" stroke="#1a1a1a" stroke-width="1.2" stroke-linecap="square" opacity="0.65"/>
          </g>

          <g class="diagram-layer diagram-layer-residents">
            <rect x="${resX}" y="${resY}" width="${resW}" height="${resH}" rx="5" fill="#fff9f2" stroke="#e8d4b8" stroke-width="0.75"/>
            <text x="${W / 2}" y="${resY + 18}" text-anchor="middle" class="oe-flow-title">Residents &amp; rural communities</text>
            <text x="${W / 2}" y="${resY + 34}" text-anchor="middle" class="oe-flow-sub">Affected at every stage — wells, jobs, taxes</text>
          </g>
        </svg>
      </figure>`;
  }

  function buildGspSchematicHtml() {
    const x0 = 64;
    const x1 = 274;
    const x2 = 484;
    const x3 = 694;
    const x4 = 896;
    const cx = [169, 379, 589, 795];

    return `
      <figure class="orient-editorial orient-gsp-editorial orient-gsp-interactive orient-diagram-interactive" aria-label="One connected basin with four adjacent GSPs. Regulated areas A and B, stressed C, and overdraft D share a continuous aquifer. Pumping in downstream GSPs lowers groundwater beneath upstream regions; land and infrastructure subside where overdraft is severe.">
        <svg class="orient-editorial-svg" viewBox="48 42 864 510" preserveAspectRatio="xMidYMid meet" role="img">
          <defs>
            <linearGradient id="gsp-canvas" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#fcfcfb"/>
              <stop offset="100%" stop-color="#f6f5f2"/>
            </linearGradient>
            <linearGradient id="gsp-regulated" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#5a8f82" stop-opacity="0.1"/>
              <stop offset="100%" stop-color="#5a8f82" stop-opacity="0.16"/>
            </linearGradient>
            <linearGradient id="gsp-stressed" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#c9a030" stop-opacity="0.1"/>
              <stop offset="100%" stop-color="#c9a030" stop-opacity="0.16"/>
            </linearGradient>
            <linearGradient id="gsp-overdraft" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#b85c38" stop-opacity="0.1"/>
              <stop offset="100%" stop-color="#b85c38" stop-opacity="0.17"/>
            </linearGradient>
            <linearGradient id="gsp-geology" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#e8e2d8"/>
              <stop offset="45%" stop-color="#d8cfc2"/>
              <stop offset="100%" stop-color="#c8bfb0"/>
            </linearGradient>
            <linearGradient id="gsp-aquifer-layer" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#8ab4d4" stop-opacity="0.22"/>
              <stop offset="100%" stop-color="#4a7a9a" stop-opacity="0.32"/>
            </linearGradient>
            <linearGradient id="gsp-water-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#6ba3cc" stop-opacity="0.38"/>
              <stop offset="100%" stop-color="#2c5282" stop-opacity="0.52"/>
            </linearGradient>
            <linearGradient id="gsp-canal-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#d0d3d6"/>
              <stop offset="100%" stop-color="#a8adb4"/>
            </linearGradient>
            <marker id="gsp-flow-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" fill="#3d7ea8"/>
            </marker>
            <clipPath id="gsp-water-clip">
              <path d="M${x0} 326 L${x1} 344 L${x2} 370 L${x3} 392 L${x4} 406 L${x4} 468 L${x0} 468 Z"/>
            </clipPath>
          </defs>

          <rect width="960" height="560" fill="url(#gsp-canvas)"/>

          <g class="gsp-layer gsp-layer-plan">
          <!-- PLAN VIEW PANEL -->
          <rect x="${x0}" y="48" width="${x4 - x0}" height="168" rx="4" fill="#fff" stroke="#e2e8f0" stroke-width="0.75"/>
          <text x="${x0 + 12}" y="66" class="oe-section-label">Plan view</text>
          <text x="${x4 - 12}" y="66" text-anchor="end" class="oe-micro oe-muted">Four adjacent GSP regions</text>

          <!-- GSP regions — even blocks, subtle fills -->
          <rect x="${x0 + 8}" y="78" width="${x1 - x0 - 8}" height="122" fill="url(#gsp-regulated)"/>
          <rect x="${x1}" y="78" width="${x2 - x1}" height="122" fill="url(#gsp-regulated)"/>
          <rect x="${x2}" y="78" width="${x3 - x2}" height="122" fill="url(#gsp-stressed)"/>
          <rect x="${x3}" y="78" width="${x4 - x3 - 8}" height="122" fill="url(#gsp-overdraft)"/>

          <line x1="${x1}" y1="78" x2="${x1}" y2="216" stroke="#5a6578" stroke-width="0.9" stroke-dasharray="6 4" opacity="0.78"/>
          <line x1="${x2}" y1="78" x2="${x2}" y2="216" stroke="#5a6578" stroke-width="0.9" stroke-dasharray="6 4" opacity="0.78"/>
          <line x1="${x3}" y1="78" x2="${x3}" y2="216" stroke="#5a6578" stroke-width="0.9" stroke-dasharray="6 4" opacity="0.78"/>

          <text x="${cx[0]}" y="128" text-anchor="middle" class="oe-gsp-label">GSP A</text>
          <text x="${cx[0]}" y="144" text-anchor="middle" class="oe-gsp-status oe-green">Regulated</text>
          <text x="${cx[1]}" y="128" text-anchor="middle" class="oe-gsp-label">GSP B</text>
          <text x="${cx[1]}" y="144" text-anchor="middle" class="oe-gsp-status oe-green">Regulated</text>
          <text x="${cx[2]}" y="128" text-anchor="middle" class="oe-gsp-label">GSP C</text>
          <text x="${cx[2]}" y="144" text-anchor="middle" class="oe-gsp-status oe-ochre">Stressed</text>
          <text x="${cx[3]}" y="128" text-anchor="middle" class="oe-gsp-label">GSP D</text>
          <text x="${cx[3]}" y="144" text-anchor="middle" class="oe-gsp-status oe-rust">Overdraft</text>
          </g>

          <g class="gsp-layer gsp-layer-xsection">
          <!-- CROSS-SECTION PANEL -->
          <rect x="${x0}" y="232" width="${x4 - x0}" height="248" rx="4" fill="#fff" stroke="#e2e8f0" stroke-width="0.75"/>
          <text x="${x0 + 12}" y="250" class="oe-section-label">Cross-section</text>

          <!-- Geology body -->
          <path d="M${x0 + 8} 262 L${x1} 261 L${x2} 266 L${x3} 276 L${x4 - 8} 288 L${x4 - 8} 468 L${x0 + 8} 468 Z" fill="url(#gsp-geology)"/>
          <rect x="${x0 + 8}" y="388" width="${x4 - x0 - 16}" height="52" fill="url(#gsp-aquifer-layer)"/>
          <line x1="${x0 + 8}" y1="388" x2="${x4 - 8}" y2="388" stroke="#6b9ab8" stroke-width="0.6" opacity="0.55"/>
          <line x1="${x0 + 8}" y1="440" x2="${x4 - 8}" y2="440" stroke="#6b9ab8" stroke-width="0.6" opacity="0.45"/>
          <text x="${x0 + 16}" y="382" class="oe-tiny oe-muted">Aquifer</text>

          <!-- GSP admin boundaries — cross-section (faint, above geology fill) -->
          <line x1="${x1}" y1="258" x2="${x1}" y2="468" stroke="#5a6578" stroke-width="0.85" stroke-dasharray="6 4" opacity="0.36"/>
          <line x1="${x2}" y1="258" x2="${x2}" y2="468" stroke="#5a6578" stroke-width="0.85" stroke-dasharray="6 4" opacity="0.36"/>
          <line x1="${x3}" y1="258" x2="${x3}" y2="468" stroke="#5a6578" stroke-width="0.85" stroke-dasharray="6 4" opacity="0.36"/>
          </g>

          <g class="gsp-layer gsp-layer-water">
          <!-- Groundwater saturation -->
          <g clip-path="url(#gsp-water-clip)">
            <rect x="${x0 + 8}" y="326" width="${x4 - x0 - 16}" height="142" fill="url(#gsp-water-fill)"/>
          </g>

          <!-- Water table — strong profile, steep decline in B & C -->
          <path d="M${x0 + 8} 326 L${x1} 344 L${x2} 370 L${x3} 392 L${x4 - 8} 406" fill="none" stroke="#2563eb" stroke-width="1.5" stroke-linecap="round"/>
          <text x="${x0 + 16}" y="320" class="oe-tiny oe-blue">Water table</text>
          </g>

          <g class="gsp-layer gsp-layer-flow">
          <!-- Directional groundwater flow (downgradient toward pumping) -->
          <line x1="680" y1="382" x2="820" y2="398" stroke="#3d7ea8" stroke-width="0.85" marker-end="url(#gsp-flow-arrow)"/>
          <line x1="520" y1="364" x2="680" y2="382" stroke="#3d7ea8" stroke-width="0.85" marker-end="url(#gsp-flow-arrow)"/>
          <line x1="340" y1="348" x2="520" y2="364" stroke="#3d7ea8" stroke-width="0.85" marker-end="url(#gsp-flow-arrow)"/>
          <text x="560" y="356" text-anchor="middle" class="oe-tiny oe-blue">Groundwater flow</text>
          </g>

          <g class="gsp-layer gsp-layer-surface">
          <!-- Land surface -->
          <path d="M${x0 + 8} 262 L${x1} 261 L${x2} 266 L${x3} 276 L${x4 - 8} 288" fill="none" stroke="#9a9088" stroke-width="0.75"/>

          <!-- Canal on surface — design grade vs subsidence -->
          <line x1="${x0 + 8}" y1="256" x2="${x4 - 8}" y2="256" stroke="#94a3b8" stroke-width="0.45" stroke-dasharray="5 4" opacity="0.5"/>
          <text x="${x4 - 16}" y="252" text-anchor="end" class="oe-tiny oe-muted">Design grade</text>
          <path d="M${x0 + 8} 262 L${x1} 261 L${x2} 266 L${x3} 276 L${x4 - 8} 288 L${x4 - 8} 294 L${x0 + 8} 268 Z" fill="url(#gsp-canal-fill)" opacity="0.75" stroke="#8a9098" stroke-width="0.45"/>
          <line x1="${x0 + 20}" y1="272" x2="${x0 + 52}" y2="272" stroke="#8a9098" stroke-width="0.45"/>
          <text x="${x0 + 56}" y="275" class="oe-tiny oe-muted">Irrigation canal</text>
          </g>

          <g class="gsp-layer gsp-layer-pump oe-pump">
            <line x1="${cx[2]}" y1="266" x2="${cx[2]}" y2="370" stroke="#475569" stroke-width="0.65"/>
            <polygon points="${cx[2]},266 ${cx[2] - 4},274 ${cx[2] + 4},274" fill="#475569" opacity="0.7"/>
            <text x="${cx[2]}" y="262" text-anchor="middle" class="oe-tiny oe-muted">Pumping</text>
            <line x1="${cx[3]}" y1="276" x2="${cx[3]}" y2="392" stroke="#475569" stroke-width="0.65"/>
            <polygon points="${cx[3]},276 ${cx[3] - 4},284 ${cx[3] + 4},284" fill="#475569" opacity="0.7"/>
            <text x="${cx[3]}" y="272" text-anchor="middle" class="oe-tiny oe-muted">Pumping</text>
          </g>

          <g class="gsp-layer gsp-layer-callout oe-callout">
            <rect x="348" y="278" width="62" height="16" rx="2" fill="#ecfdf5" stroke="#5a8f82" stroke-width="0.5"/>
            <text x="379" y="289" text-anchor="middle" class="oe-callout-text oe-green">Minor</text>
            <rect x="554" y="286" width="70" height="16" rx="2" fill="#fffef8" stroke="#c9a030" stroke-width="0.5"/>
            <text x="589" y="297" text-anchor="middle" class="oe-callout-text oe-ochre">Canal sag</text>
            <rect x="748" y="298" width="94" height="16" rx="2" fill="#fffafa" stroke="#b85c38" stroke-width="0.5"/>
            <text x="795" y="309" text-anchor="middle" class="oe-callout-text oe-rust">Severe damage</text>
          </g>

          <g class="gsp-layer gsp-layer-footer">
          <!-- Explanatory statement -->
          <text x="480" y="508" text-anchor="middle" class="oe-annotation">Overdraft in downstream GSPs lowers the shared aquifer across basin boundaries.</text>

          <!-- Legend bar -->
          <rect x="290" y="528" width="380" height="16" rx="2" fill="#fff" stroke="#e8eaec" stroke-width="0.4"/>
          <circle cx="318" cy="536" r="3.5" fill="#5a8f82"/>
          <text x="328" y="539" class="oe-legend-text">Regulated</text>
          <circle cx="418" cy="536" r="3.5" fill="#c9a030"/>
          <text x="428" y="539" class="oe-legend-text">Stressed</text>
          <circle cx="508" cy="536" r="3.5" fill="#b85c38"/>
          <text x="518" y="539" class="oe-legend-text">Overdraft</text>
          <line x1="598" y1="536" x2="612" y2="536" stroke="#8a9098" stroke-width="1.2"/>
          <text x="620" y="539" class="oe-legend-text">Canal</text>
          </g>
        </svg>
      </figure>`;
  }

  function renderOrientSection(orient) {
    const el = document.getElementById("intro-orient-panel");
    if (!el || !orient) return;

    const boldMd = (s) =>
      String(s || "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

    const guideHtml = (orient.site_guide || [])
      .map(
        (g, i) => `
        <li class="orient-guide-item">
          <a href="#" class="orient-guide-link" data-goto-tab="${g.tab_id}">
            <span class="orient-guide-num">${String(i + 1).padStart(2, "0")}</span>
            <span class="orient-guide-body">
              <span class="orient-guide-tab">${g.tab}</span>
              <span class="orient-guide-blurb">${g.blurb}</span>
            </span>
            <span class="orient-guide-arrow" aria-hidden="true">→</span>
          </a>
        </li>`,
      )
      .join("");

    el.innerHTML = `
      <div class="orient-panel">
        <header class="orient-panel-header">
          <h2 class="orient-title">${orient.title || ""}</h2>
        </header>
        <div class="orient-panel-body">
        <div class="orient-big-q">
          <span class="orient-big-q-label">${orient.big_question?.label || "Big question"}</span>
          <p class="orient-big-q-text">${orient.big_question?.text || ""}</p>
        </div>

        <section class="orient-section orient-section-connect" aria-labelledby="orient-connect-head">
          <div class="orient-connect-flow">
            <h3 id="orient-connect-head" class="orient-section-head">How everything connects</h3>
            <p class="orient-network-lede">${boldMd(orient.network_caption)}</p>
            <div class="orient-network-wrap">${buildOrientFlowHtml()}</div>
          </div>
          <div class="orient-connect-break" aria-hidden="true">
            <span class="orient-connect-break-line"></span>
            <span class="orient-connect-break-label">Fragmented policy · shared reality</span>
            <span class="orient-connect-break-line"></span>
          </div>
          <div class="orient-notes">
            <div class="orient-notes-row">
              <div class="orient-note">
                <h4>What is a GSP?</h4>
                <p>${boldMd(orient.gsp_note)}</p>
              </div>
              <div class="orient-note orient-note-warn">
                <h4>Shared groundwater</h4>
                <p>${boldMd(orient.connected_note)}</p>
              </div>
            </div>
            ${buildGspSchematicHtml()}
            <p class="orient-editorial-cap">Note: Schematic illustration for orientation only. Hydrogeologic processes, basin geometry, and GSP conditions are simplified and not drawn to scale.</p>
          </div>
        </section>
        </div>
      </div>

      <section class="intro-section orient-guide-section" aria-labelledby="orient-guide-head">
        <h2 id="orient-guide-head">Where to look on this site</h2>
        <p class="orient-guide-lede">Jump straight to the view you need: play around with the overview map, look at GSP specific details, and more.</p>
        <ul class="orient-guide">${guideHtml}</ul>
        <p class="orient-cta-wrap">
          <a href="#" class="intro-cta inline-cta orient-guide-cta" data-goto-tab="explorer">Start with the map →</a>
        </p>
      </section>`;

    el.querySelectorAll("[data-goto-tab]").forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        switchTab(link.dataset.gotoTab);
      });
    });
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
        <li class="sources-item">
          <a href="${item.url}" target="_blank" rel="noopener noreferrer">${item.label}</a>
          ${item.description ? `<p class="sources-desc">${item.description}</p>` : ""}
        </li>`).join("");
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

  const EXPLORE_TAB_LABEL = { explorer: "map", close: "close view" };

  function exploreTabLabel(tab) {
    return EXPLORE_TAB_LABEL[tab] || String(tab || "").replace(/_/g, " ");
  }

  const TAKEAWAY_TONE_LABEL = { good: "Positive signal", bad: "Negative signal", warn: "Mixed / caution", mixed: "Mixed signal", neutral: "" };

  function renderTakeawaysPage() {
    const page = DATA.takeaways_page;
    if (!page) return;
    const ledeEl = document.getElementById("takeaways-lede");
    const headlineEl = document.getElementById("takeaways-headline");
    const answerEl = document.getElementById("takeaways-sgma-answer");
    const sectionsEl = document.getElementById("takeaways-sections");
    if (ledeEl) {
      if (page.lede) {
        ledeEl.hidden = false;
        ledeEl.textContent = page.lede;
      } else {
        ledeEl.hidden = true;
        ledeEl.textContent = "";
      }
    }

    const cards = page.headline_cards || [];
    if (headlineEl) {
      if (cards.length) {
        headlineEl.hidden = false;
        headlineEl.innerHTML = cards.map((c) => {
          const tone = c.tone || "neutral";
          return `
            <div class="takeaway-head-card takeaway-head-card-${tone}" title="${c.hint || ""}">
              <span class="takeaway-head-val">${c.val}</span>
              <span class="takeaway-head-lbl">${c.lbl}</span>
              ${c.hint ? `<span class="takeaway-head-hint">${c.hint}</span>` : ""}
            </div>`;
        }).join("") + (page.baseline_note ? `<p class="takeaways-baseline-note">${page.baseline_note}</p>` : "");
      } else {
        headlineEl.hidden = true;
        headlineEl.innerHTML = "";
      }
    }

    if (answerEl) {
      const ans = page.sgma_answer;
      if (ans?.text) {
        answerEl.hidden = false;
        const tone = ans.tone || "mixed";
        answerEl.className = `takeaways-sgma-answer takeaway-sgma-tone-${tone}`;
        answerEl.innerHTML = ans.question
          ? `<strong>${ans.question}</strong> <span>${ans.text}</span>`
          : ans.text;
      } else {
        answerEl.hidden = true;
        answerEl.className = "takeaways-sgma-answer";
        answerEl.textContent = "";
      }
    }

    if (!sectionsEl) return;
    sectionsEl.innerHTML = (page.sections || []).map((s) => {
      const verdictTone = s.verdict_tone || "neutral";
      const toneLabel = TAKEAWAY_TONE_LABEL[verdictTone] || "";
      const statsHtml = (s.stats || []).map((m) => {
        const tone = m.tone || (m.compliant ? "good" : "neutral");
        return `
          <div class="takeaway-stat takeaway-stat-tone-${tone}${m.compliant ? " takeaway-stat-compliant" : ""}">
            <span class="val">${m.val}</span>
            <span class="lbl">${m.lbl}</span>
            ${m.hint ? `<span class="takeaway-stat-hint">${m.hint}</span>` : ""}
          </div>`;
      }).join("");
      return `
      <section class="takeaway-section takeaway-section-${verdictTone}" id="takeaway-${s.id || ""}">
        <div class="takeaway-section-copy">
          <h2>${s.title || ""}</h2>
          ${s.focus ? `<p class="takeaway-focus">${toneLabel ? `<span class="takeaway-tone-badge takeaway-tone-badge-${verdictTone}">${toneLabel}</span>` : ""}${s.focus}</p>` : ""}
          ${s.body ? `<p class="takeaway-body">${s.body}</p>` : ""}
          ${statsHtml ? `<div class="takeaway-stats">${statsHtml}</div>` : ""}
          ${s.sgma_takeaway ? `<p class="takeaway-sgma-verdict takeaway-verdict-tone-${verdictTone}">${s.sgma_takeaway}</p>` : ""}
          ${(s.bullets || []).length ? `
            <ul class="takeaway-bullets">
              ${(s.bullets || []).map((b) => `<li>${b}</li>`).join("")}
            </ul>` : ""}
          ${s.explore_tab ? `<p class="takeaway-explore"><a href="#" class="intro-cta inline-cta" data-goto-tab="${s.explore_tab}">Explore in ${exploreTabLabel(s.explore_tab)} →</a></p>` : ""}
        </div>
      </section>`;
    }).join("");
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
        if (k === "fallow_pct") {
          deltaClass = d > 0 ? "bad" : "good";
        } else if (isTrendKey(k)) {
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
