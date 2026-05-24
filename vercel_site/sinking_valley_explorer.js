(async function () {
  const TEAL = "#004655";
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
  const DROUGHT_COLOR = "#c0392b";
  const WET_COLOR = "#5dade2";

  const statusEl = document.createElement("div");
  statusEl.style.cssText =
    "position:fixed;bottom:1rem;left:50%;transform:translateX(-50%);background:#c0392b;color:#fff;padding:.5rem 1rem;border-radius:4px;font:600 12px sans-serif;z-index:99;display:none";
  document.body.appendChild(statusEl);

  function showError(msg) {
    statusEl.textContent = msg;
    statusEl.style.display = "block";
    console.error(msg);
  }

  let DATA;
  try {
    const resp = await fetch("sinking_valley_explorer_data.json");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    DATA = await resp.json();
  } catch (e) {
    showError("Data failed to load: " + (e.message || e));
    return;
  }

  const annualLayers = (DATA.annual_layers || []).sort((a, b) => a.year - b.year);
  const cumulativeLayers = (DATA.cumulative_layers || []).sort((a, b) => a.year - b.year);
  const sliderYears = DATA.slider_years || annualLayers.map((l) => l.year);
  const scaleMax = DATA.scale_max || {};

  const bbox = DATA.bbox || {};
  if (!bbox.xmin) {
    showError("Missing map bounds.");
    return;
  }

  const coords = [
    [bbox.xmin, bbox.ymax],
    [bbox.xmax, bbox.ymax],
    [bbox.xmax, bbox.ymin],
    [bbox.xmin, bbox.ymin],
  ];

  let subsidenceMode = "none";
  let overdraftMode = "none";
  let compareMode = "side_by_side";
  let selectedYear = sliderYears[sliderYears.length - 1] || 2024;
  let playing = false;

  const map = createMap("map");
  let mapPre = null;
  let mapPost = null;
  let mapOd = null;
  let mapEq = null;

  const slider = document.getElementById("year-slider");
  const yearLabel = document.getElementById("year-label");
  const playBtn = document.getElementById("play-btn");
  const subsidenceSelect = document.getElementById("subsidence-select");
  const overdraftSelect = document.getElementById("overdraft-select");
  const overdraftDesc = document.getElementById("overdraft-desc");
  const lensSelect = document.getElementById("effectiveness-select");
  const lensDesc = document.getElementById("lens-desc");
  const note = document.getElementById("baseline-note");
  const wellStats = document.getElementById("well-stats");

  const compareSelect = document.getElementById("compare-select");
  const compareDesc = document.getElementById("compare-desc");
  const splitCmp = DATA.split_comparison || {};
  const splitGweScale = splitCmp.gwe_scale || splitCmp.drop_scale || { min: 50, max: 250 };
  const equityLenses = DATA.equity_lenses || DATA.effectiveness_lenses || [];
  const overdraftLayers = DATA.overdraft_layers || [];
  const compareModes = DATA.compare_modes || [];
  const gspById = Object.fromEntries((DATA.gsp_catalog || []).map((g) => [String(g.gsp_id), g]));

  const gspTooltip = document.createElement("div");
  gspTooltip.id = "gsp-tooltip";
  gspTooltip.className = "gsp-tooltip hidden";
  document.body.appendChild(gspTooltip);

  slider.min = sliderYears[0];
  slider.max = sliderYears[sliderYears.length - 1];
  slider.value = selectedYear;
  yearLabel.textContent = selectedYear;
  if (note) note.textContent = DATA.explorer_note || "";

  function compareMeta(id) {
    return compareModes.find((m) => m.id === id);
  }

  function updateCompareDesc() {
    const m = compareMeta(compareMode);
    if (compareDesc) compareDesc.textContent = m ? m.description : "";
  }
  updateCompareDesc();

  function compareActive() {
    return compareMode !== "overlay" && showOverdraftOverlay() && showEquityOverlays();
  }

  function applyViewLayout() {
    const splitOn = document.getElementById("toggle-split")?.checked;
    const cmpOn = compareActive() && compareMode === "side_by_side" && !splitOn;
    const scatterOn = compareActive() && compareMode === "scatter" && !splitOn;
    document.getElementById("map").style.display = splitOn || cmpOn ? "none" : "block";
    document.getElementById("compare-container")?.classList.toggle("active", cmpOn);
    document.getElementById("scatter-panel")?.classList.toggle("visible", scatterOn);
    if (cmpOn && !mapOd) initCompareMaps();
    if (cmpOn) refreshCompareMaps(selectedYear);
    if (scatterOn) drawScatter(selectedYear);
  }
  function showEquityOverlays() {
    return lensSelect.value !== "none";
  }

  function showOverdraftOverlay() {
    return overdraftMode !== "none";
  }

  function equityVisibility() {
    return showEquityOverlays() ? "visible" : "none";
  }

  function overdraftVisibility() {
    return showOverdraftOverlay() ? "visible" : "none";
  }

  function lensMeta(id) {
    return equityLenses.find((l) => l.id === id);
  }

  function overdraftMeta(id) {
    return overdraftLayers.find((l) => l.id === id);
  }

  function updateLensDesc() {
    const m = lensMeta(lensSelect.value);
    lensDesc.textContent = m ? m.description : "";
  }

  function updateOverdraftDesc() {
    const m = overdraftMeta(overdraftMode);
    if (overdraftDesc) overdraftDesc.textContent = m ? m.description : "";
  }
  updateLensDesc();
  updateOverdraftDesc();

  function layerPath(L) {
    return L?.web_path || L?.file?.replace("outputs/subsidence/", "subsidence/");
  }

  function subsidenceLayerForYear(year) {
    if (subsidenceMode === "none") return null;
    const pool = subsidenceMode === "annual_rate" ? annualLayers : cumulativeLayers;
    return pool.find((l) => l.year === year) || pool[pool.length - 1];
  }

  function enrichGspData(year) {
    const feats = (DATA.gsps?.features || []).map((f) => {
      const props = { ...f.properties };
      const yv = props.year_values?.[String(year)] || {};
      return {
        ...f,
        properties: {
          ...props,
          status_std: yv.status_std || "under_review",
          fallow_pct: yv.fallow_pct ?? 0,
          well_reports: yv.well_reports ?? 0,
          avg_field_acres: yv.avg_field_acres ?? 0,
          farm_pct_vs_2014: yv.farm_pct_vs_2014 ?? 0,
          large_farm_share: yv.large_farm_share ?? 0,
          total_ag_acres: yv.total_ag_acres ?? 0,
          active_ag_acres: yv.active_ag_acres ?? 0,
          small_farm_loss: yv.small_farm_loss ?? 0,
          overdraft_ft_yr: yv.gwe_trend_4yr_ft_yr ?? yv.gwe_trend_ft_yr ?? yv.overdraft_ft_yr ?? 0,
          gwe_trend_ft_yr: yv.gwe_trend_ft_yr ?? 0,
          gwe_trend_4yr_ft_yr: yv.gwe_trend_4yr_ft_yr ?? 0,
          gwe_cumulative_drop: yv.gwe_cumulative_drop ?? 0,
        },
      };
    });
    return { type: "FeatureCollection", features: feats };
  }

  function formatTrendTooltip(v) {
    if (v == null || Number.isNaN(v)) return null;
    const n = Math.abs(Number(v));
    const s = n >= 100 ? n.toFixed(0) : n.toFixed(1);
    if (v > 0.05) return `4-yr trend: falling ${s} ft/yr`;
    if (v < -0.05) return `4-yr trend: rising ${s} ft/yr`;
    return "4-yr trend: near flat";
  }

  function overdraftFillPaint(mode) {
    const sm = scaleMax;
    if (mode === "annual") {
      const mx = sm.gwe_trend_4yr_ft_yr || sm.gwe_trend_ft_yr || sm.overdraft_ft_yr || 5;
      return ["interpolate", ["linear"], ["coalesce", ["get", "gwe_trend_4yr_ft_yr"], 0],
        -mx, "#27ae60", 0, "#a8d08d", mx * 0.3, "#f1c40f", mx * 0.6, "#e67e22", mx, "#c0392b"];
    }
    const mx = sm.gwe_cumulative_drop || 30;
    return ["interpolate", ["linear"], ["coalesce", ["get", "gwe_cumulative_drop"], 0],
      -5, "#27ae60", 0, "#e8f4ea", mx * 0.25, "#f1c40f", mx * 0.55, "#e67e22", mx, "#6b1d1d"];
  }

  function equityFillPaint(lens) {
    if (lens === "gsp_status") {
      return [
        "match", ["get", "status_std"],
        "approved", STATUS_COLORS.approved,
        "under_review", STATUS_COLORS.under_review,
        "inadequate", STATUS_COLORS.inadequate,
        "inadequate_under_review", STATUS_COLORS.inadequate_under_review,
        "state_intervention", STATUS_COLORS.state_intervention,
        "pre_sgma", STATUS_COLORS.pre_sgma,
        "incomplete", STATUS_COLORS.incomplete,
        STATUS_COLORS.unknown,
      ];
    }
    const sm = scaleMax;
    if (lens === "fallowed_land") {
      const mx = sm.fallow_pct || 40;
      return ["interpolate", ["linear"], ["coalesce", ["get", "fallow_pct"], 0],
        0, "#e8f4ea", mx * 0.2, "#7cb87c", mx * 0.5, "#c8922a", mx * 0.8, "#c0392b", mx, "#6b1d1d"];
    }
    if (lens === "water_access") {
      const mx = sm.well_reports || 10;
      return ["interpolate", ["linear"], ["coalesce", ["get", "well_reports"], 0],
        0, "#f5f8fa", 1, "#d4e6f1", mx * 0.3, "#85c1e9", mx * 0.6, "#3498db", mx, "#1a5276"];
    }
    if (lens === "farm_consolidation") {
      const mx = sm.large_farm_share || 20;
      return ["interpolate", ["linear"], ["coalesce", ["get", "large_farm_share"], 0],
        8, "#27ae60", 12, "#a8d08d", 15, "#f1c40f", 18, "#e67e22", mx, "#c0392b"];
    }
    if (lens === "ag_production") {
      const mx = sm.total_ag_acres || 200000;
      return ["interpolate", ["linear"], ["coalesce", ["get", "total_ag_acres"], 0],
        0, "#e8f4ea", mx * 0.2, "#a8d08d", mx * 0.45, "#f1c40f", mx * 0.7, "#e67e22", mx, "#6b1d1d"];
    }
    return STATUS_COLORS.unknown;
  }

  function buildWellFeatures(year) {
    return (DATA.dry_wells || [])
      .filter((w) => w.year === year)
      .map((w) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [w.lon, w.lat] },
        properties: { drought_year: w.drought_year },
      }));
  }

  function baseStyle() {
    return {
      version: 8,
      sources: {
        basemap: {
          type: "raster",
          tiles: ["https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png"],
          tileSize: 256,
          attribution: "© CARTO © OSM",
        },
      },
      layers: [{ id: "basemap", type: "raster", source: "basemap" }],
    };
  }

  function createMap(containerId) {
    return new maplibregl.Map({
      container: containerId,
      style: baseStyle(),
      center: [(bbox.xmin + bbox.xmax) / 2, (bbox.ymin + bbox.ymax) / 2],
      zoom: 7.4,
      minZoom: 4,
      maxZoom: 18,
    });
  }

  function addCountyLayers(m) {
    if (!DATA.counties) return;
    m.addSource("counties", { type: "geojson", data: DATA.counties });
    const vis = document.getElementById("toggle-counties").checked ? "visible" : "none";
    m.addLayer({
      id: "county-fill", type: "fill", source: "counties",
      layout: { visibility: vis },
      paint: { "fill-color": TEAL, "fill-opacity": 0.02 },
    });
    m.addLayer({
      id: "county-line", type: "line", source: "counties",
      layout: { visibility: vis },
      paint: { "line-color": TEAL, "line-width": 2, "line-opacity": 0.85 },
    });
  }

  function addMapLayers(m, year, subsLayer, opts = {}) {
    const odMode = opts.overdraftMode ?? overdraftMode;
    const eqLens = opts.equityLens ?? lensSelect.value;
    const hideSubs = opts.hideSubsidence === true;

    addCountyLayers(m);

    m.addSource("gsps", { type: "geojson", data: enrichGspData(year) });

    m.addLayer({
      id: "gsp-overdraft-fill", type: "fill", source: "gsps",
      layout: { visibility: overdraftVisibility() },
      paint: { "fill-color": overdraftFillPaint(odMode), "fill-opacity": compareMode === "overlay" ? 0.48 : 0.55 },
    });

    if (subsLayer && subsidenceMode !== "none" && !hideSubs) {
      m.addSource("subsidence", { type: "image", url: layerPath(subsLayer), coordinates: coords });
      m.addLayer({
        id: "subsidence-raster", type: "raster", source: "subsidence",
        paint: { "raster-opacity": 0.88, "raster-fade-duration": 200 },
      });
    }

    m.addLayer({
      id: "gsp-equity-fill", type: "fill", source: "gsps",
      layout: { visibility: equityVisibility() },
      paint: { "fill-color": equityFillPaint(eqLens), "fill-opacity": compareMode === "overlay" ? 0.42 : 0.42 },
    });
    m.addLayer({
      id: "gsp-line", type: "line", source: "gsps",
      layout: { visibility: showEquityOverlays() || showOverdraftOverlay() ? "visible" : "none" },
      paint: { "line-color": "#333", "line-width": 0.7, "line-opacity": 0.45 },
    });

    const showDots = document.getElementById("toggle-well-dots").checked;
    m.addSource("wells", {
      type: "geojson",
      data: { type: "FeatureCollection", features: buildWellFeatures(year) },
    });
    m.addLayer({
      id: "wells-circle", type: "circle", source: "wells",
      layout: { visibility: showDots ? "visible" : "none" },
      paint: {
        "circle-radius": 4,
        "circle-color": ["case", ["==", ["get", "drought_year"], true], DROUGHT_COLOR, WET_COLOR],
        "circle-opacity": 0.85,
        "circle-stroke-width": 1,
        "circle-stroke-color": "#fff",
      },
    });
  }

  function addSingleLayerMap(m, year, layerKind) {
    addCountyLayers(m);
    m.addSource("gsps", { type: "geojson", data: enrichGspData(year) });
    if (layerKind === "overdraft") {
      m.addLayer({
        id: "single-fill", type: "fill", source: "gsps",
        paint: { "fill-color": overdraftFillPaint(overdraftMode), "fill-opacity": 0.72 },
      });
    } else {
      m.addLayer({
        id: "single-fill", type: "fill", source: "gsps",
        paint: { "fill-color": equityFillPaint(lensSelect.value), "fill-opacity": 0.72 },
      });
    }
    m.addLayer({
      id: "single-line", type: "line", source: "gsps",
      paint: { "line-color": "#333", "line-width": 0.7, "line-opacity": 0.5 },
    });
  }

  function initCompareMaps() {
    mapOd = createMap("map-od");
    mapEq = createMap("map-eq");
    mapOd.on("load", () => {
      addSingleLayerMap(mapOd, selectedYear, "overdraft");
      fitMap(mapOd);
      bindGspHover(mapOd, { layers: ["single-fill"], overdraftOnly: true });
    });
    mapEq.on("load", () => {
      addSingleLayerMap(mapEq, selectedYear, "equity");
      fitMap(mapEq);
      bindGspHover(mapEq, { layers: ["single-fill"], equityOnly: true });
    });
  }

  function fitMap(m) {
    if (DATA.counties?.features?.length) {
      const b = turfBbox(DATA.counties);
      if (b) m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 0 });
    }
  }

  function refreshCompareMaps(year) {
    const odLabel = document.getElementById("compare-od-label");
    const eqLabel = document.getElementById("compare-eq-label");
    const odMeta = overdraftMeta(overdraftMode);
    const eqMeta = lensMeta(lensSelect.value);
    if (odLabel) odLabel.textContent = odMeta ? odMeta.label : "Overdraft";
    if (eqLabel) eqLabel.textContent = eqMeta ? eqMeta.label : "Equity";
    [mapOd, mapEq].forEach((m, i) => {
      if (!m?.getSource("gsps")) return;
      m.getSource("gsps").setData(enrichGspData(year));
      if (m.getLayer("single-fill")) {
        m.setPaintProperty(
          "single-fill",
          "fill-color",
          i === 0 ? overdraftFillPaint(overdraftMode) : equityFillPaint(lensSelect.value),
        );
      }
    });
  }

  function hideGspTooltip() {
    gspTooltip.classList.add("hidden");
  }

  function gspDisplayName(props) {
    const gid = String(props.gsp_id || props.GSP_ID || "");
    const cat = gspById[gid];
    if (cat?.label) return cat.label;
    return props.Basin_Subbasin_Name || `GSP ${gid}`;
  }

  function formatEquityVal(lens, v) {
    if (lens === "ag_production") return `${Math.round(v).toLocaleString()} ac`;
    if (lens === "fallowed_land") return `${Number(v).toFixed(1)}%`;
    if (lens === "water_access") return `${Math.round(v)} reports (cum.)`;
    if (lens === "farm_consolidation") return `${Number(v).toFixed(1)}% large farms`;
    return String(v);
  }

  function statusLabel(std) {
    const labels = {
      approved: "Approved",
      under_review: "Under review",
      inadequate: "Inadequate",
      inadequate_under_review: "Inadequate (review)",
      state_intervention: "State intervention",
      incomplete: "Incomplete",
      pre_sgma: "Pre-SGMA",
    };
    return labels[std] || (std || "Unknown").replace(/_/g, " ");
  }

  function parseYearValues(props) {
    const raw = props.year_values;
    if (!raw) return {};
    if (typeof raw === "string") {
      try { return JSON.parse(raw); } catch { return {}; }
    }
    return raw;
  }

  function gspObservationLines(props, year, ctx = {}) {
    const lines = [`<strong>${gspDisplayName(props)}</strong>`];
    const yv = parseYearValues(props)[String(year)] || {};

    if (ctx.splitGwe) {
      const gwe = props.split_gwe_ft ?? yv.gwe_ft;
      if (gwe != null) lines.push(`Water table elevation: ${Number(gwe).toFixed(0)} ft`);
      return lines;
    }

    const showOd = ctx.overdraftOnly || (ctx.overdraftOnly !== false && showOverdraftOverlay() && !ctx.equityOnly);
    const showEq = ctx.equityOnly || (ctx.equityOnly !== false && showEquityOverlays() && !ctx.overdraftOnly);

    if (showOd && overdraftMode !== "none") {
      if (overdraftMode === "annual") {
        const v = yv.gwe_trend_4yr_ft_yr ?? props.gwe_trend_4yr_ft_yr;
        const line = formatTrendTooltip(v);
        if (line) lines.push(line);
      } else {
        const v = yv.gwe_cumulative_drop ?? props.gwe_cumulative_drop;
        if (v != null) {
          const n = Math.abs(Number(v)).toFixed(1);
          if (v > 0) lines.push(`Water table: ${n} ft below pre-2016 baseline`);
          else if (v < 0) lines.push(`Water table: ${n} ft above pre-2016 baseline`);
          else lines.push("Water table: at pre-2016 baseline");
        }
      }
    }

    if (showEq) {
      const lens = ctx.equityLens ?? lensSelect.value;
      if (lens === "gsp_status") {
        lines.push(`GSP status: ${statusLabel(yv.status_std || props.status_std)}`);
      } else if (lens !== "none") {
        const merged = { ...props, ...yv };
        const v = equityNumericValue(merged, lens);
        const meta = lensMeta(lens);
        if (v != null) lines.push(`${meta?.label || lens}: ${formatEquityVal(lens, v)}`);
      }
    }

    return lines;
  }

  function bindGspHover(m, opts = {}) {
    const layerIds = opts.layers || ["gsp-overdraft-fill", "gsp-equity-fill", "split-fill", "single-fill"];
    m.on("mousemove", (e) => {
      const activeLayers = layerIds.filter((id) => {
        if (!m.getLayer(id)) return false;
        const vis = m.getLayoutProperty(id, "visibility");
        return vis !== "none";
      });
      if (!activeLayers.length) {
        hideGspTooltip();
        m.getCanvas().style.cursor = "";
        return;
      }
      const feats = m.queryRenderedFeatures(e.point, { layers: activeLayers });
      if (!feats.length) {
        hideGspTooltip();
        m.getCanvas().style.cursor = "";
        return;
      }
      const props = feats[0].properties;
      const year = opts.year ?? selectedYear;
      gspTooltip.innerHTML = gspObservationLines(props, year, opts).join("<br>");
      gspTooltip.classList.remove("hidden");
      gspTooltip.style.left = `${e.originalEvent.clientX + 12}px`;
      gspTooltip.style.top = `${e.originalEvent.clientY + 12}px`;
      m.getCanvas().style.cursor = "pointer";
    });
    m.on("mouseleave", () => {
      hideGspTooltip();
      m.getCanvas().style.cursor = "";
    });
  }

  function equityNumericValue(props, lens) {
    const yv = props.year_values?.[String(selectedYear)] || {};
    const p = { ...props, ...yv };
    if (lens === "fallowed_land") return p.fallow_pct ?? null;
    if (lens === "water_access") return p.well_reports ?? null;
    if (lens === "farm_consolidation") return p.large_farm_share ?? null;
    if (lens === "ag_production") return p.total_ag_acres ?? null;
    return null;
  }

  function gweStressValue(props) {
    const yv = props.year_values?.[String(selectedYear)] || {};
    if (overdraftMode === "annual") return yv.gwe_trend_4yr_ft_yr ?? null;
    return yv.gwe_cumulative_drop ?? null;
  }

  function drawScatter(year) {
    const canvas = document.getElementById("scatter-canvas");
    const panel = document.getElementById("scatter-panel");
    if (!canvas || !panel) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    const lens = lensSelect.value;
    if (lens === "none" || lens === "gsp_status" || overdraftMode === "none") {
      panel.classList.remove("visible");
      return;
    }
    panel.classList.add("visible");
    const pts = [];
    for (const f of DATA.gsps?.features || []) {
      const x = equityNumericValue(f.properties, lens);
      const y = gweStressValue(f.properties);
      if (x == null || y == null) continue;
      pts.push({ x: Number(x), y: Number(y) });
    }
    if (!pts.length) return;
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const pad = 28;
    const xmin = Math.min(...xs);
    const xmax = Math.max(...xs);
    const ymin = Math.min(...ys);
    const ymax = Math.max(...ys);
    const xscale = (v) => pad + ((v - xmin) / Math.max(xmax - xmin, 1e-6)) * (w - pad * 2);
    const yscale = (v) => h - pad - ((v - ymin) / Math.max(ymax - ymin, 1e-6)) * (h - pad * 2);
    ctx.strokeStyle = "#ddd";
    ctx.beginPath();
    ctx.moveTo(pad, pad);
    ctx.lineTo(pad, h - pad);
    ctx.lineTo(w - pad, h - pad);
    ctx.stroke();
    ctx.fillStyle = TEAL;
    pts.forEach((p) => {
      ctx.beginPath();
      ctx.arc(xscale(p.x), yscale(p.y), 4, 0, Math.PI * 2);
      ctx.fill();
    });
    const xLab = document.getElementById("scatter-x-label");
    const yLab = document.getElementById("scatter-y-label");
    const eqMeta = lensMeta(lens);
    const odMeta = overdraftMeta(overdraftMode);
    if (xLab) xLab.textContent = eqMeta?.label || "Equity";
    if (yLab) yLab.textContent = odMeta?.label || "GWE stress";
  }

  function setupMap(m, year, subsLayer, opts = {}) {
    m.on("load", () => {
      addMapLayers(m, year, subsLayer, opts);
      if (DATA.counties?.features?.length) {
        const b = turfBbox(DATA.counties);
        if (b) m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 0 });
      }
    });
  }

  function turfBbox(fc) {
    let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
    for (const f of fc.features || []) {
      walkCoords(f.geometry, (lon, lat) => {
        xmin = Math.min(xmin, lon); ymin = Math.min(ymin, lat);
        xmax = Math.max(xmax, lon); ymax = Math.max(ymax, lat);
      });
    }
    return xmin < Infinity ? [xmin, ymin, xmax, ymax] : null;
  }

  function walkCoords(geom, fn) {
    if (!geom) return;
    if (geom.type === "Polygon") geom.coordinates[0].forEach((c) => fn(c[0], c[1]));
    else if (geom.type === "MultiPolygon")
      geom.coordinates.forEach((p) => p[0].forEach((c) => fn(c[0], c[1])));
  }

  function updateSubsidenceLegend() {
    const block = document.getElementById("subsidence-legend");
    const title = document.getElementById("subsidence-legend-title");
    const bar = document.getElementById("subsidence-bar");
    if (subsidenceMode === "none") {
      block.classList.add("hidden");
      return;
    }
    block.classList.remove("hidden");
    title.textContent = subsidenceMode === "annual_rate" ? "Subsidence rate" : "Cumulative subsidence";
    bar.className = subsidenceMode === "annual_rate" ? "subsidence-bar" : "subsidence-bar cumulative-bar";
    document.getElementById("legend-min").textContent =
      subsidenceMode === "annual_rate" ? "Slower (green)" : "0 ft";
    document.getElementById("legend-max").textContent =
      subsidenceMode === "annual_rate" ? "Faster (red)" : "4 ft";
  }

  function updateOverdraftLegend() {
    const block = document.getElementById("overdraft-legend");
    const leg = DATA.overdraft_legend?.[overdraftMode] || {};
    const title = document.getElementById("overdraft-legend-title");
    const bar = document.getElementById("overdraft-bar");
    const minEl = document.getElementById("overdraft-legend-min");
    const maxEl = document.getElementById("overdraft-legend-max");
    if (!block) return;
    if (overdraftMode === "none") {
      block.classList.add("hidden");
      return;
    }
    block.classList.remove("hidden");
    title.textContent = leg.title || "Overdraft";
    minEl.textContent = leg.min || "Low";
    maxEl.textContent = leg.max || "High";
    const cols = overdraftMode === "annual"
      ? ["#27ae60", "#a8d08d", "#f1c40f", "#e67e22", "#c0392b"]
      : ["#27ae60", "#e8f4ea", "#f1c40f", "#e67e22", "#6b1d1d"];
    bar.style.background = `linear-gradient(90deg, ${cols.join(", ")})`;
  }

  function updateEquityLegend() {
    const lens = lensSelect.value;
    const block = document.getElementById("effectiveness-legend");
    const title = document.getElementById("effectiveness-legend-title");
    const body = document.getElementById("effectiveness-legend-body");
    if (lens === "none") {
      block.classList.add("hidden");
      return;
    }
    block.classList.remove("hidden");
    body.innerHTML = "";

    const labels = {
      gsp_status: "GSP status",
      fallowed_land: "Fallowed land",
      water_access: "Water access (cumulative)",
      farm_consolidation: "Farm consolidation (NASS)",
      ag_production: "Ag production (acres)",
    };
    title.textContent = labels[lens] || "Equity lens";

    if (lens === "gsp_status") {
      (DATA.status_legend || []).forEach((item) => {
        const row = document.createElement("div");
        row.className = "legend-item";
        row.innerHTML = `<span class="legend-swatch" style="background:${item.color}"></span><span>${item.label}</span>`;
        body.appendChild(row);
      });
    } else {
      const gradients = {
        fallowed_land: ["#e8f4ea", "#7cb87c", "#c8922a", "#c0392b", "#6b1d1d"],
        water_access: ["#f5f8fa", "#d4e6f1", "#85c1e9", "#3498db", "#1a5276"],
        farm_consolidation: ["#27ae60", "#a8d08d", "#f1c40f", "#e67e22", "#c0392b"],
        ag_production: ["#e8f4ea", "#a8d08d", "#f1c40f", "#e67e22", "#6b1d1d"],
      };
      const labels2 = {
        fallowed_land: ["0%", "10%", "20%", "30%", "40%+"],
        water_access: ["Few reports", "", "", "", "Many (cumulative)"],
        farm_consolidation: [
          DATA.farm_consolidation_legend?.min || "Fewer large ops",
          "",
          "",
          "",
          DATA.farm_consolidation_legend?.max || "More large ops (500+ ac)",
        ],
        ag_production: [
          DATA.ag_production_legend?.min || "Less cropland",
          "",
          "",
          "",
          DATA.ag_production_legend?.max || "More cropland",
        ],
      };
      const bar = document.createElement("div");
      bar.className = "subsidence-bar";
      const cols = gradients[lens] || gradients.fallowed_land;
      bar.style.background = `linear-gradient(90deg, ${cols.join(", ")})`;
      body.appendChild(bar);
      const lbl = document.createElement("div");
      lbl.className = "bar-labels";
      const ls = labels2[lens] || ["Low", "", "", "", "High"];
      lbl.innerHTML = `<span>${ls[0]}</span><span>${ls[4]}</span>`;
      body.appendChild(lbl);
    }
  }

  function refreshLayerVisibility(m) {
    if (!m) return;
    const splitOn = document.getElementById("toggle-split")?.checked;
    const cmpSide = compareActive() && compareMode === "side_by_side" && !splitOn;
    const cmpScatter = compareActive() && compareMode === "scatter" && !splitOn;
    const overlay = !cmpSide && !cmpScatter && (compareMode === "overlay" || !compareActive());
    const eqVis = overlay && showEquityOverlays() ? "visible" : cmpScatter ? "none" : "none";
    const odVis = (overlay && showOverdraftOverlay()) || cmpScatter ? "visible" : "none";
    const lineVis = eqVis === "visible" || odVis === "visible" ? "visible" : "none";
    if (m.getLayer("gsp-equity-fill")) m.setLayoutProperty("gsp-equity-fill", "visibility", eqVis);
    if (m.getLayer("gsp-overdraft-fill")) m.setLayoutProperty("gsp-overdraft-fill", "visibility", odVis);
    if (m.getLayer("gsp-line")) m.setLayoutProperty("gsp-line", "visibility", lineVis);
  }

  function refreshMap(m, year) {
    if (!m?.isStyleLoaded()) return;
    const subsLayer = subsidenceLayerForYear(year);

    if (m.getLayer("subsidence-raster")) {
      if (subsidenceMode === "none" || !subsLayer) {
        m.setLayoutProperty("subsidence-raster", "visibility", "none");
      } else {
        m.setLayoutProperty("subsidence-raster", "visibility", "visible");
        m.getSource("subsidence").updateImage({ url: layerPath(subsLayer), coordinates: coords });
      }
    } else if (subsLayer && subsidenceMode !== "none") {
      m.addSource("subsidence", { type: "image", url: layerPath(subsLayer), coordinates: coords });
      m.addLayer({
        id: "subsidence-raster", type: "raster", source: "subsidence",
        paint: { "raster-opacity": 0.88 },
      }, "gsp-equity-fill");
    }

    if (m.getSource("gsps")) {
      m.getSource("gsps").setData(enrichGspData(year));
      if (m.getLayer("gsp-overdraft-fill")) {
        m.setPaintProperty("gsp-overdraft-fill", "fill-color", overdraftFillPaint(overdraftMode));
      }
      if (m.getLayer("gsp-equity-fill")) {
        m.setPaintProperty("gsp-equity-fill", "fill-color", equityFillPaint(lensSelect.value));
      }
      refreshLayerVisibility(m);
    }
    if (m.getSource("wells")) {
      m.getSource("wells").setData({ type: "FeatureCollection", features: buildWellFeatures(year) });
    }
  }

  function splitGwePaint() {
    const lo = splitGweScale.min ?? 50;
    const hi = splitGweScale.max ?? 250;
    const mid = lo + (hi - lo) * 0.5;
    return ["interpolate", ["linear"], ["coalesce", ["get", "split_gwe_ft"], lo],
      lo, "#c0392b", mid, "#f1c40f", hi, "#27ae60"];
  }

  function buildSplitGspGeo(year) {
    const fallback = splitGweScale.min ?? 50;
    const feats = (DATA.gsps?.features || []).map((f) => {
      const props = { ...f.properties };
      const gwe = props.year_values?.[String(year)]?.gwe_ft;
      return {
        ...f,
        properties: { ...props, split_gwe_ft: gwe ?? fallback },
      };
    });
    return { type: "FeatureCollection", features: feats };
  }

  function addSplitMapLayers(m, year) {
    if (DATA.counties) {
      m.addSource("counties", { type: "geojson", data: DATA.counties });
      m.addLayer({
        id: "county-fill", type: "fill", source: "counties",
        paint: { "fill-color": TEAL, "fill-opacity": 0.02 },
      });
      m.addLayer({
        id: "county-line", type: "line", source: "counties",
        paint: { "line-color": TEAL, "line-width": 2, "line-opacity": 0.85 },
      });
    }
    m.addSource("gsps", { type: "geojson", data: buildSplitGspGeo(year) });
    m.addLayer({
      id: "split-fill", type: "fill", source: "gsps",
      paint: { "fill-color": splitGwePaint(), "fill-opacity": 1 },
    });
    m.addLayer({
      id: "split-line", type: "line", source: "gsps",
      paint: { "line-color": "#333", "line-width": 0.7, "line-opacity": 0.5 },
    });
  }

  function setupSplitMap(m, year) {
    m.on("load", () => {
      addSplitMapLayers(m, year);
      bindGspHover(m, { layers: ["split-fill"], year, splitGwe: true });
      if (DATA.counties?.features?.length) {
        const b = turfBbox(DATA.counties);
        if (b) m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, duration: 0 });
      }
    });
  }

  function updateSplitChrome() {
    const cap = document.getElementById("split-caption");
    if (cap) cap.textContent = splitCmp.caption || "";
    const preTitle = document.getElementById("split-label-pre");
    const postTitle = document.getElementById("split-label-post");
    const preSub = document.getElementById("split-sub-pre");
    const postSub = document.getElementById("split-sub-post");
    if (preTitle) preTitle.textContent = splitCmp.pre_title || "Before SGMA";
    if (postTitle) postTitle.textContent = splitCmp.post_title || "After SGMA";
    if (preSub) preSub.textContent = splitCmp.pre_subtitle || "Pre-2016 average · water level (ft)";
    if (postSub) postSub.textContent = splitCmp.post_subtitle || "2024 average · water level (ft)";
    const legTitle = document.getElementById("split-legend-title");
    const legMin = document.getElementById("split-legend-min");
    const legMid = document.getElementById("split-legend-mid");
    const legMax = document.getElementById("split-legend-max");
    const legNote = document.getElementById("split-legend-note");
    if (legTitle) legTitle.textContent = splitCmp.legend_title || "Groundwater elevation (ft)";
    if (legMin) legMin.textContent = splitCmp.legend_min || "Shallower";
    if (legMid) legMid.textContent = splitCmp.legend_mid || "Mid";
    if (legMax) legMax.textContent = splitCmp.legend_max || "Deeper";
    if (legNote) legNote.textContent = splitCmp.legend_note || "";
  }

  function enterSplitMode() {
    playing = false;
    playBtn.textContent = "▶ Play";
    document.getElementById("map").style.display = "none";
    document.getElementById("compare-container")?.classList.remove("active");
    document.getElementById("split-container").classList.add("active");
    document.getElementById("hud-body").classList.add("split-hidden");
    document.getElementById("legend-panel").classList.add("split-hidden");
    updateSplitChrome();

    const preYear = splitCmp.pre_year || 2014;
    const postYear = splitCmp.post_year || 2024;

    if (!mapPre) {
      mapPre = createMap("map-pre");
      mapPost = createMap("map-post");
      setupSplitMap(mapPre, preYear);
      setupSplitMap(mapPost, postYear);
    } else {
      if (mapPre.getSource("gsps")) mapPre.getSource("gsps").setData(buildSplitGspGeo(preYear));
      if (mapPost.getSource("gsps")) mapPost.getSource("gsps").setData(buildSplitGspGeo(postYear));
    }
  }

  function exitSplitMode() {
    document.getElementById("map").style.display = "block";
    document.getElementById("split-container").classList.remove("active");
    document.getElementById("hud-body").classList.remove("split-hidden");
    document.getElementById("legend-panel").classList.remove("split-hidden");
    applyViewLayout();
  }

  function updateWellStats(year) {
    const feats = buildWellFeatures(year);
    const droughtN = feats.filter((f) => f.properties.drought_year).length;
    const cumTotal = (DATA.gsps?.features || []).reduce((sum, f) => {
      const yv = f.properties?.year_values?.[String(year)] || {};
      return sum + (yv.well_reports || 0);
    }, 0);
    wellStats.textContent =
      `Dry wells (${year}): ${feats.length} this year · ${cumTotal} cumulative through ${year} · ${droughtN} drought / ${feats.length - droughtN} non-drought · bias-adjusted`;
  }

  function applyYear(year) {
    selectedYear = year;
    yearLabel.textContent = year;
    slider.value = year;
    refreshMap(map, year);
    updateWellStats(year);
    applyViewLayout();
  }

  setupMap(map, selectedYear, subsidenceLayerForYear(selectedYear));
  map.on("load", () => {
    bindGspHover(map);
    applyYear(selectedYear);
    updateSubsidenceLegend();
    updateOverdraftLegend();
    updateEquityLegend();
    applyViewLayout();
  });

  slider.addEventListener("input", () => {
    if (document.getElementById("toggle-split").checked) return;
    applyYear(parseInt(slider.value, 10));
  });

  playBtn.addEventListener("click", () => {
    if (document.getElementById("toggle-split").checked) return;
    playing = !playing;
    playBtn.textContent = playing ? "⏸ Pause" : "▶ Play";
    if (!playing) return;
    (function tick() {
      if (!playing) return;
      let idx = sliderYears.indexOf(parseInt(slider.value, 10));
      idx = idx < 0 ? 0 : (idx + 1) % sliderYears.length;
      applyYear(sliderYears[idx]);
      setTimeout(tick, 900);
    })();
  });

  subsidenceSelect.addEventListener("change", (e) => {
    subsidenceMode = e.target.value;
    updateSubsidenceLegend();
    refreshMap(map, selectedYear);
  });

  overdraftSelect?.addEventListener("change", (e) => {
    overdraftMode = e.target.value;
    updateOverdraftDesc();
    updateOverdraftLegend();
    if (map?.getLayer("gsp-overdraft-fill")) {
      map.setPaintProperty("gsp-overdraft-fill", "fill-color", overdraftFillPaint(overdraftMode));
      refreshLayerVisibility(map);
    }
    applyViewLayout();
  });

  lensSelect.addEventListener("change", () => {
    updateLensDesc();
    updateEquityLegend();
    if (map?.getLayer("gsp-equity-fill")) {
      map.setPaintProperty("gsp-equity-fill", "fill-color", equityFillPaint(lensSelect.value));
      refreshLayerVisibility(map);
    }
    applyViewLayout();
  });

  compareSelect?.addEventListener("change", (e) => {
    compareMode = e.target.value;
    updateCompareDesc();
    applyViewLayout();
  });

  document.getElementById("toggle-counties").addEventListener("change", (e) => {
    const v = e.target.checked ? "visible" : "none";
    if (map.getLayer("county-fill")) map.setLayoutProperty("county-fill", "visibility", v);
    if (map.getLayer("county-line")) map.setLayoutProperty("county-line", "visibility", v);
    [mapPre, mapPost].forEach((m) => {
      if (!m) return;
      if (m.getLayer("county-fill")) m.setLayoutProperty("county-fill", "visibility", v);
      if (m.getLayer("county-line")) m.setLayoutProperty("county-line", "visibility", v);
    });
  });

  document.getElementById("toggle-well-dots").addEventListener("change", (e) => {
    const v = e.target.checked ? "visible" : "none";
    if (map.getLayer("wells-circle")) map.setLayoutProperty("wells-circle", "visibility", v);
  });

  document.getElementById("toggle-split").addEventListener("change", (e) => {
    if (e.target.checked) enterSplitMode();
    else exitSplitMode();
  });

  window.SinkingValleyExplorer = {
    DATA,
    splitCmp,
    STATUS_COLORS,
    TEAL,
    resizeMaps() {
      [map, mapPre, mapPost, mapOd, mapEq].forEach((m) => {
        try { m?.resize(); } catch (_) { /* ignore */ }
      });
    },
  };
})();
