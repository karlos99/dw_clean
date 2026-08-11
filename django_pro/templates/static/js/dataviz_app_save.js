        // ──────────────────────────────────────────────────────────────────
        // ── SAVE CHART MODAL ───────────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────

        const saveChartBtn      = document.getElementById('dv-save-chart-btn');
        const saveModalEl        = document.getElementById('dv-save-modal');
        const chartNameEl        = document.getElementById('dv-chart-name');
        const chartDescEl        = document.getElementById('dv-chart-desc');
        const saveErrorEl        = document.getElementById('dv-save-error');
        const saveOverwriteWarnEl= document.getElementById('dv-save-overwrite-warn');
        const saveOverwriteNameEl= document.getElementById('dv-save-overwrite-name');
        const saveCancelBtn      = document.getElementById('dv-save-cancel');
        const saveConfirmBtn     = document.getElementById('dv-save-confirm');

        let _lastSavedChartId = null;
        let _pendingOverwriteId = null;  // set when a name conflict needs confirmation

        function openSaveModal() {
            if (!saveModalEl) return;
            if (saveErrorEl) { saveErrorEl.textContent = ''; saveErrorEl.classList.add('hidden'); }
            if (saveOverwriteWarnEl) saveOverwriteWarnEl.classList.add('hidden');
            _pendingOverwriteId = null;
            if (saveConfirmBtn) saveConfirmBtn.textContent = 'Save';
            // Pre-fill name/desc from last saved chart if we are in update mode
            if (_lastSavedChartId && chartNameEl && !chartNameEl.value) {
                const last = _savedCharts.find((c) => c.id === _lastSavedChartId);
                if (last) {
                    chartNameEl.value = last.name || '';
                    if (chartDescEl) chartDescEl.value = last.description || '';
                }
            }
            saveModalEl.classList.add('open');
        }
        function closeSaveModal() {
            if (saveModalEl) saveModalEl.classList.remove('open');
            _pendingOverwriteId = null;
            if (saveConfirmBtn) saveConfirmBtn.textContent = 'Save';
        }

        // ──────────────────────────────────────────────────────────────────
        // ── MY CHARTS MANAGEMENT ──────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────

        const myChartsBtnEl        = document.getElementById('dv-my-charts-btn');
        const chartsManageModalEl  = document.getElementById('dv-charts-manage-modal');
        const chartsManageCloseEl  = document.getElementById('dv-charts-manage-close');
        const chartsManageDoneEl   = document.getElementById('dv-charts-manage-done');
        const chartsManageListEl   = document.getElementById('dv-charts-manage-list');

        function openChartManage() {
            if (!chartsManageModalEl) return;
            chartsManageModalEl.classList.add('open');
            refreshChartManageList();
        }

        function closeChartManage() {
            if (chartsManageModalEl) chartsManageModalEl.classList.remove('open');
        }

        function refreshChartManageList() {
            if (!chartsManageListEl || !chartsUrl) return;
            chartsManageListEl.innerHTML = '<p class="text-xs text-slate-400 italic">Loading…</p>';
            fetch(chartsUrl, { credentials: 'same-origin' })
                .then((r) => r.json())
                .then((data) => {
                    _savedCharts = data.charts || [];
                    renderChartManageList();
                    refreshAddToDashSelect();
                })
                .catch(() => {
                    chartsManageListEl.innerHTML = '<p class="text-xs text-red-500">Failed to load charts.</p>';
                });
        }

        function renderChartManageList() {
            if (!chartsManageListEl) return;
            if (!_savedCharts.length) {
                chartsManageListEl.innerHTML = '<p class="text-xs text-slate-400 italic">No saved charts yet. Use the Save Chart button in Explore mode.</p>';
                return;
            }
            chartsManageListEl.innerHTML = '';
            _savedCharts.forEach((chart) => {
                const row = document.createElement('div');
                row.className = 'flex items-center gap-2 px-3 py-2 rounded-lg border border-slate-100 hover:bg-slate-50';
                row.dataset.chartId = chart.id;

                const typeColors = {
                    bar:'bg-blue-100 text-blue-700', line:'bg-cyan-100 text-cyan-700',
                    pie:'bg-purple-100 text-purple-700', area:'bg-indigo-100 text-indigo-700',
                    scatter:'bg-orange-100 text-orange-700',
                };
                const typeBadge = typeColors[chart.chart_type] || 'bg-slate-100 text-slate-600';
                const dateStr   = chart.created_at ? chart.created_at.split('T')[0] : '';

                row.innerHTML = `
                    <div class="flex-1 min-w-0">
                        <div class="cm-chart-name text-sm font-semibold text-slate-800 truncate">${escapeHtml(chart.name)}</div>
                        <div class="cm-chart-rename hidden mt-1">
                            <input type="text" class="cm-rename-input w-full px-2 py-1 text-xs border border-blue-400 rounded-lg" value="${escapeHtml(chart.name)}">
                        </div>
                        <div class="text-[10px] text-slate-400 mt-0.5">${dateStr}</div>
                    </div>
                    <span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${typeBadge}">${escapeHtml(chart.chart_type || '—')}</span>
                    <div class="flex gap-1 shrink-0">
                        <button type="button" class="cm-load px-2 py-1 text-[11px] font-semibold text-white bg-blue-600 rounded hover:bg-blue-500" title="Load into Explore">&#9654; Load</button>
                        <button type="button" class="cm-rename px-2 py-1 text-[11px] font-semibold border border-slate-300 rounded hover:bg-slate-100" title="Rename">&#9998;</button>
                        <button type="button" class="cm-rename-save hidden px-2 py-1 text-[11px] font-semibold bg-emerald-600 text-white rounded hover:bg-emerald-500" title="Save name">&#10003;</button>
                        <button type="button" class="cm-rename-cancel hidden px-2 py-1 text-[11px] font-semibold border border-slate-300 rounded hover:bg-slate-100" title="Cancel">&times;</button>
                        <button type="button" class="cm-delete px-2 py-1 text-[11px] font-semibold border border-red-200 text-red-500 rounded hover:bg-red-50" title="Delete">&#128465;</button>
                    </div>
                `;

                // Load
                row.querySelector('.cm-load').addEventListener('click', () => {
                    loadChartIntoExplore(chart);
                    closeChartManage();
                });

                // Rename toggle
                const nameEl     = row.querySelector('.cm-chart-name');
                const renameWrap = row.querySelector('.cm-chart-rename');
                const renameInput= row.querySelector('.cm-rename-input');
                const renameBtn  = row.querySelector('.cm-rename');
                const saveRenBtn = row.querySelector('.cm-rename-save');
                const canRenBtn  = row.querySelector('.cm-rename-cancel');

                renameBtn.addEventListener('click', () => {
                    nameEl.classList.add('hidden');
                    renameWrap.classList.remove('hidden');
                    renameBtn.classList.add('hidden');
                    saveRenBtn.classList.remove('hidden');
                    canRenBtn.classList.remove('hidden');
                    renameInput.focus();
                });

                canRenBtn.addEventListener('click', () => {
                    nameEl.classList.remove('hidden');
                    renameWrap.classList.add('hidden');
                    renameBtn.classList.remove('hidden');
                    saveRenBtn.classList.add('hidden');
                    canRenBtn.classList.add('hidden');
                });

                saveRenBtn.addEventListener('click', () => {
                    const newName = renameInput.value.trim();
                    if (!newName) return;
                    fetch(saveChartUrl, {
                        method: 'POST', credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                        body: JSON.stringify({ id: chart.id, name: newName, chart_type: chart.chart_type, config: chart.config }),
                    })
                        .then((r) => r.json())
                        .then((data) => {
                            if (data.error) { alert('Rename failed: ' + data.error); return; }
                            chart.name = newName;
                            nameEl.textContent = newName;
                            // Toggle back
                            nameEl.classList.remove('hidden');
                            renameWrap.classList.add('hidden');
                            renameBtn.classList.remove('hidden');
                            saveRenBtn.classList.add('hidden');
                            canRenBtn.classList.add('hidden');
                            refreshAddToDashSelect();
                            // Update in _savedCharts array
                            const idx = _savedCharts.findIndex((c) => c.id === chart.id);
                            if (idx >= 0) _savedCharts[idx].name = newName;
                        })
                        .catch(() => alert('Network error.'));
                });

                // Delete
                row.querySelector('.cm-delete').addEventListener('click', () => {
                    if (!confirm(`Delete chart "${chart.name}"? This action cannot be undone.`)) return;
                    fetch(`${chartsUrl}${chart.id}/`, {
                        method: 'DELETE', credentials: 'same-origin',
                        headers: { 'X-CSRFToken': csrfToken },
                    })
                        .then((r) => r.json())
                        .then((data) => {
                            if (data.error) { alert('Delete failed: ' + data.error); return; }
                            row.remove();
                            _savedCharts = _savedCharts.filter((c) => c.id !== chart.id);
                            if (_lastSavedChartId === chart.id) _lastSavedChartId = null;
                            refreshAddToDashSelect();
                            if (!_savedCharts.length) {
                                chartsManageListEl.innerHTML = '<p class="text-xs text-slate-400 italic">No saved charts yet.</p>';
                            }
                        })
                        .catch(() => alert('Network error.'));
                });

                chartsManageListEl.appendChild(row);
            });
        }


        function loadChartIntoExplore(chart) {
            // Switch to Explore mode
            document.getElementById('dv-mode-explore') && document.getElementById('dv-mode-explore').click();

            const cfg = chart.config || {};
            const srcType = chart.source_type || 'query';

            // ── Helper: apply chart settings once data is loaded ──────────
            function _applyChartSettings() {
                // Restore chart type
                if (chartTypeEl) { chartTypeEl.value = cfg.chart_type || chart.chart_type || 'bar'; chartTypeEl.dispatchEvent(new Event('change')); }

                // Restore aggregation
                if (aggEl && cfg.agg) aggEl.value = cfg.agg;

                // Restore slot columns via assignSlot (preferred) or direct hidden-select fallback
                const slotX      = cfg.slot_x      || cfg.x_col      || '';
                const slotY      = cfg.slot_y      || cfg.y_col      || '';
                const slotSeries = cfg.slot_series || cfg.stack_col  || '';
                const slotSize   = cfg.slot_size   || cfg.size_col   || '';

                if (typeof assignSlot === 'function') {
                    if (slotX)      assignSlot('x',      slotX);
                    if (slotY)      assignSlot('y',      slotY);
                    if (slotSeries) assignSlot('series', slotSeries);
                    if (slotSize)   assignSlot('size',   slotSize);
                } else {
                    if (xColEl     && slotX)      { xColEl.value     = slotX;      xColEl.dispatchEvent(new Event('change')); }
                    if (yColEl     && slotY)      { yColEl.value     = slotY;      yColEl.dispatchEvent(new Event('change')); }
                    if (stackColEl && slotSeries) { stackColEl.value = slotSeries; stackColEl.dispatchEvent(new Event('change')); }
                    if (sizeColEl  && slotSize)   { sizeColEl.value  = slotSize;   sizeColEl.dispatchEvent(new Event('change')); }
                }

                // Restore multi-dimensional group-by
                const savedGroupBy = cfg.group_by_cols || [];
                state.chartGroupByCols = savedGroupBy.slice();
                if (typeof renderGroupByChips === 'function') renderGroupByChips();

                // Restore inline filters
                if (cfg.inline_filters && Array.isArray(cfg.inline_filters)) {
                    _inlineFilters = cfg.inline_filters.slice();
                    if (typeof renderFilterChips === 'function') renderFilterChips();
                }

                applyFilters();  // re-filter with inline filters applied
            }

            // Restore dataset filters on the UI
            const filters = chart.source_filters || {};
            const _searchEl = document.getElementById('dv-search');
            if (_searchEl) _searchEl.value = filters.search || '';
            setMultiSelectValues(schoolYearBtn, schoolYearDd, 'dv-school-year-opt',
                filters.year || filters.school_year || [], 'All');
            setMultiSelectValues(gradeBtn, gradeDd, 'dv-grade-opt',
                filters.grade || [], 'All');
            setMultiSelectValues(statusBtn, statusDd, 'dv-status-opt',
                filters.status || [], 'All');

            // Restore saved query selection
            if (savedQueryEl && chart.source_id) savedQueryEl.value = chart.source_id;

            // Restore column checkboxes from saved config
            const savedCols = cfg.selected_columns || [];
            if (savedCols.length) {
                const savedSet = new Set(savedCols);
                document.querySelectorAll('.dv-col, input[data-column]').forEach(function (input) {
                    const key = input.dataset.column || input.value;
                    input.checked = savedSet.has(key);
                });
            }

            // Update saved chart reference so Save = update
            _lastSavedChartId = chart.id;
            const chartNameEl2 = document.getElementById('dv-chart-name');
            const chartDescEl2 = document.getElementById('dv-chart-desc');
            if (chartNameEl2) chartNameEl2.value = chart.name || '';
            if (chartDescEl2) chartDescEl2.value = chart.description || '';

            // ── Load data then apply chart settings ──────────────────────
            if (srcType === 'sql' && chart.source_sql && sqlUrl) {
                // Re-run the saved SQL query
                state.sourceType = 'sql';
                state.sourceSql  = chart.source_sql;
                if (loadStatus) loadStatus.textContent = 'Running saved SQL…';
                fetch(sqlUrl, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ sql: chart.source_sql }),
                })
                    .then((r) => r.json())
                    .then((data) => {
                        if (data.error) { if (loadStatus) loadStatus.textContent = 'SQL error: ' + data.error; return; }
                        const cols = data.columns || [];
                        const rows = data.rows || [];
                        state.rows    = rows;
                        state.columns = cols;
                        state.columnTypes = {};
                        state.columnDefinitions = chart.column_defs || {};
                        state.filteredRows = rows.slice();
                        cols.forEach((c) => {
                            state.columnTypes[c] = (data.col_types || {})[c] || 'string';
                            if (!state.columnDefinitions[c]) state.columnDefinitions[c] = c;
                        });
                        // Repopulate dropdowns
                        const allCols = cols.map((c) => [c, state.columnDefinitions[c] || c]);
                        if (typeof fillSelect === 'function') {
                            fillSelect(xColEl, allCols, '', false);
                            fillSelect(yColEl, allCols, '', false);
                            fillSelect(stackColEl, allCols, '', false);
                            fillSelect(sizeColEl, allCols, '', false);
                        }
                        [xColEl, yColEl, stackColEl, sizeColEl].forEach((el) => { if (el) el.disabled = false; });
                        if (aggEl) aggEl.disabled = false;
                        if (renderBtn) renderBtn.disabled = false;
                        if (chartTypeEl) chartTypeEl.disabled = false;
                        if (emptyState)        emptyState.classList.add('hidden');
                        if (chartWrap)         chartWrap.classList.remove('hidden');
                        if (chartControlsWrap) chartControlsWrap.classList.remove('opacity-40');
                        buildDimMetricPanels();
                        if (loadStatus) loadStatus.textContent = rows.length.toLocaleString() + ' rows from SQL';
                        _applyChartSettings();
                    })
                    .catch((err) => { if (loadStatus) loadStatus.textContent = 'SQL load error: ' + err.message; });
            } else {
                // Query / dataset source — trigger normal data load, then apply settings
                state.sourceType = 'query';
                state.sourceSql  = '';

                // Wrap loadDataset so we can apply chart settings after it completes
                const _origLoadDataset = loadDataset;
                const _applyAfterLoad = async function () {
                    await _origLoadDataset();
                    _applyChartSettings();
                };
                _applyAfterLoad();
            }
        }

        if (myChartsBtnEl)       myChartsBtnEl.addEventListener('click', openChartManage);
        if (chartsManageCloseEl) chartsManageCloseEl.addEventListener('click', closeChartManage);
        if (chartsManageDoneEl)  chartsManageDoneEl.addEventListener('click', closeChartManage);

        // Alias for the "Open Appearance options" button in Customize tab
        const openAppDrawerCustBtn = document.getElementById('dv-open-appearance-drawer-customize');
        if (openAppDrawerCustBtn) {
            openAppDrawerCustBtn.addEventListener('click', () => {
                const drawerEl = document.getElementById('dv-drawer-appearance');
                const backdropEl = document.getElementById('dv-drawer-backdrop');
                if (drawerEl) drawerEl.classList.add('open');
                if (backdropEl) backdropEl.classList.add('open');
            });
        }

        if (saveCancelBtn) saveCancelBtn.addEventListener('click', closeSaveModal);
        if (saveChartBtn)  saveChartBtn.addEventListener('click', openSaveModal);

        // Clear overwrite warning when user edits the name
        if (chartNameEl) {
            chartNameEl.addEventListener('input', () => {
                if (_pendingOverwriteId) {
                    _pendingOverwriteId = null;
                    if (saveOverwriteWarnEl) saveOverwriteWarnEl.classList.add('hidden');
                    if (saveConfirmBtn) saveConfirmBtn.textContent = 'Save';
                }
            });
        }

        if (saveConfirmBtn) {
            saveConfirmBtn.addEventListener('click', () => {
                const name = chartNameEl ? chartNameEl.value.trim() : '';
                if (!name) {
                    if (saveErrorEl) { saveErrorEl.textContent = 'Name is required.'; saveErrorEl.classList.remove('hidden'); }
                    return;
                }

                // ── Overwrite-by-name: check if another chart already has this name ────
                const existingByName = _savedCharts.find(
                    (c) => c.name.trim().toLowerCase() === name.toLowerCase()
                        && c.id !== (_lastSavedChartId || 0)
                );
                if (existingByName && !_pendingOverwriteId) {
                    // First click with duplicate name → show warning and wait for re-confirm
                    _pendingOverwriteId = existingByName.id;
                    if (saveOverwriteWarnEl && saveOverwriteNameEl) {
                        saveOverwriteNameEl.textContent = `"${existingByName.name}"`;
                        saveOverwriteWarnEl.classList.remove('hidden');
                    }
                    if (saveConfirmBtn) saveConfirmBtn.textContent = 'Overwrite';
                    return;
                }

                // If pending overwrite, use that chart's ID
                const saveId = _pendingOverwriteId || _lastSavedChartId || null;

                // ── Build full chart config payload ──────────────────────────────────
                // Slot assignments (primary source of truth for x/y/series/size)
                const slotX      = (typeof slotState !== 'undefined' && slotState.x)      || (xColEl     ? xColEl.value     : '');
                const slotY      = (typeof slotState !== 'undefined' && slotState.y)      || (yColEl     ? yColEl.value     : '');
                const slotSeries = (typeof slotState !== 'undefined' && slotState.series) || (stackColEl ? stackColEl.value : '');
                const slotSize   = (typeof slotState !== 'undefined' && slotState.size)   || (sizeColEl  ? sizeColEl.value  : '');

                // Multi-dimensional x_cols: primary X + group-level extras
                const groupByCols = state.chartGroupByCols || [];
                const xCols = slotX
                    ? [slotX, ...groupByCols].filter(Boolean)
                    : groupByCols.filter(Boolean);

                const payload = {
                    id:   saveId,
                    name,
                    description: chartDescEl ? chartDescEl.value.trim() : '',
                    chart_type:  chartTypeEl ? chartTypeEl.value : 'bar',
                    config: {
                        chart_type:     chartTypeEl ? chartTypeEl.value : 'bar',
                        x_col:          slotX,
                        y_col:          slotY,
                        stack_col:      slotSeries,
                        size_col:       slotSize,
                        agg:            aggEl ? aggEl.value : 'count',
                        x_cols:         xCols,
                        group_by_cols:  groupByCols,
                        slot_x:         slotX,
                        slot_series:    slotSeries,
                        slot_y:         slotY,
                        slot_size:      slotSize,
                        inline_filters: _inlineFilters || [],
                        selected_columns: (typeof getSelectedColumns === 'function') ? getSelectedColumns() : (state.columns || []),
                        ...Object.fromEntries(
                            Object.entries(styleState).map(([k, v]) => [
                                k.replace(/([A-Z])/g, '_$1').toLowerCase(), v
                            ])
                        ),
                    },
                    source_type:    state.sourceType || 'query',
                    source_id:      (document.getElementById('dv-saved-query') || {}).value || null,
                    source_sql:     state.sourceType === 'sql' ? (state.sourceSql || '') : '',
                    source_filters: {
                        search: (document.getElementById('dv-search')      || {}).value || '',
                        year:   getMultiSelectValues(schoolYearDd, 'dv-school-year-opt'),
                        grade:  getMultiSelectValues(gradeDd, 'dv-grade-opt'),
                        status: getMultiSelectValues(statusDd, 'dv-status-opt'),
                    },
                    column_defs: state.columnDefinitions || {},
                };

                if (saveConfirmBtn) saveConfirmBtn.disabled = true;

                fetch(saveChartUrl, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify(payload),
                })
                    .then((r) => r.json())
                    .then((data) => {
                        if (saveConfirmBtn) saveConfirmBtn.disabled = false;
                        if (data.error) {
                            if (saveErrorEl) { saveErrorEl.textContent = data.error; saveErrorEl.classList.remove('hidden'); }
                            return;
                        }
                        _lastSavedChartId = data.chart && data.chart.id;
                        _pendingOverwriteId = null;
                        _savedCharts = [data.chart, ..._savedCharts.filter((c) => c.id !== data.chart.id)];
                        closeSaveModal();
                        refreshAddToDashSelect();
                    })
                    .catch((err) => {
                        if (saveConfirmBtn) saveConfirmBtn.disabled = false;
                        if (saveErrorEl) { saveErrorEl.textContent = err.message; saveErrorEl.classList.remove('hidden'); }
                    });
            });
        }
