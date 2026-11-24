(() => {
  const API_BASE = window.CA_DASH_API_BASE || "/ca_dash/api";
  const state = {
    indicator: "chronic",
    districtCds: "",
    districtShort: "DSUSD",
    schoolCds: "",
    subgroup: "ALL",
    selectedSubgroups: [],
    reportingYear: null,
    availableYears: [],
    selectedYears: [],
    yearColors: {},
    official2026: {},
    customYearColors: {},
    schools: [],
    subgroups: [],
    selectedDrill: null,
    report: null,
    districtExpanded: true,
    overlayDetail: null,
    overlayShowAllSubgroups: false,
  };

  const YEAR_COLORS_KEY = "dsusdYearColors";
  const YEAR_SELECTION_PREFIX = "caDashSelectedYears";

  const DEFAULT_YEAR_COLORS = {
    2020: "#64748b",
    2021: "#475569",
    2022: "#6366f1",
    2023: "#7c3aed",
    2024: "#0891b2",
    2025: "#059669",
    2026: "#d97706",
    202599: "#f59e0b",
  };

  const ESTIMATE_2026_YEAR = 202599;

  const els = {
    indicator: document.getElementById("indicator"),
    subgroupSection: document.getElementById("subgroup-section"),
    subgroupSelect: document.getElementById("subgroup-select"),
    subgroupMulti: document.getElementById("subgroup-multi"),
    subgroupDropdownBtn: document.getElementById("subgroup-dropdown-btn"),
    subgroupDropdownPanel: document.getElementById("subgroup-dropdown-panel"),
    subgroupDropdownSummary: document.getElementById("subgroup-dropdown-summary"),
    subgroupChips: document.getElementById("subgroup-chips"),
    subgroupCount: document.getElementById("subgroup-count"),
    subgroupSelectAll: document.getElementById("subgroup-select-all"),
    subgroupSelectNone: document.getElementById("subgroup-select-none"),
    schoolSelect: document.getElementById("school-select"),
    reportingYearBadge: document.getElementById("reporting-year-badge"),
    yearCheckboxes: document.getElementById("year-checkboxes"),
    yearDropdownBtn: document.getElementById("year-dropdown-btn"),
    yearCheckboxPanel: document.getElementById("year-checkbox-panel"),
    yearDropdownSummary: document.getElementById("year-dropdown-summary"),
    yearSection: document.getElementById("year-section"),
    yearLegendToolbar: document.getElementById("year-legend-toolbar"),
    contentTitle: document.getElementById("content-title"),
    contentSubtitle: document.getElementById("content-subtitle"),
    schoolGridsContainer: document.getElementById("school-grids-container"),
    districtGrid: document.getElementById("district-grid"),
    districtLogic: document.getElementById("district-logic"),
    districtGridPanel: document.getElementById("district-grid-panel"),
    districtToggle: document.getElementById("district-toggle"),
    districtSubgroupToggle: document.getElementById("district-subgroup-toggle"),
    districtGridTitle: document.getElementById("district-grid-title"),
    districtExportSubtitle: document.getElementById("district-export-subtitle"),
    districtExportTarget: document.getElementById("district-export-target"),
    districtSaveJpg: document.getElementById("district-save-jpg"),
    districtNoColor: document.getElementById("district-no-color"),
    overlayBackdrop: document.getElementById("overlay-backdrop"),
    schoolOverlay: document.getElementById("school-overlay"),
    overlayTitle: document.getElementById("overlay-title"),
    overlaySubtitle: document.getElementById("overlay-subtitle"),
    overlayBody: document.getElementById("overlay-body"),
    overlayClose: document.getElementById("overlay-close"),
    drillPanel: document.getElementById("drill-panel"),
    drillTitle: document.getElementById("drill-title"),
    drillContext: document.getElementById("drill-context"),
    drillClose: document.getElementById("drill-close"),
    entityList: document.getElementById("entity-list"),
    sidebarToggle: document.getElementById("sidebar-toggle"),
    appLayout: document.querySelector(".app-layout"),
  };

  async function fetchJson(url) {
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    if (!ct.includes("application/json")) {
      const text = await res.text();
      const snippet = text.replace(/\s+/g, " ").trim().slice(0, 160);
      throw new Error(
        `Expected JSON from ${url} (HTTP ${res.status}). ${snippet || res.statusText}`
      );
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function qs(params) {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") p.set(k, v);
    });
    return p.toString();
  }

  function loadStoredYearColors() {
    try {
      return JSON.parse(localStorage.getItem(YEAR_COLORS_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveStoredYearColors(colors) {
    localStorage.setItem(YEAR_COLORS_KEY, JSON.stringify(colors));
  }

  function yearSelectionStorageKey(indicator = state.indicator) {
    return `${YEAR_SELECTION_PREFIX}:${indicator || "chronic"}`;
  }

  function loadStoredSelectedYears(indicator = state.indicator) {
    try {
      const raw = sessionStorage.getItem(yearSelectionStorageKey(indicator));
      if (!raw) return [];
      return normalizeYearList(JSON.parse(raw));
    } catch {
      return [];
    }
  }

  function saveStoredSelectedYears(years, indicator = state.indicator) {
    const normalized = normalizeYearList(years);
    if (!normalized.length) return;
    sessionStorage.setItem(yearSelectionStorageKey(indicator), JSON.stringify(normalized));
  }

  function yearHex(year) {
    const y = Number(year);
    const custom =
      state.customYearColors[y] ||
      state.customYearColors[year] ||
      state.customYearColors[String(y)];
    if (custom) return custom;

    const reportPalette = state.report?.year_palette || {};
    const fromReport = reportPalette[y]?.hex || reportPalette[year]?.hex;
    if (fromReport) return fromReport;

    const cfgPalette = state.yearColors || {};
    const fromCfg =
      (typeof cfgPalette[y] === "string" ? cfgPalette[y] : cfgPalette[y]?.hex) ||
      (typeof cfgPalette[String(y)] === "string" ? cfgPalette[String(y)] : cfgPalette[String(y)]?.hex);
    if (fromCfg) return fromCfg;

    return DEFAULT_YEAR_COLORS[y] || DEFAULT_YEAR_COLORS[year] || "#334155";
  }

  function textOnYearColor(hex) {
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return "#ffffff";
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return lum > 0.62 ? "#0b1a30" : "#ffffff";
  }

  function applyYearTagStyle(el, year) {
    const bg = yearHex(year);
    el.style.background = bg;
    el.style.color = textOnYearColor(bg);
    el.style.borderColor = bg;
  }

  function setYearColor(year, hex, refreshToggles = true) {
    state.customYearColors[year] = hex;
    state.customYearColors[Number(year)] = hex;
    saveStoredYearColors(state.customYearColors);
    if (refreshToggles) renderYearToggles();
    syncYearLegendToolbar();
    if (state.report) {
      renderSchoolGrids(state.report);
      renderDistrictGrid(state.report);
      refreshTrajectoryOverlays();
      if (!els.drillPanel.classList.contains("hidden") && state.selectedDrill) {
        onCellClick(state.selectedDrill.box, state.selectedDrill.rtype, state.selectedDrill.typeKey);
      }
    }
  }

  function buildYearLegendElement() {
    const div = document.createElement("div");
    div.className = "year-legend year-legend-export";
    div.setAttribute("role", "list");
    div.setAttribute("aria-label", "Reporting year colors");
    displayReportingYears().forEach((y) => {
      const item = document.createElement("span");
      item.className = "year-legend-item";
      item.setAttribute("role", "listitem");
      const swatch = document.createElement("span");
      swatch.className = "year-swatch";
      swatch.style.background = yearHex(y);
      const label = document.createElement("span");
      label.textContent = yearLabel(y);
      item.appendChild(swatch);
      item.appendChild(label);
      div.appendChild(item);
    });
    return div;
  }

  function appendYearLegendToExportTarget(exportTarget) {
    if (!exportTarget) return;
    exportTarget.querySelectorAll(".year-legend-export").forEach((el) => el.remove());
    if (!multiYearMode()) return;
    const legend = buildYearLegendElement();
    // insertBefore requires a direct child of exportTarget (district chart lives in .grid-wrapper).
    const anchor = Array.from(exportTarget.children).find((el) => {
      if (el.classList.contains("grid-block") || el.classList.contains("grid-wrapper")) return true;
      return el.classList.contains("five-by-five");
    });
    if (anchor) {
      exportTarget.insertBefore(legend, anchor);
    } else {
      exportTarget.appendChild(legend);
    }
  }

  function yearLabel(year) {
    const palette = state.report?.year_palette || state.yearColors || {};
    const y = Number(year);
    if (y === ESTIMATE_2026_YEAR) return "2026";
    if (palette[year]?.label) return String(palette[year].label).replace(/ \(Estimates\)$/i, "");
    if (palette[y]?.label) return String(palette[y].label).replace(/ \(Estimates\)$/i, "");
    return String(year);
  }

  function formatSchoolCds(cds) {
    const digits = String(cds ?? "").replace(/\D/g, "");
    return digits ? digits.padStart(14, "0") : "";
  }

  function normalizeYear(value) {
    const y = Number(value);
    return Number.isNaN(y) ? null : y;
  }

  function normalizeYearList(years) {
    return [...new Set((years || []).map(normalizeYear).filter((y) => y !== null))].sort(
      (a, b) => a - b
    );
  }

  function selectedReportingYears() {
    return normalizeYearList(state.selectedYears);
  }

  function yearsPresentInReport(report = state.report) {
    if (!report) return [];
    const years = new Set();
    const pushEntity = (entity) => {
      const y = normalizeYear(entity?.reportingyear);
      if (y !== null) years.add(y);
    };
    const grids = report.school_grids || (report.school_grid ? [report.school_grid] : []);
    grids.forEach((grid) => {
      (grid.cells || []).forEach((row) => {
        row.forEach((cell) => (cell.schools || []).forEach(pushEntity));
      });
    });
    const district = report.district_grid;
    if (district?.cells) {
      district.cells.forEach((row) => {
        row.forEach((cell) => (cell.schools || []).forEach(pushEntity));
      });
    }
    return [...years].sort((a, b) => a - b);
  }

  function displayReportingYears() {
    const selected = selectedReportingYears();
    if (selected.length > 1) return selected;
    const meta = normalizeYearList(state.report?.reporting_years);
    if (meta.length > 1) return meta;
    const inGrid = yearsPresentInReport();
    if (inGrid.length > 1) return inGrid;
    return selected.length ? selected : meta;
  }

  /** @deprecated use selectedReportingYears / displayReportingYears */
  function activeReportingYears() {
    return displayReportingYears();
  }

  function reportingYearsParam() {
    const years = selectedReportingYears();
    if (!years.length) return "";
    return years.join(",");
  }

  function apiParams(extra = {}) {
    const params = { indicator: state.indicator, ...extra };
    const years = reportingYearsParam();
    if (years) params.reporting_years = years;
    return params;
  }

  function gridForDrill(rtype, typeKey) {
    if (!state.report) return null;
    if (rtype === "S") {
      const grids = state.report.school_grids || (state.report.school_grid ? [state.report.school_grid] : []);
      if (typeKey) {
        return grids.find((g) => g.type_key === typeKey) || grids[0] || null;
      }
      return grids[0] || null;
    }
    return state.report.district_grid || null;
  }

  function formatDrillCellContext(grid, box) {
    if (!grid?.cells) return "";
    let row;
    let col;
    for (let ri = 0; ri < grid.cells.length; ri++) {
      for (let ci = 0; ci < grid.cells[ri].length; ci++) {
        if (Number(grid.cells[ri][ci].box) === Number(box)) {
          row = ri;
          col = ci;
          break;
        }
      }
      if (row != null) break;
    }
    if (row == null || col == null) return "";

    const statusDesc = grid.status_descriptions?.[row] || grid.status_labels?.[row] || "";
    const changeDesc = grid.change_descriptions?.[col] || grid.change_labels?.[col] || "";
    const parts = [];
    if (statusDesc) parts.push(`Status: ${statusDesc}`);
    if (changeDesc) parts.push(`Change: ${changeDesc}`);
    return parts.join(" · ");
  }

  function multiYearMode() {
    return displayReportingYears().length > 1;
  }

  function shortName(name, max = 24) {
    if (!name || name.length <= max) return name;
    return name.slice(0, max - 1) + "…";
  }

  function fmt(v) {
    return v === null || v === undefined || v === "" ? "N/A" : v;
  }

  function colorClass(hex) {
    return (
      {
        "#e21736": "color-red",
        "#f58220": "color-orange",
        "#ffce00": "color-yellow",
        "#7fc31c": "color-green",
        "#0071bc": "color-blue",
      }[hex?.toLowerCase()] || ""
    );
  }

  function slugForFilename(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
  }

  function indicatorLabel() {
    return els.indicator?.selectedOptions?.[0]?.textContent?.trim() || state.indicator;
  }

  function selectedSchoolLabel() {
    const school = state.schools.find((s) => s.cds === state.schoolCds);
    return school?.name || "";
  }

  function reportingYearsLabel() {
    const years = displayReportingYears();
    if (!years.length) return "";
    if (years.length === 1) return yearLabel(years[0]);
    return years.map((y) => yearLabel(y)).join(", ");
  }

  function studentGroupSummary() {
    if (state.schoolCds) {
      if (!state.subgroups.length) return "No groups";
      if (!state.selectedSubgroups.length) return "No groups selected";
      if (state.selectedSubgroups.length === state.subgroups.length) return "All student groups";
      if (state.selectedSubgroups.length === 1) return subgroupLabel(state.selectedSubgroups[0]);
      return `${state.selectedSubgroups.length} student groups`;
    }
    if (state.subgroup === "ALL") return "All Students";
    return subgroupLabel(state.subgroup) || state.subgroup;
  }

  function buildSelectionContext() {
    const parts = [indicatorLabel(), studentGroupSummary(), reportingYearsLabel()];
    if (state.schoolCds) {
      parts.unshift(selectedSchoolLabel() || "School");
    } else {
      parts.unshift(state.districtShort || "District");
    }
    return parts.filter(Boolean).join(" · ");
  }

  function formatExportSubtitle(chartTitle) {
    return [chartTitle, buildSelectionContext()].filter(Boolean).join(" · ");
  }

  function fillGridPanelHeader(container, grid, report) {
    const primary = grid.title || "Chart";
    const badge =
      grid.type_label ||
      (report.view_mode === "school_subgroups" ? "Student group placement" : "Schools by type");
    container.innerHTML = "";
    const h2 = document.createElement("h2");
    h2.textContent = primary;
    const context = document.createElement("p");
    context.className = "grid-panel-context";
    context.textContent = buildSelectionContext();
    const meta = document.createElement("span");
    meta.className = "grid-panel-meta";
    meta.textContent = badge;
    container.appendChild(h2);
    container.appendChild(context);
    container.appendChild(meta);
  }

  function updatePageHeader(report) {
    const indicator = indicatorLabel();
    const isSchool = report?.view_mode === "school_subgroups";
    if (els.contentTitle) {
      els.contentTitle.textContent = isSchool
        ? `${selectedSchoolLabel() || "School"} — ${indicator}`
        : `${state.districtShort} — ${indicator}`;
    }
    if (els.contentSubtitle) {
      const mode = isSchool ? "Student groups on the 5×5 grid" : "Schools on the district 5×5 map";
      els.contentSubtitle.textContent = `${mode} · ${buildSelectionContext()}`;
      if (multiYearMode() && els.contentSubtitle) {
        els.contentSubtitle.insertAdjacentHTML("beforeend", renderYearLegend());
      }
    }
  }

  function updateDistrictHeader(report) {
    if (!els.districtGridTitle) return;
    const isSchool = report?.view_mode === "school_subgroups";
    els.districtGridTitle.textContent = isSchool
      ? `${state.districtShort} district (comparison)`
      : `${state.districtShort} district map`;
    if (els.districtGridContext) {
      els.districtGridContext.textContent = buildSelectionContext();
    }
  }

  function chartJpgFilename(chartTitle) {
    const district = slugForFilename(state.districtShort) || "district";
    const ind = slugForFilename(state.indicator) || "indicator";
    const chart = slugForFilename(chartTitle) || "chart";
    const yrs =
      state.selectedYears.map((y) => (y === ESTIMATE_2026_YEAR ? "2026-est" : String(y))).join("-") ||
      "year";
    return `${district}-${ind}-${chart}-${yrs}.jpg`;
  }

  function equalizeJpgExportRowHeights(root) {
    root.querySelectorAll(".five-by-five tbody tr").forEach((tr) => {
      const tds = tr.querySelectorAll("td");
      const cells = tr.querySelectorAll("td .grid-cell");
      if (!cells.length) return;

      tds.forEach((td) => {
        td.style.height = "";
        td.style.verticalAlign = "top";
      });
      cells.forEach((cell) => {
        cell.style.height = "100%";
        cell.style.boxSizing = "border-box";
        if (cell.classList.contains("na")) {
          cell.style.minHeight = "48px";
        } else {
          cell.style.minHeight = "90px";
        }
      });

      let max = 90;
      cells.forEach((cell) => {
        const h = cell.getBoundingClientRect().height;
        if (h > max) max = h;
      });
      max = Math.ceil(max);

      tds.forEach((td) => {
        td.style.height = `${max}px`;
      });
      cells.forEach((cell) => {
        cell.style.minHeight = `${max}px`;
      });
    });
  }

  function prepareJpgExportClone(root) {
    if (!root) return;
    root.style.overflow = "visible";
    root.style.maxHeight = "none";

    root.querySelectorAll(".grid-export-heading").forEach((el) => {
      el.style.display = "block";
      el.style.overflow = "visible";
    });
    root.querySelectorAll(".grid-export-subtitle, .grid-logic, .grid-panel-context").forEach((el) => {
      el.style.overflow = "visible";
      el.style.whiteSpace = "normal";
    });

    root.querySelectorAll(".cell-tag, .school-tag, .subgroup-tag").forEach((tag) => {
      const full =
        tag.dataset.fullName ||
        (tag.title || "").split(" · ")[0] ||
        tag.textContent ||
        "";
      const nameSpan = tag.querySelector(".tag-name");
      if (nameSpan && full) nameSpan.textContent = full;
      tag.style.overflow = "visible";
      tag.style.textOverflow = "clip";
      tag.style.whiteSpace = "normal";
      tag.style.wordBreak = "break-word";
      tag.style.height = "auto";
      tag.style.maxWidth = "none";
      if (tag.classList.contains("year-tagged")) {
        tag.style.display = "block";
      }
    });

    root.querySelectorAll(".tag-name").forEach((span) => {
      span.style.overflow = "visible";
      span.style.textOverflow = "clip";
      span.style.whiteSpace = "normal";
      span.style.wordBreak = "break-word";
    });

    root.querySelectorAll(".grid-cell").forEach((cell) => {
      cell.style.overflow = "visible";
      cell.style.width = "100%";
    });

    root.querySelectorAll(".col-desc, .row-desc, .col-label, .row-label, .axis-title, .axis-sub").forEach((el) => {
      el.style.whiteSpace = "normal";
      el.style.overflow = "visible";
    });

    root.querySelectorAll(".status-label .row-desc, .five-by-five thead .col-desc").forEach((el) => {
      el.style.whiteSpace = "normal";
    });

    root.querySelectorAll(".grid-trajectory-overlay").forEach((svg) => {
      svg.style.removeProperty("display");
      svg.style.overflow = "visible";
    });

    const gridBlock = root.querySelector(".grid-block");
    if (gridBlock) {
      gridBlock.style.overflow = "visible";
    }

    equalizeJpgExportRowHeights(root);
  }

  async function saveGridAsJpg(exportTarget, chartTitle, triggerBtn) {
    if (!exportTarget) return;
    if (typeof html2canvas !== "function") {
      alert("Image export could not load. Check your network connection and refresh the page.");
      return;
    }
    const filename = chartJpgFilename(chartTitle);
    const prevLabel = triggerBtn?.textContent;
    if (triggerBtn) {
      triggerBtn.disabled = true;
      triggerBtn.textContent = "Saving…";
    }
    exportTarget.setAttribute("data-jpg-export", "1");
    try {
      if (document.fonts?.ready) await document.fonts.ready;
      const canvas = await html2canvas(exportTarget, {
        backgroundColor: "#ffffff",
        scale: 2,
        logging: false,
        useCORS: true,
        scrollX: 0,
        scrollY: -window.scrollY,
        ignoreElements: (el) => el.classList?.contains("grid-save-jpg-btn"),
        onclone: (clonedDoc, clonedElement) => {
          const root =
            clonedElement ||
            clonedDoc.querySelector('[data-jpg-export="1"]') ||
            clonedDoc.querySelector(".grid-panel-export-target") ||
            clonedDoc.querySelector("#district-export-target");
          prepareJpgExportClone(root);
        },
      });
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
      if (!blob) throw new Error("Could not create image.");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(err.message || "Could not save chart image.");
    } finally {
      exportTarget.removeAttribute("data-jpg-export");
      if (triggerBtn) {
        triggerBtn.disabled = false;
        triggerBtn.textContent = prevLabel || "Save JPG";
      }
    }
  }

  function attachSaveJpgButton(headerActions, exportTarget, chartTitle) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-ghost grid-save-jpg-btn";
    btn.textContent = "Save JPG";
    btn.title = "Download this chart as JPEG";
    btn.addEventListener("click", () => saveGridAsJpg(exportTarget, chartTitle, btn));
    headerActions.appendChild(btn);
  }

  function renderGridTable(gridData, gridType, viewMode, typeKey) {
    const wrap = document.createElement("div");
    wrap.className = "grid-block";

    const logic = document.createElement("p");
    logic.className = "grid-logic";
    logic.textContent = gridData.logic_summary || "";
    wrap.appendChild(logic);

    const table = document.createElement("table");
    table.className = "five-by-five";

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "grid-trajectory-overlay");
    svg.setAttribute("aria-hidden", "true");
    wrap.appendChild(svg);

    const thead = document.createElement("thead");
    const cornerRow = document.createElement("tr");
    const axisStatus = gridData.axis_status_title || "Status Level";
    const axisChange = gridData.axis_change_title || "Change Level";
    cornerRow.innerHTML = `<th class="corner-axis" rowspan="2"><span class="axis-title">${axisStatus}</span><span class="axis-sub">(row →)</span></th><th class="corner-axis" colspan="5"><span class="axis-title">${axisChange}</span><span class="axis-sub">(column ↓)</span></th>`;
    thead.appendChild(cornerRow);

    const labelRow = document.createElement("tr");
    gridData.change_labels.forEach((label, i) => {
      const th = document.createElement("th");
      th.innerHTML = `<span class="col-label">${label}</span><span class="col-desc">${gridData.change_descriptions?.[i] || ""}</span>`;
      labelRow.appendChild(th);
    });
    thead.appendChild(labelRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    gridData.cells.forEach((row, ri) => {
      const tr = document.createElement("tr");
      const statusTh = document.createElement("th");
      statusTh.className = "status-label";
      statusTh.innerHTML = `<span class="row-label">${gridData.status_labels[ri]}</span><span class="row-desc">${gridData.status_descriptions?.[ri] || ""}</span>`;
      tr.appendChild(statusTh);

      row.forEach((cell) => {
        const td = document.createElement("td");
        const div = document.createElement("div");
        div.className = "grid-cell " + colorClass(cell.color_hex);
        if (cell.na) div.classList.add("na");
        if (!cell.na) div.style.backgroundColor = cell.color_hex;
        div.dataset.box = cell.box || "";
        div.dataset.rtype = gridType === "school" ? "S" : "D";
        div.dataset.typeKey = typeKey || "";

        if (cell.highlighted) div.classList.add("highlighted");

        const tags = document.createElement("div");
        tags.className = "cell-tags";
        (cell.schools || []).forEach((entity) => {
          const tag = document.createElement("button");
          tag.type = "button";
          tag.className = "cell-tag" + (entity.highlighted ? " active" : "");
          const entityYear = entity.reportingyear;
          const showYear = multiYearMode() && entityYear;
          if (showYear) {
            tag.classList.add("year-tagged");
            applyYearTagStyle(tag, entityYear);
          }
          tag.dataset.fullName = entity.name || "";
          const nameSpan = document.createElement("span");
          nameSpan.className = "tag-name";
          nameSpan.textContent = showYear
            ? `${shortName(entity.name)} · ${yearLabel(entityYear)}`
            : shortName(entity.name);
          tag.appendChild(nameSpan);
          tag.title = showYear
            ? `${entity.name} · ${yearLabel(entityYear)}`
            : entity.name;
          tag.addEventListener("click", (e) => {
            e.stopPropagation();
            e.preventDefault();
            if (viewMode === "school_subgroups") return;
            if (entity.rtype === "D") return;
            const cds = formatSchoolCds(entity.cds);
            if (cds) openSchoolOverlay(cds, entityYear);
          });
          tags.appendChild(tag);
        });
        div.appendChild(tags);

        if (state.selectedDrill?.box === cell.box && state.selectedDrill?.rtype === div.dataset.rtype && state.selectedDrill?.typeKey === typeKey) {
          div.classList.add("selected-drill");
        }

        if (!cell.na && cell.box) {
          div.addEventListener("click", () => onCellClick(cell.box, div.dataset.rtype, typeKey));
        }

        td.appendChild(div);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);

    requestAnimationFrame(() => equalizeRowHeights(table));
    return wrap;
  }

  function equalizeRowHeights(table) {
    const rows = table.querySelectorAll("tbody tr");
    rows.forEach((tr) => {
      const tds = tr.querySelectorAll("td");
      if (!tds.length) return;
      tds.forEach((td) => {
        td.style.height = "";
        const cell = td.querySelector(".grid-cell");
        if (cell) {
          cell.style.minHeight = "";
          cell.style.height = "100%";
        }
      });
      let max = 90;
      tds.forEach((td) => {
        const cell = td.querySelector(".grid-cell");
        const h = cell ? cell.getBoundingClientRect().height : td.offsetHeight;
        if (h > max) max = h;
      });
      max = Math.ceil(max);
      tds.forEach((td) => {
        td.style.height = `${max}px`;
        td.style.verticalAlign = "top";
        const cell = td.querySelector(".grid-cell");
        if (cell) {
          cell.style.minHeight = `${max}px`;
          cell.style.height = "100%";
          cell.style.boxSizing = "border-box";
        }
      });
    });
    refreshTrajectoryOverlays();
  }

  function renderSchoolGrids(report) {
    els.schoolGridsContainer.innerHTML = "";
    const grids = report.school_grids || (report.school_grid ? [report.school_grid] : []);
    grids.forEach((grid) => {
      const panel = document.createElement("article");
      panel.className = "grid-panel";

      const header = document.createElement("header");
      header.className = "grid-panel-header";
      const headerMain = document.createElement("div");
      headerMain.className = "grid-panel-header-main";
      fillGridPanelHeader(headerMain, grid, report);
      const headerActions = document.createElement("div");
      headerActions.className = "grid-panel-header-actions";
      header.appendChild(headerMain);
      header.appendChild(headerActions);
      panel.appendChild(header);

      const exportTarget = document.createElement("div");
      exportTarget.className = "grid-panel-export-target";
      const exportHeading = document.createElement("div");
      exportHeading.className = "grid-export-heading";
      fillGridPanelHeader(exportHeading, grid, report);
      const exportSubtitle = document.createElement("p");
      exportSubtitle.className = "grid-export-subtitle";
      exportSubtitle.textContent = formatExportSubtitle(grid.title);
      exportTarget.appendChild(exportHeading);
      exportTarget.appendChild(exportSubtitle);
      exportTarget.appendChild(renderGridTable(grid, "school", report.view_mode, grid.type_key));
      const noColor = document.createElement("p");
      noColor.className = "no-color-msg hidden";
      if (grid.no_color_message) {
        noColor.textContent = grid.no_color_message;
        noColor.classList.remove("hidden");
      }
      exportTarget.appendChild(noColor);
      appendYearLegendToExportTarget(exportTarget);
      panel.appendChild(exportTarget);

      attachSaveJpgButton(headerActions, exportTarget, grid.title || grid.type_label);

      els.schoolGridsContainer.appendChild(panel);
    });
    if (multiYearMode()) {
      requestAnimationFrame(() => refreshTrajectoryOverlays());
    }
  }

  function renderDistrictGrid(report) {
    const grid = report.district_grid;
    updateDistrictHeader(report);
    const chartTitle = els.districtGridTitle?.textContent?.trim() || state.districtShort || "District";
    if (els.districtExportSubtitle) {
      els.districtExportSubtitle.textContent = formatExportSubtitle(chartTitle);
    }
    els.districtLogic.textContent = grid.logic_summary || "";
    els.districtGrid.innerHTML = "";
    const districtBlock = renderGridTable(grid, "district", report.view_mode, grid.type_key);
    els.districtGrid.appendChild(districtBlock);
    appendYearLegendToExportTarget(els.districtExportTarget);
    showNoColor(els.districtNoColor, grid.no_color_message);
    if (multiYearMode()) {
      requestAnimationFrame(() => refreshTrajectoryOverlays());
    }
  }

  function showNoColor(el, msg) {
    if (msg) {
      el.textContent = msg;
      el.classList.remove("hidden");
    } else el.classList.add("hidden");
  }

  function overlaySubgroupsForDisplay(detail, showAll) {
    const rows = detail?.subgroups || [];
    if (showAll) return rows;
    return rows.filter((sg) => sg.grid_eligible);
  }

  function renderSchoolOverlayBody() {
    const stash = state.overlayDetail;
    if (!stash || !els.overlayBody) return;
    const { detail: d, meta, schoolCds } = stash;
    const showAll = state.overlayShowAllSubgroups;
    const visibleSubgroups = overlaySubgroupsForDisplay(d, showAll);
    const totalSubgroups = (d.subgroups || []).length;
    const withResults = (d.subgroups || []).filter((sg) => sg.grid_eligible).length;

    const sgRows = visibleSubgroups
      .map(
        (sg) => `<tr>
          <td>${sg.studentgroup_label || sg.code}</td>
          <td><span class="color-swatch" style="background:${sg.color_hex}"></span>${sg.color_name || "—"}</td>
          <td>${fmt(sg.currstatus)}</td><td>${fmt(sg.priorstatus)}</td><td>${fmt(sg.change)}</td>
          <td>${fmt(sg.statuslevel_label)}</td></tr>`
      )
      .join("");

    const sgSection =
      totalSubgroups > 0
        ? `<div class="overlay-sg-section">
          <div class="overlay-sg-toolbar">
            <h3 class="overlay-section-title overlay-section-title-inline">Student groups</h3>
            <label class="toggle-switch overlay-sg-toggle" title="Show groups without a 5×5 color placement">
              <input type="checkbox" id="overlay-sg-show-all"${showAll ? " checked" : ""}>
              <span class="toggle-label">Show all</span>
            </label>
          </div>
          <p class="overlay-sg-summary" id="overlay-sg-summary">
            ${showAll ? `Showing all ${totalSubgroups} groups` : `Showing ${visibleSubgroups.length} of ${totalSubgroups} with results (${withResults} on chart)`}
          </p>
          ${
            sgRows
              ? `<table class="subgroup-table"><thead><tr><th>Group</th><th>Color</th><th>Current</th><th>Prior</th><th>Change</th><th>Status</th></tr></thead><tbody>${sgRows}</tbody></table>`
              : `<p class="overlay-sg-empty">No student groups match this filter.</p>`
          }
        </div>`
        : "";

    const smallDenomFlag =
      d.smalldenom === "Y" ? `<div class="overlay-flags overlay-flags-minimal"><span class="flag warn">Small denominator</span></div>` : "";

    els.overlayBody.innerHTML = `
      <div class="overlay-color-row">
        <span class="overlay-color-badge" style="border-color:${d.color_hex}">
          <span class="color-swatch" style="background:${d.color_hex}"></span>${d.color_name}
        </span>
      </div>
      <div class="overlay-metrics">
        <div><label>Current</label><strong>${fmt(d.currstatus)}</strong></div>
        <div><label>Prior</label><strong>${fmt(d.priorstatus)}</strong></div>
        <div><label>Change</label><strong>${fmt(d.change)}</strong></div>
        <div><label>Status</label><strong>${fmt(d.statuslevel_label)}</strong></div>
        <div><label>Change Level</label><strong>${fmt(d.changelevel_label)}</strong></div>
        <div><label>Denominator</label><strong>${fmt(d.currdenom)}</strong></div>
      </div>
      ${smallDenomFlag}
      ${sgSection}
      <div class="overlay-actions">
        <button type="button" class="btn-primary" id="overlay-view-grid">View subgroup chart</button>
      </div>`;

    document.getElementById("overlay-sg-show-all")?.addEventListener("change", (e) => {
      state.overlayShowAllSubgroups = Boolean(e.target.checked);
      renderSchoolOverlayBody();
    });

    document.getElementById("overlay-view-grid")?.addEventListener("click", () => {
      closeOverlay();
      selectSchool(schoolCds);
    });
  }

  async function openSchoolOverlay(cds, reportingYear) {
    const schoolCds = formatSchoolCds(cds);
    if (!schoolCds) return;
    const params = apiParams({ school_cds: schoolCds });
    if (reportingYear) params.reporting_year = reportingYear;
    let data;
    try {
      data = await fetchJson(`${API_BASE}/school-detail/?${qs(params)}`);
    } catch (err) {
      console.error("School detail failed", err);
      return;
    }
    const d = data.school_detail;
    if (!d) return;

    state.overlayShowAllSubgroups = false;
    state.overlayDetail = {
      detail: d,
      meta: data,
      schoolCds,
      reportingYear,
    };

    els.overlayTitle.textContent = d.name;
    els.overlaySubtitle.textContent = `${data.indicator_label} · ${data.reporting_year}`;

    renderSchoolOverlayBody();

    els.overlayBackdrop.classList.remove("hidden");
    els.schoolOverlay.classList.remove("hidden");
  }

  function closeOverlay() {
    els.overlayBackdrop.classList.add("hidden");
    els.schoolOverlay.classList.add("hidden");
    state.overlayDetail = null;
  }

  let yearApplyInFlight = false;
  let yearApplyPending = false;
  let reportLoadGen = 0;

  function syncSelectedYearsFromDom() {
    if (!els.yearCheckboxes) return;
    const checked = Array.from(els.yearCheckboxes.querySelectorAll('input[type="checkbox"]:checked'))
      .map((i) => parseInt(i.value, 10))
      .filter((y) => !Number.isNaN(y));
    if (checked.length) {
      state.selectedYears = normalizeYearList(checked);
    }
  }

  async function applyYearSelectionFromCheckboxes() {
    if (!els.yearCheckboxes) return;
    if (yearApplyInFlight) {
      yearApplyPending = true;
      return;
    }
    const inputs = els.yearCheckboxes.querySelectorAll('input[type="checkbox"]');
    let selected = normalizeYearList(
      Array.from(inputs)
        .filter((i) => i.checked)
        .map((i) => i.value)
    );
    if (!selected.length) {
      renderYearToggles();
      return;
    }
    state.selectedYears = selected;
    saveStoredSelectedYears(selected);
    state.reportingYear = selected[selected.length - 1];
    updateYearBadge();
    syncYearLegendToolbar();
    yearApplyInFlight = true;
    try {
      closeDrill();
      await loadSchools();
      if (state.schoolCds) await loadSubgroups();
      await loadReport();
    } finally {
      yearApplyInFlight = false;
      if (yearApplyPending) {
        yearApplyPending = false;
        await applyYearSelectionFromCheckboxes();
      }
    }
  }

  function toggleSubgroupPanel(open) {
    const panel = els.subgroupDropdownPanel;
    if (!panel) return;
    if (open === undefined) {
      panel.classList.toggle("is-hidden");
    } else {
      panel.classList.toggle("is-hidden", !open);
    }
    const expanded = !panel.classList.contains("is-hidden");
    els.subgroupDropdownBtn?.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function toggleYearPanel(open) {
    const panel = els.yearCheckboxPanel;
    if (!panel) return;
    if (open === undefined) {
      panel.classList.toggle("is-hidden");
    } else {
      panel.classList.toggle("is-hidden", !open);
    }
    const expanded = !panel.classList.contains("is-hidden");
    els.yearDropdownBtn?.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function renderYearToggles() {
    const box = els.yearCheckboxes;
    if (!box) return;
    if (!state.availableYears.length) {
      els.yearSection?.classList.add("disabled");
      box.innerHTML = "";
      updateYearBadge();
      return;
    }
    els.yearSection?.classList.remove("disabled");
    const prior = new Set(normalizeYearList(state.selectedYears));
    box.innerHTML = "";
    state.availableYears.forEach((year) => {
      const y = normalizeYear(year);
      if (y === null) return;
      const label = document.createElement("label");
      label.className = "year-check-item";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = String(y);
      input.checked = prior.has(y);
      const colorSwatch = document.createElement("span");
      colorSwatch.className = "year-color-swatch";
      colorSwatch.style.backgroundColor = yearHex(y);
      colorSwatch.title = `Color for ${yearLabel(y)}`;
      const picker = document.createElement("input");
      picker.type = "color";
      picker.className = "year-color-picker";
      picker.value = yearHex(y);
      picker.title = `Customize color for ${yearLabel(y)}`;
      picker.addEventListener("click", (e) => e.stopPropagation());
      picker.addEventListener("input", (e) => {
        colorSwatch.style.backgroundColor = e.target.value;
        setYearColor(y, e.target.value, false);
      });
      picker.addEventListener("change", (e) => {
        setYearColor(y, e.target.value, true);
      });
      const text = document.createElement("span");
      text.textContent = yearLabel(y);
      input.addEventListener("change", async () => {
        const checked = box.querySelectorAll('input[type="checkbox"]:checked');
        if (!checked.length) {
          input.checked = true;
          return;
        }
        await applyYearSelectionFromCheckboxes();
      });
      label.appendChild(input);
      label.appendChild(colorSwatch);
      label.appendChild(picker);
      label.appendChild(text);
      box.appendChild(label);
    });
    if (!state.selectedYears.length && state.availableYears.length) {
      const latest = state.availableYears[state.availableYears.length - 1];
      const latestInput = box.querySelector(`input[value="${latest}"]`);
      if (latestInput) latestInput.checked = true;
      state.selectedYears = [latest];
    }
    updateYearBadge();
    syncYearLegendToolbar();
  }

  function updateYearBadge() {
    if (els.yearDropdownSummary) {
      if (!state.selectedYears.length) {
        els.yearDropdownSummary.textContent = "Select years";
      } else if (state.selectedYears.length === 1) {
        els.yearDropdownSummary.textContent = yearLabel(state.selectedYears[0]);
      } else {
        els.yearDropdownSummary.textContent = state.selectedYears.map((y) => yearLabel(y)).join(", ");
      }
    }
    if (!state.selectedYears.length) {
      els.reportingYearBadge.textContent = "";
      return;
    }
    if (state.selectedYears.length === 1) {
      els.reportingYearBadge.textContent = yearLabel(state.selectedYears[0]);
    } else {
      els.reportingYearBadge.textContent = `${state.selectedYears.length} years selected`;
    }
  }

  function syncYearLegendToolbar() {
    if (!els.yearLegendToolbar) return;
    els.yearLegendToolbar.innerHTML = "";
    if (!multiYearMode()) {
      els.yearLegendToolbar.classList.add("hidden");
      return;
    }
    els.yearLegendToolbar.classList.remove("hidden");
    const legend = buildYearLegendElement();
    while (legend.firstChild) {
      els.yearLegendToolbar.appendChild(legend.firstChild);
    }
  }

  function renderYearLegend() {
    if (!multiYearMode()) return "";
    return `<div class="year-legend">${displayReportingYears()
      .map(
        (y) =>
          `<span class="year-legend-item"><span class="year-swatch" style="background:${yearHex(y)}"></span>${yearLabel(y)}</span>`
      )
      .join("")}</div>`;
  }

  function renderSidebarSchools() {
    const prev = els.schoolSelect.value;
    els.schoolSelect.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = "← All Schools (district map)";
    els.schoolSelect.appendChild(allOpt);

    state.schools.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.cds;
      opt.textContent = s.name;
      if (state.schoolCds === s.cds) opt.selected = true;
      els.schoolSelect.appendChild(opt);
    });

    els.schoolSelect.onchange = () => selectSchool(els.schoolSelect.value);
  }

  function subgroupLabel(code) {
    return state.subgroups.find((s) => s.code === code)?.label || code;
  }

  function syncSelectedSubgroups() {
    const codes = state.subgroups.map((s) => s.code);
    state.selectedSubgroups = state.selectedSubgroups.filter((c) => codes.includes(c));
    if (!state.selectedSubgroups.length && codes.length) {
      state.selectedSubgroups = [...codes];
    }
  }

  function updateSubgroupCount() {
    if (!els.subgroupCount) return;
    const total = state.subgroups.length;
    const selected = state.selectedSubgroups.length;
    let summary = "Student groups";
    if (!total) {
      els.subgroupCount.textContent = "No groups available";
      summary = "No groups";
    } else if (!selected) {
      els.subgroupCount.textContent = "None selected";
      summary = "None selected";
    } else if (selected === 1) {
      const label = subgroupLabel(state.selectedSubgroups[0]);
      els.subgroupCount.textContent = label;
      summary = label;
    } else if (selected === total) {
      els.subgroupCount.textContent = `All ${total} selected`;
      summary = `All ${total} groups`;
    } else {
      els.subgroupCount.textContent = `${selected} of ${total} selected`;
      summary = `${selected} of ${total} groups`;
    }
    if (els.subgroupDropdownSummary) {
      els.subgroupDropdownSummary.textContent = summary;
    }
  }

  async function applySubgroupSelection() {
    closeDrill();
    await loadReport();
    renderSidebarSubgroups();
  }

  function renderSidebarSubgroups() {
    els.subgroupSelect.innerHTML = "";
    if (els.subgroupChips) els.subgroupChips.innerHTML = "";

    if (state.schoolCds) {
      els.subgroupSelect.classList.add("hidden");
      els.subgroupMulti?.classList.remove("hidden");
      els.subgroupSection.classList.remove("disabled");
      syncSelectedSubgroups();

      if (!state.subgroups.length) {
        els.subgroupSection.classList.add("disabled");
        updateSubgroupCount();
        return;
      }

      state.subgroups.forEach((sg) => {
        const selected = state.selectedSubgroups.includes(sg.code);
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "multi-filter-chip" + (selected ? " is-selected" : "");
        chip.textContent = sg.label;
        chip.title = sg.label;
        chip.setAttribute("aria-pressed", String(selected));
        chip.addEventListener("click", async () => {
          if (state.selectedSubgroups.includes(sg.code)) {
            state.selectedSubgroups = state.selectedSubgroups.filter((c) => c !== sg.code);
          } else {
            state.selectedSubgroups.push(sg.code);
          }
          await applySubgroupSelection();
        });
        els.subgroupChips.appendChild(chip);
      });

      updateSubgroupCount();
      return;
    }

    els.subgroupSelect.classList.remove("hidden");
    els.subgroupMulti?.classList.add("hidden");
    els.subgroupSection.classList.remove("disabled");
    state.subgroups.forEach((sg) => {
      const opt = document.createElement("option");
      opt.value = sg.code;
      opt.textContent = sg.label;
      if (state.subgroup === sg.code) opt.selected = true;
      els.subgroupSelect.appendChild(opt);
    });
    els.subgroupSelect.onchange = () => {
      state.subgroup = els.subgroupSelect.value;
      loadReport();
    };
  }

  async function loadConfig() {
    const cfg = await fetchJson(`${API_BASE}/config/`);
    state.districtCds = cfg.district_cds;
    state.districtShort = cfg.district_short;
    state.yearColors = cfg.year_colors || DEFAULT_YEAR_COLORS;
    state.official2026 = cfg.official_2026 || {};
    state.availableYears = normalizeYearList(
      cfg.available_reporting_years?.[state.indicator] || []
    );
    const latest = normalizeYear(cfg.reporting_years?.[state.indicator]);
    const availableSet = new Set(state.availableYears);
    const stored = loadStoredSelectedYears(state.indicator);
    const current = normalizeYearList(
      state.selectedYears.length ? state.selectedYears : stored
    );
    if (!current.length || !current.every((y) => availableSet.has(y))) {
      state.selectedYears = latest ? [latest] : state.availableYears.slice(-1);
    } else {
      state.selectedYears = current;
    }
    saveStoredSelectedYears(state.selectedYears);
    state.reportingYear = state.selectedYears[state.selectedYears.length - 1] || latest;
    renderYearToggles();
    updateYearBadge();
    syncYearLegendToolbar();
  }

  async function loadIndicators() {
    const items = await fetchJson(`${API_BASE}/indicators/`);
    els.indicator.innerHTML = items.map((i) => `<option value="${i.key}">${i.label}</option>`).join("");
    els.indicator.value = state.indicator;
  }

  async function loadSchools() {
    state.schools = await fetchJson(`${API_BASE}/schools/?${qs(apiParams())}`);
    renderSidebarSchools();
  }

  async function loadSubgroups() {
    const params = apiParams();
    if (state.schoolCds) params.school_cds = state.schoolCds;
    state.subgroups = await fetchJson(`${API_BASE}/subgroups/?${qs(params)}`);
    if (state.schoolCds) {
      syncSelectedSubgroups();
    } else if (!state.subgroups.some((s) => s.code === state.subgroup)) {
      state.subgroup = state.subgroups.find((s) => s.code === "ALL")?.code || state.subgroups[0]?.code || "ALL";
    }
    renderSidebarSubgroups();
  }

  async function loadReport() {
    const gen = ++reportLoadGen;
    syncSelectedYearsFromDom();
    let requestedYears = normalizeYearList(state.selectedYears);
    if (!requestedYears.length) {
      requestedYears = loadStoredSelectedYears(state.indicator);
      if (requestedYears.length) state.selectedYears = requestedYears;
    }

    const params = { indicator: state.indicator };
    if (requestedYears.length) {
      params.reporting_years = requestedYears.join(",");
      state.selectedYears = requestedYears;
    } else {
      const fallback = reportingYearsParam();
      if (fallback) params.reporting_years = fallback;
    }
    if (state.schoolCds) {
      params.school_cds = state.schoolCds;
      // Match cdedapp: always pass studentgroups; empty means \"no filter\" on the API.
      params.studentgroups = state.selectedSubgroups.join(",");
    } else {
      params.studentgroup = state.subgroup;
    }
    if (els.districtSubgroupToggle?.checked) params.district_subgroups = "true";

    state.report = await fetchJson(`${API_BASE}/report/?${qs(params)}`);
    if (gen !== reportLoadGen) return;

    const apiYears = normalizeYearList(state.report.reporting_years);
    if (requestedYears.length) {
      state.selectedYears = requestedYears;
      saveStoredSelectedYears(requestedYears);
      state.report.reporting_years = requestedYears;
    } else if (apiYears.length) {
      state.selectedYears = apiYears;
      saveStoredSelectedYears(apiYears);
      state.report.reporting_years = apiYears;
    }

    if (requestedYears.length > 1) {
      const gridYears = yearsPresentInReport(state.report);
      if (gridYears.length < requestedYears.length) {
        console.warn(
          "CA Dashboard: chart is missing some selected years.",
          { requested: requestedYears, inChart: gridYears }
        );
      }
    }

    state.reportingYear = state.selectedYears[state.selectedYears.length - 1] ?? normalizeYear(state.report.reporting_year);
    renderYearToggles();
    updateYearBadge();
    syncYearLegendToolbar();

    updatePageHeader(state.report);
    renderSchoolGrids(state.report);
    renderDistrictGrid(state.report);
  }

  async function onCellClick(box, rtype, typeKey) {
    if (state.selectedDrill?.box === box && state.selectedDrill?.rtype === rtype && state.selectedDrill?.typeKey === typeKey) {
      closeDrill();
      return;
    }
    state.selectedDrill = { box, rtype, typeKey };
    const params = apiParams({ box, rtype, type_key: typeKey });
    if (state.schoolCds) {
      params.school_cds = state.schoolCds;
      // Match cdedapp: always pass studentgroups; empty means \"no filter\" on the API.
      params.studentgroups = state.selectedSubgroups.join(",");
    } else {
      params.studentgroup = state.subgroup;
    }

    const data = await fetchJson(`${API_BASE}/box-entities/?${qs(params)}`);
    const grid = gridForDrill(rtype, typeKey);
    els.drillPanel.classList.remove("hidden");
    els.drillTitle.textContent = state.report.view_mode === "school_subgroups" ? "Groups in cell" : "Schools in cell";
    const cellContext = formatDrillCellContext(grid, box);
    const chartNote =
      grid?.type_label && (state.report.school_grids?.length > 1) ? grid.type_label : "";
    els.drillContext.textContent = [cellContext, chartNote].filter(Boolean).join(" · ") || "Cell details";

    els.entityList.innerHTML = "";
    data.entities.forEach((entity) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="entity-name">${entity.name}</span>
        <span class="entity-stats"><span class="color-swatch" style="background:${entity.color_hex}"></span>
        ${fmt(entity.currstatus)} / Δ ${fmt(entity.change)}</span>`;
      if (multiYearMode() && entity.reportingyear) {
        li.classList.add("year-tagged-entity");
        applyYearTagStyle(li, entity.reportingyear);
        const textColor = textOnYearColor(yearHex(entity.reportingyear));
        li.querySelector(".entity-stats")?.style.setProperty("color", textColor);
        li.querySelector(".entity-stats")?.style.setProperty("opacity", "0.92");
      }
      li.addEventListener("click", () => {
        if (entity.rtype === "D") {
          closeDrill();
          return;
        }
        const cds = formatSchoolCds(entity.cds);
        if (cds && state.report.view_mode !== "school_subgroups") {
          openSchoolOverlay(cds, entity.reportingyear);
        } else {
          closeDrill();
        }
      });
      els.entityList.appendChild(li);
    });
    renderSchoolGrids(state.report);
    renderDistrictGrid(state.report);
  }

  function closeDrill() {
    state.selectedDrill = null;
    els.drillPanel.classList.add("hidden");
    if (state.report) {
      renderSchoolGrids(state.report);
      renderDistrictGrid(state.report);
    }
  }

  async function selectSchool(cds) {
    state.schoolCds = cds || "";
    state.selectedSubgroups = [];
    renderSidebarSchools();
    await loadSubgroups();
    closeDrill();
    closeOverlay();
    await loadReport();
  }

  function toggleDistrict() {
    state.districtExpanded = !state.districtExpanded;
    els.districtGridPanel.classList.toggle("collapsed", !state.districtExpanded);
    els.districtGridPanel.classList.toggle("expanded-full", state.districtExpanded);
    els.districtToggle.setAttribute("aria-expanded", String(state.districtExpanded));
  }

  els.indicator.addEventListener("change", async () => {
    state.indicator = els.indicator.value;
    state.selectedYears = [];
    state.selectedSubgroups = [];
    closeDrill();
    closeOverlay();
    await loadConfig();
    await loadSchools();
    await loadSubgroups();
    if (state.schoolCds && !state.schools.some((s) => s.cds === state.schoolCds)) {
      state.schoolCds = "";
    }
    await loadReport();
  });

  els.overlayClose.addEventListener("click", closeOverlay);
  els.overlayBackdrop.addEventListener("click", closeOverlay);
  els.drillClose.addEventListener("click", closeDrill);
  els.districtToggle.addEventListener("click", toggleDistrict);
  els.districtSubgroupToggle?.addEventListener("change", () => loadReport());

  els.yearDropdownBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleSubgroupPanel(false);
    toggleYearPanel();
  });

  els.subgroupDropdownBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleYearPanel(false);
    toggleSubgroupPanel();
  });

  document.addEventListener("click", (e) => {
    if (els.yearCheckboxPanel && !els.yearCheckboxPanel.classList.contains("is-hidden")) {
      if (!e.target.closest("#year-dropdown")) toggleYearPanel(false);
    }
    if (els.subgroupDropdownPanel && !els.subgroupDropdownPanel.classList.contains("is-hidden")) {
      if (!e.target.closest("#subgroup-multi")) toggleSubgroupPanel(false);
    }
  });

  els.subgroupSelectAll?.addEventListener("click", async () => {
    if (!state.schoolCds || !state.subgroups.length) return;
    state.selectedSubgroups = state.subgroups.map((s) => s.code);
    await applySubgroupSelection();
  });

  els.subgroupSelectNone?.addEventListener("click", async () => {
    if (!state.schoolCds || !state.subgroups.length) return;
    const allStudents = state.subgroups.find((s) => s.code === "ALL");
    state.selectedSubgroups = allStudents ? [allStudents.code] : [];
    await applySubgroupSelection();
  });

  els.sidebarToggle?.addEventListener("click", () => {
    const collapsed = els.appLayout.classList.toggle("sidebar-collapsed");
    localStorage.setItem("sidebarCollapsed", String(collapsed));
    setTimeout(() => {
      document.querySelectorAll(".five-by-five").forEach(equalizeRowHeights);
    }, 360);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeOverlay();
      closeDrill();
    }
  });

  const BADGE_SIZE = 14;
  const BADGE_GAP = 3;
  const SAME_CELL_RING_R = 14;

  function getCellCenter(box, wrap, fanIndex, fanTotal) {
    const cell = wrap.querySelector(`.grid-cell[data-box="${box}"]`);
    if (!cell) return null;
    const wrapRect = wrap.getBoundingClientRect();
    const cellRect = cell.getBoundingClientRect();
    let x = cellRect.left + cellRect.width / 2 - wrapRect.left;
    let y = cellRect.top + cellRect.height / 2 - wrapRect.top;

    if (fanTotal > 1) {
      const spread = Math.min(22, 12 * fanTotal);
      const step = spread / (fanTotal - 1);
      const offset = (fanIndex - (fanTotal - 1) / 2) * step;
      const angle = 0.7; // radians, slant the fan
      x += Math.cos(angle) * offset;
      y += Math.sin(angle) * offset;
    }
    return { x, y, cell };
  }

  function clearMilestoneBadges(wrap) {
    wrap.querySelectorAll(".trajectory-badge").forEach((b) => b.remove());
  }

  function addStackedBadges(cell, years) {
    const unique = [...new Set(years)].sort((a, b) => a - b);
    unique.forEach((year, i) => {
      const badge = document.createElement("span");
      badge.className = "trajectory-badge";
      badge.style.background = yearHex(year);
      badge.style.top = `${BADGE_GAP + i * (BADGE_SIZE + BADGE_GAP)}px`;
      badge.textContent = String(year).slice(-2);
      badge.title = String(year);
      cell.appendChild(badge);
    });
  }

  function getSubgroupTrajectory(grid, code) {
    const milestones = [];
    const years = selectedReportingYears().length
      ? selectedReportingYears()
      : displayReportingYears();
    years.forEach((year) => {
      for (const row of grid.cells) {
        for (const cell of row) {
          const entity = (cell.schools || []).find(
            (e) =>
              (e.code === code || e.name === subgroupLabel(code)) &&
              Number(e.reportingyear) === Number(year)
          );
          if (entity) {
            milestones.push({ year, box: cell.box, code });
            break;
          }
        }
      }
    });
    return milestones;
  }

  function buildTrajectorySegment(a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const dist = Math.hypot(dx, dy) || 1;

    if (dist < 8) {
      // same cell: draw a small ring to indicate "stayed here"
      const rx = SAME_CELL_RING_R * 0.6;
      const ry = SAME_CELL_RING_R * 0.35;
      const ringX = a.x - rx;
      const ringY = a.y - SAME_CELL_RING_R;
      const d = (
        `M ${a.x} ${a.y - SAME_CELL_RING_R + 1}` +
        ` C ${ringX - 6} ${ringY}, ${ringX - 2} ${ringY + SAME_CELL_RING_R * 1.6}` +
        `   ${a.x + 2} ${ringY + SAME_CELL_RING_R * 1.8}` +
        ` C ${ringX + rx + 6} ${ringY + SAME_CELL_RING_R * 1.6}` +
        `   ${ringX + rx + 2} ${ringY}, ${a.x} ${a.y - SAME_CELL_RING_R + 1}`
      );
      const midAngle = Math.PI * 0.7;
      const tipR = SAME_CELL_RING_R + 4;
      const tipX = a.x + Math.cos(midAngle) * tipR * 0.5;
      const tipY = a.y - SAME_CELL_RING_R + tipR * 0.7;
      return {
        d,
        tipX, tipY,
        tangentX: -Math.sin(midAngle), tangentY: Math.cos(midAngle),
        sameCell: true,
        cellCenter: { x: a.x, y: a.y },
      };
    }

    const perpX = -dy / dist;
    const perpY = dx / dist;
    const bulge = Math.min(55, dist * 0.45);
    const cx = (a.x + b.x) / 2 + perpX * bulge;
    const cy = (a.y + b.y) / 2 + perpY * bulge;
    const tangentX = b.x - cx;
    const tangentY = b.y - cy;
    const tLen = Math.hypot(tangentX, tangentY) || 1;
    const ux = tangentX / tLen;
    const uy = tangentY / tLen;
    const inset = 12;
    return {
      d: `M ${a.x} ${a.y} Q ${cx} ${cy} ${b.x - ux * inset} ${b.y - uy * inset}`,
      tipX: b.x, tipY: b.y,
      tangentX, tangentY,
      sameCell: false,
    };
  }

  function appendArrowhead(svg, tipX, tipY, tangentX, tangentY, size, color) {
    const len = Math.hypot(tangentX, tangentY);
    if (len < 0.3) return;
    const ux = tangentX / len;
    const uy = tangentY / len;
    const px = -uy;
    const py = ux;
    const baseX = tipX - ux * size;
    const baseY = tipY - uy * size;
    const wing = size * 0.5;

    const halo = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    halo.setAttribute("points", `${tipX},${tipY} ${baseX + px * (wing + 1.8)},${baseY + py * (wing + 1.8)} ${baseX - px * (wing + 1.8)},${baseY - py * (wing + 1.8)}`);
    halo.setAttribute("fill", "#ffffff");
    halo.setAttribute("class", "trajectory-arrowhead-halo");
    svg.appendChild(halo);

    const head = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    head.setAttribute("points", `${tipX},${tipY} ${baseX + px * wing},${baseY + py * wing} ${baseX - px * wing},${baseY - py * wing}`);
    head.setAttribute("fill", color);
    head.setAttribute("class", "trajectory-arrowhead");
    svg.appendChild(head);
  }

  function drawTrajectoriesForGrid(grid, wrap) {
    const svg = wrap.querySelector(".grid-trajectory-overlay");
    if (!svg) return;
    svg.innerHTML = "";
    clearMilestoneBadges(wrap);
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (w < 1 || h < 1) return;
    svg.setAttribute("width", w);
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);

    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    svg.appendChild(defs);

    const activeSubgroups = state.selectedSubgroups.length
      ? state.selectedSubgroups
      : state.subgroups.map((s) => s.code);

    // --- first pass: collect all centers, fan counts, and badge data ---

    // centersBySubgroup[code] = [{ x, y, cell, box, year }, ...]
    const centersBySubgroup = {};
    // fanCountByKey["box:year"] = number of subgroups arriving
    const fanCountByKey = {};
    // badgesByCell[box] = Set<year>
    const badgesByCell = {};

    activeSubgroups.forEach((code) => {
      const milestones = getSubgroupTrajectory(grid, code);
      if (milestones.length < 2) return;
      const firstPass = milestones.map((m) => {
        const rough = getCellCenter(m.box, wrap, 0, 1);
        return rough ? { x: rough.x, y: rough.y, cell: rough.cell, box: m.box, year: m.year } : null;
      }).filter(Boolean);
      if (firstPass.length < 2) return;

      // count arrivals (skip source year)
      for (let i = 1; i < firstPass.length; i++) {
        const key = `${firstPass[i].box}:${firstPass[i].year}`;
        fanCountByKey[key] = (fanCountByKey[key] || 0) + 1;
      }
      // collect badges (all years)
      firstPass.forEach((c) => {
        if (!badgesByCell[c.box]) badgesByCell[c.box] = new Set();
        badgesByCell[c.box].add(c.year);
      });

      centersBySubgroup[code] = firstPass;
    });

    // --- second pass: recompute centers with fanning ---

    // fanIdxByKey["box:year:code"] = assigned index
    const fanIdxByKey = {};
    activeSubgroups.forEach((code) => {
      const centers = centersBySubgroup[code];
      if (!centers || centers.length < 2) return;
      const fanned = centers.map((c) => {
        const key = `${c.box}:${c.year}`;
        const total = fanCountByKey[key] || 1;
        if (!(key in fanIdxByKey)) fanIdxByKey[key] = {};
        if (!(code in fanIdxByKey[key])) fanIdxByKey[key][code] = Object.keys(fanIdxByKey[key]).length;
        const idx = fanIdxByKey[key][code];
        return getCellCenter(c.box, wrap, idx, total);
      }).filter(Boolean);
      if (fanned.length < 2) return;

      // --- draw segments ---
      for (let i = 0; i < fanned.length - 1; i++) {
        const a = fanned[i];
        const b = fanned[i + 1];
        if (!a || !b) continue;

        const segment = buildTrajectorySegment(a, b);
        const fromColor = yearHex(centers[i].year);
        const toColor = yearHex(centers[i + 1].year);
        const gradId = `traj-grad-${code}-${i}`;

        const grad = document.createElementNS("http://www.w3.org/2000/svg", "linearGradient");
        grad.setAttribute("id", gradId);
        grad.setAttribute("gradientUnits", "userSpaceOnUse");
        grad.setAttribute("x1", String(a.x));
        grad.setAttribute("y1", String(a.y));
        grad.setAttribute("x2", String(b.x));
        grad.setAttribute("y2", String(b.y));
        const stop1 = document.createElementNS("http://www.w3.org/2000/svg", "stop");
        stop1.setAttribute("offset", "0%");
        stop1.setAttribute("stop-color", fromColor);
        const stop2 = document.createElementNS("http://www.w3.org/2000/svg", "stop");
        stop2.setAttribute("offset", "100%");
        stop2.setAttribute("stop-color", toColor);
        grad.appendChild(stop1);
        grad.appendChild(stop2);
        defs.appendChild(grad);

        const halo = document.createElementNS("http://www.w3.org/2000/svg", "path");
        halo.setAttribute("d", segment.d);
        halo.setAttribute("fill", "none");
        halo.setAttribute("stroke", "#ffffff");
        halo.setAttribute("stroke-width", "6");
        halo.setAttribute("stroke-linecap", "round");
        halo.setAttribute("stroke-linejoin", "round");
        halo.classList.add("trajectory-arrow-halo");
        svg.appendChild(halo);

        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", segment.d);
        path.setAttribute("fill", "none");
        path.setAttribute("stroke", `url(#${gradId})`);
        path.setAttribute("stroke-width", "3.2");
        path.setAttribute("opacity", "0.88");
        path.setAttribute("stroke-linecap", "round");
        path.setAttribute("stroke-linejoin", "round");
        path.classList.add("trajectory-arrow-segment");

        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        const label = state.subgroups.find((s) => s.code === code)?.label || code;
        title.textContent = `${label}: ${centers[i].year} → ${centers[i + 1].year}`;
        path.appendChild(title);
        svg.appendChild(path);

        const arrowSize = segment.sameCell ? 8 : 10;
        appendArrowhead(svg, segment.tipX, segment.tipY, segment.tangentX, segment.tangentY, arrowSize, toColor);
      }
    });

    // --- third pass: stacked year badges per cell ---
    Object.entries(badgesByCell).forEach(([box, yearsSet]) => {
      const cell = wrap.querySelector(`.grid-cell[data-box="${box}"]`);
      if (!cell) return;
      addStackedBadges(cell, [...yearsSet]);
    });
  }

  function refreshTrajectoryOverlays() {
    document.querySelectorAll(".grid-trajectory-overlay").forEach((svg) => {
      svg.innerHTML = "";
    });
    document.querySelectorAll(".trajectory-badge").forEach((b) => b.remove());

    if (!multiYearMode()) return;
    const schoolPanels = els.schoolGridsContainer.querySelectorAll(".grid-panel");
    const schoolSubgroupView =
      Boolean(state.schoolCds) && state.report?.view_mode === "school_subgroups";

    if (schoolSubgroupView) {
      const grids = state.report?.school_grids || [];
      const schoolPanels = els.schoolGridsContainer.querySelectorAll(".grid-panel");
      schoolPanels.forEach((panel, idx) => {
        const grid = grids[idx];
        const wrap = panel.querySelector(".grid-block");
        if (grid && wrap) drawTrajectoriesForGrid(grid, wrap);
      });
      return;
    }

    if (state.report?.district_grid && !state.schoolCds) {
      if (districtWrap) drawTrajectoriesForGrid(state.report.district_grid, districtWrap);
    }
  }

  window.addEventListener("resize", () => {
    refreshTrajectoryOverlays();
  });

  els.districtSaveJpg?.addEventListener("click", () => {
    const chartTitle = els.districtGridTitle?.textContent?.trim() || "District";
    saveGridAsJpg(els.districtExportTarget, chartTitle, els.districtSaveJpg);
  });

  async function init() {
    state.customYearColors = loadStoredYearColors();
    if (localStorage.getItem("sidebarCollapsed") === "true") {
      els.appLayout?.classList.add("sidebar-collapsed");
    }
    try {
      await loadConfig();
      await loadIndicators();
      await loadSchools();
      await loadSubgroups();
      await loadReport();
    } catch (err) {
      document.querySelector(".main-content").innerHTML =
        `<p class="error-msg">Error: ${err.message}</p>`;
    }
  }

  init();
})();
