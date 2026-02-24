(function () {
    const utils = window.DVUtils || {};
    const {
        isBlankValue,
        parseJSON,
        toNumber,
        inferColumnType,
        normalizeLabel,
        escapeHtml,
        formatValueForColumn,
        fillSelect,
        readJsonResponse,
    } = utils;

    document.addEventListener('DOMContentLoaded', function () {
        const appData = document.getElementById('dataviz-app-data');
        const appRoot = document.getElementById('dataviz-app');
        if (!appData || !appRoot || typeof echarts === 'undefined' || !window.DVChartRenderer || !readJsonResponse) {
            return;
        }

        const loadStudentsUrl = appData.dataset.loadStudentsUrl;
        const renderChartUrl = appData.dataset.renderChartUrl;
        const csrfToken = appData.dataset.csrfToken || '';
        const defaultColumns = parseJSON(appData.dataset.defaultColumns || '[]', []);
        const initialDefinitions = parseJSON(appData.dataset.columnDefinitions || '{}', {});

        const savedQueryEl = document.getElementById('dv-saved-query');
        const searchEl = document.getElementById('dv-search');
        const schoolYearEl = document.getElementById('dv-school-year');
        const gradeEl = document.getElementById('dv-grade');
        const statusEl = document.getElementById('dv-status');
        const loadBtn = document.getElementById('dv-load-data');
        const loadStatus = document.getElementById('dv-load-status');
        const useDefaultBtn = document.getElementById('dv-use-default-columns');

        const chartControlsWrap = document.getElementById('dv-chart-controls');
        const emptyState = document.getElementById('dv-empty-state');
        const chartWrap = document.getElementById('dv-chart-wrap');
        const chartEl = document.getElementById('dv-chart');
        const chartRenderer = window.DVChartRenderer.create(chartEl);
        const chartTypeEl = document.getElementById('dv-chart-type');
        const chartTypeHelpEl = document.getElementById('dv-chart-type-help');
        const xColEl = document.getElementById('dv-x-col');
        const yColEl = document.getElementById('dv-y-col');
        const stackColEl = document.getElementById('dv-stack-col');
        const aggEl = document.getElementById('dv-agg');
        const chartSourceEl = document.getElementById('dv-chart-source');
        const multiXWrapEl = document.getElementById('dv-multi-x-wrap');
        const multiXTagsEl = document.getElementById('dv-multi-x-tags');
        const groupSeriesWrapEl = document.getElementById('dv-group-series-wrap');
        const groupSeriesTagsEl = document.getElementById('dv-group-series-tags');
        const seriesHelpEl = document.getElementById('dv-series-help');
        const renderBtn = document.getElementById('dv-render-chart');
        const recommendationsEl = document.getElementById('dv-recommendations');
        const previewHead = document.getElementById('dv-preview-head');
        const previewBody = document.getElementById('dv-preview-body');
        const filterBarEl = document.getElementById('dv-filter-bar');
        const filterColsEl = document.getElementById('dv-filter-cols');
        const filterSearchEl = document.getElementById('dv-filter-search');
        const filterClearEl = document.getElementById('dv-filter-clear');
        const filterStatusEl = document.getElementById('dv-filter-status');
        const previewPageStatusEl = document.getElementById('dv-preview-page-status');
        const previewPrevBtn = document.getElementById('dv-preview-prev');
        const previewNextBtn = document.getElementById('dv-preview-next');

        const xWrap = document.getElementById('dv-wrap-x');
        const yWrap = document.getElementById('dv-wrap-y');
        const stackWrap = document.getElementById('dv-wrap-stack');

        const tabDataBtn = document.getElementById('dv-tab-data');
        const tabSummaryBtn = document.getElementById('dv-tab-summary');
        const panelDataEl = document.getElementById('dv-panel-data');
        const panelSummaryEl = document.getElementById('dv-panel-summary');
        const groupByTagsEl = document.getElementById('dv-group-by-tags');
        const groupTableWrapEl = document.getElementById('dv-group-table-wrap');
        const groupMetricModeEl = document.getElementById('dv-group-metric-mode');
        const groupViewModeEl = document.getElementById('dv-group-view-mode');
        const groupApplyViewBtn = document.getElementById('dv-group-apply-view');
        const groupBaseLabelEl = document.getElementById('dv-group-base-label');
        const groupFilterBadgeEl = document.getElementById('dv-group-filter-badge');
        const groupFilterLabelEl = document.getElementById('dv-group-filter-label');
        const groupFilterClearEl = document.getElementById('dv-group-filter-clear');

        const optTitleEl = document.getElementById('dv-opt-title');
        const optPaletteEl = document.getElementById('dv-opt-palette');
        const optBgEl = document.getElementById('dv-opt-bg');
        const optLegendEl = document.getElementById('dv-opt-legend');
        const optLabelsEl = document.getElementById('dv-opt-labels');
        const optSmoothEl = document.getElementById('dv-opt-smooth');

        const styleState = {
            title: '',
            palette: 'default',
            background: 'white',
            legendPos: 'bottom',
            dataLabels: false,
            smooth: false,
        };

        const state = {
            rows: [],
            columns: [],
            columnDefinitions: { ...initialDefinitions },
            columnTypes: {},
            recommendations: [],
            filterState: { search: '', columns: {} },
            filteredRows: [],
            groupState: { groupKeys: [] },
            groupSeriesCols: [],
            chartXCols: [],
            groupReport: { metricMode: 'all', viewMode: 'table' },
            previewPage: 1,
            previewPageSize: 100,
            chartSource: 'dataset',
        };

        function updateChartTypeHelp() {
            if (!chartTypeHelpEl || !chartTypeEl) {
                return;
            }
            const helps = {
                bar: 'Bar: compare values across categories.',
                stacked_bar: 'Stacked Bar: compare totals and composition by category.',
                line: 'Line: track trends across ordered categories.',
                area: 'Area: trend with emphasis on cumulative magnitude.',
                stacked_area: 'Stacked Area: compare cumulative trends across series.',
                pie: 'Pie: show part-to-whole distribution.',
                donut: 'Donut: pie chart with center space for cleaner labels.',
                scatter: 'Scatter: examine relationship between two numeric variables.',
                heatmap: 'Heatmap: compare intensity across a category matrix.',
                histogram: 'Histogram: inspect value distribution by bins.',
                box: 'Box Plot: show spread, median, and outliers.',
                violin: 'Violin: show distribution shape and spread.',
                funnel: 'Funnel: visualize staged drop-off.',
                treemap: 'Treemap: hierarchical part-to-whole with area encoding.',
                radar: 'Radar: compare multiple dimensions for one profile.',
                gauge: 'Gauge: show one KPI value against a range.',
            };
            chartTypeHelpEl.textContent = helps[chartTypeEl.value] || 'Pick a chart type to see guidance.';
        }

        function getSelectedRawMetricCols() {
            return (state.groupSeriesCols || [])
                .filter((c) => String(c).startsWith('__plcnt_'))
                .map((c) => String(c).replace('__plcnt_', ''));
        }

        function getVisibleGroupMetricCols(allNumericCols) {
            const mode = state.groupReport.metricMode || 'all';
            if (mode === 'count') {
                return [];
            }
            if (mode === 'selected') {
                return allNumericCols.filter((c) => (state.groupSeriesCols || []).includes(c));
            }
            return allNumericCols;
        }

        function applyGroupReportViewToChart() {
            const mode = state.groupReport.viewMode || 'table';
            if (mode === 'table') {
                renderGroupSummary();
                return;
            }

            const grouped = getChartSourceDataset();
            if (!grouped.rows.length) {
                renderGroupSummary();
                return;
            }

            const numericCols = (grouped.columns || []).filter((c) => (grouped.columnTypes[c] || 'string') === 'number');
            const metricCols = numericCols.filter((c) => c !== '__group_count' && String(c).startsWith('__plcnt_'));

            let selectedSeries;
            if ((state.groupReport.metricMode || 'all') === 'count') {
                selectedSeries = ['__group_count'];
            } else if ((state.groupReport.metricMode || 'all') === 'selected') {
                selectedSeries = state.groupSeriesCols.filter((c) => metricCols.includes(c));
                if (!selectedSeries.length) {
                    selectedSeries = metricCols.slice(0, 3);
                }
            } else {
                selectedSeries = metricCols.slice(0, 6);
                if (!selectedSeries.length) {
                    selectedSeries = ['__group_count'];
                }
            }

            state.groupSeriesCols = selectedSeries.slice();
            if (chartSourceEl) {
                chartSourceEl.value = 'group';
            }
            state.chartSource = 'group';

            if (chartTypeEl) {
                chartTypeEl.value = mode === 'stacked_bar' ? 'stacked_bar' : 'bar';
            }
            if (aggEl) {
                aggEl.value = 'sum';
            }

            const baseGroup = (state.groupState.groupKeys || [])[0] || '';
            if (baseGroup) {
                xColEl.value = baseGroup;
                state.chartXCols = [baseGroup];
            }
            yColEl.value = selectedSeries[0] || '__group_count';
            stackColEl.value = '';

            syncControlsForChartType();
            autoRenderChart();
        }

        let chartRequestNonce = 0;

        const supportedCharts = [
            'bar', 'stacked_bar', 'line', 'area', 'stacked_area',
            'pie', 'donut', 'scatter', 'heatmap', 'histogram', 'box', 'violin',
            'funnel', 'treemap', 'radar', 'gauge',
        ];

        function getSelectedColumns() {
            const seen = new Set();
            const selected = [];

            document.querySelectorAll('.dv-col:checked, input[data-column]:checked').forEach((input) => {
                const key = input.dataset.column || input.value;
                if (key && !seen.has(key)) {
                    seen.add(key);
                    selected.push(key);
                }
            });

            return selected;
        }

        function applyDefaultColumns() {
            const selected = new Set(defaultColumns || []);
            document.querySelectorAll('.dv-col, input[data-column]').forEach((input) => {
                const key = input.dataset.column || input.value;
                input.checked = selected.has(key);
            });
        }

        function initTreeToggles() {
            document.querySelectorAll('[data-toggle-category]').forEach((button) => {
                button.addEventListener('click', function () {
                    const targetKey = button.dataset.toggleCategory;
                    const target = document.querySelector(`[data-category="${targetKey}"]`);
                    if (!target) {
                        return;
                    }
                    target.classList.toggle('hidden');
                });
            });
        }

        function getActiveRows() {
            return state.filteredRows && state.filteredRows.length >= 0 ? state.filteredRows : state.rows;
        }

        function buildRecommendations() {
            const numberCols = state.columns.filter((column) => (state.columnTypes[column] || 'string') === 'number');
            const stringCols = state.columns.filter((column) => (state.columnTypes[column] || 'string') !== 'number');

            const recs = [];
            if (stringCols.length) {
                recs.push(`Use ${normalizeLabel(state.columnDefinitions[stringCols[0]] || stringCols[0])} as category`);
            }
            if (numberCols.length) {
                recs.push(`Use ${normalizeLabel(state.columnDefinitions[numberCols[0]] || numberCols[0])} as value`);
            }
            if (!recs.length) {
                recs.push('Load data and select columns to see recommendations');
            }

            state.recommendations = recs;
            recommendationsEl.textContent = recs.join(' • ');
        }

        function renderPreview() {
            const rows = getActiveRows();
            const columns = state.columns;

            if (!columns.length) {
                previewHead.innerHTML = '';
                previewBody.innerHTML = '<tr><td class="px-3 py-4 text-slate-500" colspan="1">No columns selected.</td></tr>';
                if (previewPageStatusEl) {
                    previewPageStatusEl.textContent = 'Rows 0-0 of 0';
                }
                if (previewPrevBtn) {
                    previewPrevBtn.disabled = true;
                }
                if (previewNextBtn) {
                    previewNextBtn.disabled = true;
                }
                return;
            }

            previewHead.innerHTML = `<tr>${columns.map((column) => {
                const label = normalizeLabel(state.columnDefinitions[column] || column);
                return `<th class="text-left px-3 py-2 font-semibold text-slate-700 whitespace-nowrap">${label}</th>`;
            }).join('')}</tr>`;

            if (!rows.length) {
                previewBody.innerHTML = `<tr><td class="px-3 py-4 text-slate-400 italic" colspan="${columns.length}">No rows match current filters.</td></tr>`;
                if (previewPageStatusEl) {
                    previewPageStatusEl.textContent = 'Rows 0-0 of 0';
                }
                if (previewPrevBtn) {
                    previewPrevBtn.disabled = true;
                }
                if (previewNextBtn) {
                    previewNextBtn.disabled = true;
                }
                return;
            }

            const totalRows = rows.length;
            const pageSize = state.previewPageSize || 100;
            const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
            if (state.previewPage > totalPages) {
                state.previewPage = totalPages;
            }
            if (state.previewPage < 1) {
                state.previewPage = 1;
            }

            const start = (state.previewPage - 1) * pageSize;
            const end = Math.min(start + pageSize, totalRows);
            const sample = rows.slice(start, end);

            previewBody.innerHTML = sample.map((row) => {
                return `<tr class="hover:bg-slate-50">${columns.map((column) => {
                    const raw = row[column];
                    const text = isBlankValue(raw) ? '—' : formatValueForColumn(column, raw);
                    return `<td class="px-3 py-2 text-slate-700 whitespace-nowrap">${escapeHtml(text)}</td>`;
                }).join('')}</tr>`;
            }).join('');

            if (previewPageStatusEl) {
                previewPageStatusEl.textContent = `Rows ${start + 1}-${end} of ${totalRows.toLocaleString()}`;
            }
            if (previewPrevBtn) {
                previewPrevBtn.disabled = state.previewPage <= 1;
            }
            if (previewNextBtn) {
                previewNextBtn.disabled = state.previewPage >= totalPages;
            }
        }

        function updateFilterStatus() {
            const total = state.rows.length;
            const filtered = getActiveRows().length;
            if (!total) {
                filterStatusEl.textContent = '';
                return;
            }
            if (filtered === total) {
                filterStatusEl.textContent = `${total.toLocaleString()} rows`;
                return;
            }
            filterStatusEl.textContent = `${filtered.toLocaleString()} of ${total.toLocaleString()} rows (filtered)`;
        }

        function applyFilters() {
            const baseRows = state.rows;
            const activeSearch = (state.filterState.search || '').toLowerCase().trim();
            const activeColumnFilters = state.filterState.columns || {};

            state.filteredRows = baseRows.filter((row) => {
                if (activeSearch) {
                    const searchHit = state.columns.some((column) => {
                        const raw = row[column];
                        if (isBlankValue(raw)) {
                            return false;
                        }
                        return String(raw).toLowerCase().includes(activeSearch);
                    });
                    if (!searchHit) {
                        return false;
                    }
                }

                for (const [col, filterVal] of Object.entries(activeColumnFilters)) {
                    if (filterVal == null || filterVal === '' || (typeof filterVal === 'object' && !Object.keys(filterVal).length)) {
                        continue;
                    }

                    const colType = state.columnTypes[col] || 'string';
                    if (Array.isArray(filterVal)) {
                        const rowVal = isBlankValue(row[col]) ? '(Blank)' : String(row[col]);
                        if (!filterVal.includes(rowVal)) {
                            return false;
                        }
                    } else if (colType === 'number' && typeof filterVal === 'object') {
                        const num = toNumber(row[col]);
                        if (num === null) {
                            return false;
                        }
                        const min = filterVal.min;
                        const max = filterVal.max;
                        if (min !== '' && min != null && num < Number(min)) {
                            return false;
                        }
                        if (max !== '' && max != null && num > Number(max)) {
                            return false;
                        }
                    } else {
                        const rowVal = isBlankValue(row[col]) ? '(Blank)' : String(row[col]);
                        if (rowVal !== filterVal) {
                            return false;
                        }
                    }
                }
                return true;
            });

            updateFilterStatus();
            state.previewPage = 1;
            renderPreview();
            autoRenderChart();

            if (panelSummaryEl && !panelSummaryEl.classList.contains('hidden')) {
                renderGroupSummary();
            }
        }

        function buildFilterBar() {
            if (!filterColsEl || !filterBarEl) {
                return;
            }

            filterColsEl.innerHTML = '';
            const rows = state.rows;

            const closeFilterPanels = () => {
                filterColsEl.querySelectorAll('[data-dv-filter-panel]').forEach((panel) => {
                    panel.classList.add('hidden');
                });
            };

            if (!filterColsEl.dataset.outsideBound) {
                filterColsEl.dataset.outsideBound = 'true';
                document.addEventListener('click', (event) => {
                    if (!filterColsEl.contains(event.target)) {
                        closeFilterPanels();
                    }
                });
            }

            state.columns.forEach((col) => {
                const colType = state.columnTypes[col] || 'string';
                const label = normalizeLabel(state.columnDefinitions[col] || col);

                if (colType === 'number') {
                    const nums = rows.map((r) => toNumber(r[col])).filter((n) => n !== null);
                    if (!nums.length) {
                        return;
                    }
                    const minVal = Math.min(...nums);
                    const maxVal = Math.max(...nums);

                    const existing = state.filterState.columns[col];
                    const hasActive = !!(existing && typeof existing === 'object' && !Array.isArray(existing) && (existing.min !== '' || existing.max !== ''));

                    const wrap = document.createElement('div');
                    wrap.className = 'relative';
                    wrap.innerHTML = `
                        <button type="button" data-dv-filter-toggle class="text-[11px] font-semibold px-2 py-1 rounded border ${hasActive ? 'bg-blue-50 text-blue-700 border-blue-300' : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'}">${escapeHtml(label)}</button>
                        <div data-dv-filter-panel class="hidden absolute z-20 mt-1 w-64 bg-white border border-slate-200 rounded-md shadow-lg p-2 space-y-2">
                            <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Range</div>
                            <div class="flex items-center gap-2">
                                <input type="number" step="any" data-bound="min" placeholder="Min" class="w-full px-2 py-1 text-xs border border-slate-300 rounded" value="${existing && existing.min != null ? escapeHtml(existing.min) : ''}">
                                <span class="text-xs text-slate-400">to</span>
                                <input type="number" step="any" data-bound="max" placeholder="Max" class="w-full px-2 py-1 text-xs border border-slate-300 rounded" value="${existing && existing.max != null ? escapeHtml(existing.max) : ''}">
                            </div>
                            <div class="text-[10px] text-slate-400">Data range: ${minVal.toLocaleString()} – ${maxVal.toLocaleString()}</div>
                            <div class="flex items-center justify-between border-t border-slate-200 pt-2">
                                <button type="button" data-dv-filter-reset class="text-xs text-slate-500 hover:text-slate-700">Reset</button>
                                <button type="button" data-dv-filter-apply class="text-xs font-semibold text-blue-600 hover:text-blue-800">Apply</button>
                            </div>
                        </div>
                    `;

                    const toggleBtn = wrap.querySelector('[data-dv-filter-toggle]');
                    const panel = wrap.querySelector('[data-dv-filter-panel]');
                    const minInput = wrap.querySelector('input[data-bound="min"]');
                    const maxInput = wrap.querySelector('input[data-bound="max"]');
                    const applyBtn = wrap.querySelector('[data-dv-filter-apply]');
                    const resetBtn = wrap.querySelector('[data-dv-filter-reset]');

                    minInput.min = minVal;
                    minInput.max = maxVal;
                    maxInput.min = minVal;
                    maxInput.max = maxVal;

                    toggleBtn.addEventListener('click', (event) => {
                        event.stopPropagation();
                        const willOpen = panel.classList.contains('hidden');
                        closeFilterPanels();
                        panel.classList.toggle('hidden', !willOpen);
                    });

                    applyBtn.addEventListener('click', () => {
                        const min = minInput.value;
                        const max = maxInput.value;
                        if (min === '' && max === '') {
                            delete state.filterState.columns[col];
                        } else {
                            state.filterState.columns[col] = { min, max };
                        }
                        panel.classList.add('hidden');
                        applyFilters();
                    });

                    resetBtn.addEventListener('click', () => {
                        minInput.value = '';
                        maxInput.value = '';
                        delete state.filterState.columns[col];
                        panel.classList.add('hidden');
                        applyFilters();
                    });

                    filterColsEl.appendChild(wrap);
                    return;
                }

                const distinct = new Set();
                for (const row of rows) {
                    distinct.add(isBlankValue(row[col]) ? '(Blank)' : String(row[col]));
                    if (distinct.size > 50) {
                        break;
                    }
                }

                if (distinct.size < 2 || distinct.size > 50) {
                    return;
                }

                const existingValsRaw = state.filterState.columns[col];
                const existingVals = Array.isArray(existingValsRaw)
                    ? existingValsRaw.slice()
                    : (typeof existingValsRaw === 'string' ? [existingValsRaw] : []);

                const sortedOptions = Array.from(distinct).sort((a, b) => String(a).localeCompare(String(b)));
                const hasActive = existingVals.length > 0;

                const wrap = document.createElement('div');
                wrap.className = 'relative';
                wrap.innerHTML = `
                    <button type="button" data-dv-filter-toggle class="text-[11px] font-semibold px-2 py-1 rounded border ${hasActive ? 'bg-blue-50 text-blue-700 border-blue-300' : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'}">${escapeHtml(label)}${hasActive ? ` (${existingVals.length})` : ''}</button>
                    <div data-dv-filter-panel class="hidden absolute z-20 mt-1 w-64 bg-white border border-slate-200 rounded-md shadow-lg">
                        <div class="flex items-center justify-between px-2 py-1 text-[11px] text-slate-500 border-b border-slate-200">
                            <button type="button" data-dv-filter-selectall class="hover:text-slate-700">Select all</button>
                            <button type="button" data-dv-filter-clearall class="hover:text-slate-700">Clear</button>
                        </div>
                        <div class="max-h-56 overflow-auto p-2 space-y-1" data-dv-options></div>
                        <div class="flex items-center justify-between border-t border-slate-200 px-2 py-1 text-xs">
                            <button type="button" data-dv-filter-reset class="text-slate-500 hover:text-slate-700">Reset</button>
                            <button type="button" data-dv-filter-apply class="text-blue-600 hover:text-blue-700 font-semibold">Apply</button>
                        </div>
                    </div>
                `;

                const toggleBtn = wrap.querySelector('[data-dv-filter-toggle]');
                const panel = wrap.querySelector('[data-dv-filter-panel]');
                const optionsWrap = wrap.querySelector('[data-dv-options]');
                const applyBtn = wrap.querySelector('[data-dv-filter-apply]');
                const resetBtn = wrap.querySelector('[data-dv-filter-reset]');
                const selAllBtn = wrap.querySelector('[data-dv-filter-selectall]');
                const clearAllBtn = wrap.querySelector('[data-dv-filter-clearall]');

                optionsWrap.innerHTML = sortedOptions.map((value) => {
                    const display = value === '(Blank)'
                        ? '(Blank)'
                        : formatValueForColumn(col, value);
                    const checked = existingVals.includes(value) ? 'checked' : '';
                    return `
                        <label class="flex items-center gap-2 text-xs text-slate-700 p-1 rounded hover:bg-slate-50">
                            <input type="checkbox" data-dv-filter-option value="${escapeHtml(value)}" ${checked} class="rounded border-slate-300 text-blue-600 focus:ring-blue-600">
                            <span>${escapeHtml(display)}</span>
                        </label>
                    `;
                }).join('');

                toggleBtn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    const willOpen = panel.classList.contains('hidden');
                    closeFilterPanels();
                    panel.classList.toggle('hidden', !willOpen);
                });

                selAllBtn.addEventListener('click', () => {
                    optionsWrap.querySelectorAll('input[data-dv-filter-option]').forEach((input) => {
                        input.checked = true;
                    });
                });

                clearAllBtn.addEventListener('click', () => {
                    optionsWrap.querySelectorAll('input[data-dv-filter-option]').forEach((input) => {
                        input.checked = false;
                    });
                });

                applyBtn.addEventListener('click', () => {
                    const values = Array.from(optionsWrap.querySelectorAll('input[data-dv-filter-option]:checked')).map((input) => input.value);
                    if (values.length) {
                        state.filterState.columns[col] = values;
                    } else {
                        delete state.filterState.columns[col];
                    }
                    panel.classList.add('hidden');
                    applyFilters();
                });

                resetBtn.addEventListener('click', () => {
                    delete state.filterState.columns[col];
                    optionsWrap.querySelectorAll('input[data-dv-filter-option]').forEach((input) => {
                        input.checked = false;
                    });
                    panel.classList.add('hidden');
                    applyFilters();
                });

                filterColsEl.appendChild(wrap);
            });

            filterSearchEl.value = state.filterState.search || '';
            filterSearchEl.oninput = () => {
                state.filterState.search = filterSearchEl.value || '';
                applyFilters();
            };

            filterClearEl.onclick = () => {
                state.filterState = { search: '', columns: {} };
                filterSearchEl.value = '';
                closeFilterPanels();
                if (groupFilterBadgeEl) {
                    groupFilterBadgeEl.classList.add('hidden');
                    groupFilterBadgeEl.classList.remove('flex');
                }
                buildFilterBar();
                applyFilters();
            };

            filterBarEl.classList.remove('hidden');
            updateFilterStatus();
        }

        function setControlVisible(wrapperEl, visible) {
            if (!wrapperEl) {
                return;
            }
            wrapperEl.style.display = visible ? '' : 'none';
        }

        function getChartSourceDataset() {
            return {
                rows: getActiveRows(),
                columns: state.columns,
                columnTypes: state.columnTypes,
                columnDefinitions: state.columnDefinitions,
                usingGroup: false,
            };
        }

        function getColumnPools(chartColumnTypes, chartColumns) {
            const number = [];
            const nonNumber = [];

            chartColumns.forEach((column) => {
                if ((chartColumnTypes[column] || 'string') === 'number') {
                    number.push(column);
                } else {
                    nonNumber.push(column);
                }
            });

            return { number, nonNumber };
        }

        function syncControlsForChartType() {
            const chartType = chartTypeEl.value || 'bar';
            const chartDataset = getChartSourceDataset();
            const pools = getColumnPools(chartDataset.columnTypes, chartDataset.columns);

            let xPool = pools.nonNumber;
            let yPool = pools.number;
            let stackPool = pools.nonNumber;
            let showX = true;
            let showY = true;
            let showStack = true;

            if (chartType === 'scatter') {
                xPool = pools.number;
                yPool = pools.number;
                stackPool = pools.nonNumber;
            } else if (chartType === 'heatmap') {
                xPool = pools.nonNumber;
                yPool = pools.nonNumber;
                stackPool = [];
                showStack = false;
            } else if (chartType === 'pie' || chartType === 'donut' || chartType === 'funnel' || chartType === 'treemap' || chartType === 'radar') {
                xPool = pools.nonNumber;
                yPool = pools.number;
                stackPool = [];
                showStack = false;
            } else if (chartType === 'gauge') {
                xPool = [];
                yPool = pools.number;
                stackPool = [];
                showX = false;
                showStack = false;
            } else if (chartType === 'histogram') {
                xPool = [...pools.number, ...pools.nonNumber];
                yPool = [];
                showY = false;
                stackPool = pools.nonNumber;
            } else if (chartType === 'box' || chartType === 'violin') {
                xPool = pools.nonNumber;
                yPool = pools.number;
                stackPool = [];
                showStack = false;
            }

            const xCurrent = xColEl.value;
            const yCurrent = yColEl.value;
            const sCurrent = stackColEl.value;

            fillSelect(xColEl, xPool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'Select...');
            fillSelect(yColEl, yPool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'Select...');
            fillSelect(stackColEl, stackPool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'None');

            if (xPool.includes(xCurrent)) {
                xColEl.value = xCurrent;
            }
            if (yPool.includes(yCurrent)) {
                yColEl.value = yCurrent;
            }
            if (stackPool.includes(sCurrent)) {
                stackColEl.value = sCurrent;
            }

            if (!xColEl.value && xPool.length) {
                xColEl.value = xPool[0];
            }
            if (!yColEl.value && yPool.length) {
                yColEl.value = yPool[0];
            }

            setControlVisible(xWrap, showX);
            setControlVisible(yWrap, showY);
            setControlVisible(stackWrap, showStack);

            const canUseMultiX = showX && ['bar', 'stacked_bar', 'line', 'area', 'stacked_area'].includes(chartType) && xPool.length > 1;
            if (multiXWrapEl) {
                multiXWrapEl.classList.toggle('hidden', !canUseMultiX);
            }
            if (canUseMultiX) {
                buildMultiXAxisUI(chartDataset, xPool);
            } else {
                state.chartXCols = [];
                if (multiXTagsEl) {
                    multiXTagsEl.innerHTML = '';
                }
            }

            const canUseMultiSeries = (chartType === 'bar' || chartType === 'stacked_bar') && pools.number.length > 1;
            if (groupSeriesWrapEl) {
                groupSeriesWrapEl.classList.toggle('hidden', !canUseMultiSeries);
            }
            if (canUseMultiSeries) {
                buildGroupSeriesUI(chartDataset);
            } else {
                state.groupSeriesCols = [];
                if (groupSeriesTagsEl) {
                    groupSeriesTagsEl.innerHTML = '';
                }
            }

            xColEl.disabled = !showX;
            yColEl.disabled = !showY;
            stackColEl.disabled = !showStack;
        }

        function buildMultiXAxisUI(chartDataset, xPool) {
            if (!multiXTagsEl) {
                return;
            }

            state.chartXCols = state.chartXCols.filter((c) => xPool.includes(c));
            if (xColEl.value && !state.chartXCols.includes(xColEl.value)) {
                state.chartXCols.unshift(xColEl.value);
            }
            state.chartXCols = state.chartXCols.slice(0, 4);

            multiXTagsEl.innerHTML = '';
            xPool.forEach((col) => {
                const active = state.chartXCols.includes(col);
                const label = normalizeLabel((chartDataset.columnDefinitions && chartDataset.columnDefinitions[col]) || state.columnDefinitions[col] || col);

                const btn = document.createElement('button');
                btn.type = 'button';
                btn.dataset.col = col;
                btn.className = active
                    ? 'px-2.5 py-1 text-xs border-2 border-indigo-600 bg-indigo-50 rounded-full text-indigo-700 font-semibold'
                    : 'px-2.5 py-1 text-xs border border-slate-300 rounded-full text-slate-600 hover:border-indigo-400 hover:text-indigo-700 transition-colors';
                btn.title = label;
                btn.textContent = label.length > 24 ? `${label.slice(0, 22)}…` : label;

                btn.addEventListener('click', () => {
                    const idx = state.chartXCols.indexOf(col);
                    if (idx >= 0) {
                        state.chartXCols.splice(idx, 1);
                    } else {
                        state.chartXCols.push(col);
                    }

                    if (!state.chartXCols.length) {
                        state.chartXCols = [col];
                    }
                    if (!state.chartXCols.includes(xColEl.value)) {
                        xColEl.value = state.chartXCols[0];
                    }

                    syncControlsForChartType();
                    autoRenderChart();
                });

                multiXTagsEl.appendChild(btn);
            });
        }

        function buildGroupSeriesUI(chartDataset) {
            if (!groupSeriesTagsEl) {
                return;
            }

            const metricCols = state.chartSource === 'group'
                ? (chartDataset.columns || []).filter((col) => String(col).startsWith('__plcnt_'))
                : (chartDataset.columns || []).filter((col) => (chartDataset.columnTypes[col] || 'string') === 'number');

            if (seriesHelpEl) {
                seriesHelpEl.textContent = 'Use with Bar / Stacked Bar to compare multiple numeric metrics from the dataset.';
            }

            if (!metricCols.length) {
                groupSeriesTagsEl.innerHTML = '<p class="text-xs text-slate-400 italic">No multi-series metrics available.</p>';
                state.groupSeriesCols = [];
                return;
            }

            state.groupSeriesCols = state.groupSeriesCols.filter((c) => metricCols.includes(c));

            groupSeriesTagsEl.innerHTML = '';
            metricCols.forEach((col) => {
                const active = state.groupSeriesCols.includes(col);
                const label = normalizeLabel((chartDataset.columnDefinitions && chartDataset.columnDefinitions[col]) || state.columnDefinitions[col] || col);

                const btn = document.createElement('button');
                btn.type = 'button';
                btn.dataset.col = col;
                btn.className = active
                    ? 'px-2.5 py-1 text-xs border-2 border-blue-600 bg-blue-50 rounded-full text-blue-700 font-semibold'
                    : 'px-2.5 py-1 text-xs border border-slate-300 rounded-full text-slate-600 hover:border-blue-400 hover:text-blue-700 transition-colors';
                btn.title = label;
                btn.textContent = label.length > 24 ? `${label.slice(0, 22)}…` : label;

                btn.addEventListener('click', () => {
                    const idx = state.groupSeriesCols.indexOf(col);
                    if (idx >= 0) {
                        state.groupSeriesCols.splice(idx, 1);
                    } else {
                        state.groupSeriesCols.push(col);
                    }

                    if (!state.groupSeriesCols.length) {
                        yColEl.value = col;
                    } else if (!state.groupSeriesCols.includes(yColEl.value)) {
                        yColEl.value = state.groupSeriesCols[0];
                    }

                    syncControlsForChartType();
                    autoRenderChart();
                });

                groupSeriesTagsEl.appendChild(btn);
            });
        }

        function switchTab(name) {
            const isData = name === 'data';

            if (panelDataEl) {
                panelDataEl.classList.toggle('hidden', !isData);
            }
            if (panelSummaryEl) {
                panelSummaryEl.classList.toggle('hidden', isData);
            }

            if (tabDataBtn && tabSummaryBtn) {
                const activate = (el) => {
                    el.classList.add('border-slate-900', 'text-slate-900');
                    el.classList.remove('border-transparent', 'text-slate-400');
                };
                const deactivate = (el) => {
                    el.classList.remove('border-slate-900', 'text-slate-900');
                    el.classList.add('border-transparent', 'text-slate-400');
                };

                if (isData) {
                    activate(tabDataBtn);
                    deactivate(tabSummaryBtn);
                } else {
                    activate(tabSummaryBtn);
                    deactivate(tabDataBtn);
                    renderGroupSummary();
                }
            }
        }

        function computeGroupSummary(rows, groupKeys) {
            if (!groupKeys.length) {
                return [];
            }

            const numericCols = state.columns
                .filter((c) => (state.columnTypes[c] || 'string') === 'number')
                .slice(0, 6);

            const groups = new Map();

            rows.forEach((row) => {
                const keyParts = groupKeys.map((k) => (isBlankValue(row[k]) ? '(Blank)' : String(row[k])));
                const joinedKey = keyParts.join('‖');

                if (!groups.has(joinedKey)) {
                    const entry = { keyParts, count: 0 };
                    numericCols.forEach((c) => {
                        entry[`sum_${c}`] = 0;
                        entry[`cnt_${c}`] = 0;
                    });
                    groups.set(joinedKey, entry);
                }

                const entry = groups.get(joinedKey);
                entry.count += 1;

                numericCols.forEach((c) => {
                    const n = toNumber(row[c]);
                    if (n !== null) {
                        entry[`sum_${c}`] += n;
                        entry[`cnt_${c}`] += 1;
                    }
                });
            });

            return Array.from(groups.values()).sort((a, b) => b.count - a.count).map((entry) => ({
                keyParts: entry.keyParts,
                count: entry.count,
                numericCols,
                entry,
            }));
        }

        function setGroupFilter(keyParts, groupKeys) {
            groupKeys.forEach((k, i) => {
                state.filterState.columns[k] = keyParts[i];
            });

            buildFilterBar();

            if (groupFilterBadgeEl && groupFilterLabelEl) {
                const label = groupKeys.map((k, i) => `${normalizeLabel(state.columnDefinitions[k] || k)} = ${keyParts[i]}`).join(' · ');
                groupFilterLabelEl.textContent = label;
                groupFilterBadgeEl.classList.remove('hidden');
                groupFilterBadgeEl.classList.add('flex');
            }

            applyFilters();
            switchTab('data');
        }

        function renderGroupSummary() {
            if (!groupTableWrapEl) {
                return;
            }
            if (groupBaseLabelEl) {
                const base = (state.groupState.groupKeys || []).map((k) => normalizeLabel(state.columnDefinitions[k] || k));
                groupBaseLabelEl.textContent = base.length ? `Base: ${base.join(' · ')}` : 'Base: none';
            }
            if (!state.groupState.groupKeys.length) {
                groupTableWrapEl.innerHTML = '<p class="px-4 py-6 text-xs text-slate-400 italic text-center">Select at least one column above to see grouped data.</p>';
                return;
            }

            const grouped = getChartSourceDataset();
            const summary = grouped.rows || [];
            const allPlMetricCols = (grouped.columns || []).filter((c) => String(c).startsWith('__plcnt_'));
            const metricCols = getVisibleGroupMetricCols(allPlMetricCols);

            if (!summary.length) {
                groupTableWrapEl.innerHTML = '<p class="px-4 py-6 text-xs text-slate-400 italic text-center">No data to group.</p>';
                return;
            }

            const table = document.createElement('table');
            table.className = 'min-w-full text-xs';

            const thead = document.createElement('thead');
            const headRow = document.createElement('tr');
            headRow.className = 'bg-slate-50 sticky top-0';

            state.groupState.groupKeys.forEach((k) => {
                const th = document.createElement('th');
                th.className = 'text-left px-3 py-2 font-semibold text-slate-700 whitespace-nowrap border-b border-slate-200';
                th.textContent = normalizeLabel(state.columnDefinitions[k] || k);
                headRow.appendChild(th);
            });

            const countTh = document.createElement('th');
            countTh.className = 'text-right px-3 py-2 font-semibold text-slate-700 whitespace-nowrap border-b border-slate-200';
            countTh.textContent = 'Count';
            headRow.appendChild(countTh);

            metricCols.forEach((c) => {
                const th = document.createElement('th');
                th.className = 'text-right px-3 py-2 font-semibold text-slate-700 whitespace-nowrap border-b border-slate-200';
                const raw = normalizeLabel((grouped.columnDefinitions && grouped.columnDefinitions[c]) || state.columnDefinitions[c] || c);
                th.textContent = raw.length > 26 ? `${raw.slice(0, 24)}…` : raw;
                th.title = raw;
                headRow.appendChild(th);
            });

            const actionTh = document.createElement('th');
            actionTh.className = 'px-3 py-2 border-b border-slate-200';
            headRow.appendChild(actionTh);

            thead.appendChild(headRow);
            table.appendChild(thead);

            const tbody = document.createElement('tbody');
            tbody.className = 'divide-y divide-slate-100';

            summary.slice(0, 200).forEach((groupRow) => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-blue-50 cursor-pointer transition-colors group';

                const keyParts = state.groupState.groupKeys.map((k) => String(groupRow[k] ?? '(Blank)'));
                keyParts.forEach((val) => {
                    const td = document.createElement('td');
                    td.className = 'px-3 py-2 text-slate-800 whitespace-nowrap';
                    td.textContent = val;
                    tr.appendChild(td);
                });

                const countTd = document.createElement('td');
                countTd.className = 'px-3 py-2 text-right font-semibold text-slate-700';
                countTd.textContent = Number(groupRow.__group_count || 0).toLocaleString();
                tr.appendChild(countTd);

                metricCols.forEach((c) => {
                    const td = document.createElement('td');
                    td.className = 'px-3 py-2 text-right text-slate-600';
                    const val = Number(groupRow[c] || 0);
                    td.textContent = val.toLocaleString();
                    tr.appendChild(td);
                });

                const btnTd = document.createElement('td');
                btnTd.className = 'px-3 py-2 text-center';
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'text-[11px] font-semibold text-blue-600 hover:text-blue-900 border border-blue-200 rounded px-2 py-0.5 hover:bg-blue-50 transition-colors whitespace-nowrap opacity-0 group-hover:opacity-100';
                btn.textContent = 'Filter ▸';
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    setGroupFilter(keyParts, state.groupState.groupKeys);
                });
                btnTd.appendChild(btn);
                tr.appendChild(btnTd);

                tr.addEventListener('click', () => setGroupFilter(keyParts, state.groupState.groupKeys));
                tbody.appendChild(tr);
            });

            if (summary.length > 200) {
                const tr = document.createElement('tr');
                const td = document.createElement('td');
                td.colSpan = state.groupState.groupKeys.length + metricCols.length + 2;
                td.className = 'px-3 py-2 text-center text-[11px] text-slate-400 italic';
                td.textContent = `Showing 200 of ${summary.length.toLocaleString()} groups`;
                tr.appendChild(td);
                tbody.appendChild(tr);
            }

            table.appendChild(tbody);
            groupTableWrapEl.innerHTML = '';
            groupTableWrapEl.appendChild(table);
        }

        function buildGroupByUI() {
            if (!groupByTagsEl) {
                return;
            }

            state.groupState.groupKeys = [];
            groupByTagsEl.innerHTML = '';

            const eligibleCols = state.columns.filter((col) => {
                if ((state.columnTypes[col] || 'string') !== 'string') {
                    return false;
                }

                const distinct = new Set();
                for (const row of state.rows) {
                    distinct.add(isBlankValue(row[col]) ? '(Blank)' : String(row[col]));
                    if (distinct.size > 50) {
                        return false;
                    }
                }
                return distinct.size >= 2;
            });

            if (!eligibleCols.length) {
                groupByTagsEl.innerHTML = '<p class="text-xs text-slate-400 italic">No suitable group columns in this dataset.</p>';
                return;
            }

            eligibleCols.forEach((col) => {
                const label = normalizeLabel(state.columnDefinitions[col] || col);
                const cbId = `dv-gb-${col.replace(/[^a-z0-9]/gi, '_')}`;

                const tag = document.createElement('label');
                tag.htmlFor = cbId;
                tag.className = 'flex items-center gap-1.5 px-2.5 py-1 rounded-full border cursor-pointer select-none text-xs font-semibold border-slate-300 text-slate-600 hover:border-blue-400 hover:text-blue-700 bg-white transition-colors';
                tag.title = label;

                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.id = cbId;
                cb.value = col;
                cb.className = 'sr-only';

                const span = document.createElement('span');
                span.textContent = label.length > 22 ? label.slice(0, 20) + '…' : label;

                cb.addEventListener('change', () => {
                    if (cb.checked) {
                        if (state.groupState.groupKeys.length >= 3) {
                            cb.checked = false;
                            return;
                        }
                        state.groupState.groupKeys.push(col);
                        tag.classList.add('border-blue-500', 'text-blue-700', 'bg-blue-50');
                        tag.classList.remove('border-slate-300', 'text-slate-600', 'bg-white');
                    } else {
                        state.groupState.groupKeys = state.groupState.groupKeys.filter((k) => k !== col);
                        tag.classList.remove('border-blue-500', 'text-blue-700', 'bg-blue-50');
                        tag.classList.add('border-slate-300', 'text-slate-600', 'bg-white');
                    }
                    renderGroupSummary();
                    if (state.chartSource === 'group') {
                        state.groupSeriesCols = [];
                        state.chartXCols = [];
                        syncControlsForChartType();
                        autoRenderChart();
                    }
                });

                tag.appendChild(cb);
                tag.appendChild(span);
                groupByTagsEl.appendChild(tag);
            });
        }

        function getChartConfig() {
            const effectiveAgg = aggEl.value || 'count';
            const xCols = (state.chartXCols && state.chartXCols.length)
                ? state.chartXCols.slice()
                : (xColEl.value ? [xColEl.value] : []);
            return {
                chart_type: chartTypeEl.value || 'bar',
                x_col: xColEl.value || '',
                x_cols: xCols,
                y_col: yColEl.value || '',
                y_cols: state.groupSeriesCols.slice(),
                stack_col: stackColEl.value || '',
                agg: effectiveAgg,
                title: styleState.title || '',
                palette: styleState.palette || 'default',
                background: styleState.background || 'white',
                legend_pos: styleState.legendPos || 'bottom',
                data_labels: !!styleState.dataLabels,
                smooth: !!styleState.smooth,
                source: 'dataset',
            };
        }

        function getChartRequiredColumns(chartConfig) {
            const cols = new Set();
            [chartConfig.x_col, chartConfig.y_col, chartConfig.stack_col].forEach((c) => {
                if (c) {
                    cols.add(c);
                }
            });

            (chartConfig.x_cols || []).forEach((c) => {
                if (c) {
                    cols.add(c);
                }
            });

            (chartConfig.y_cols || []).forEach((c) => {
                if (c) {
                    cols.add(c);
                }
            });

            return Array.from(cols);
        }

        function projectRowsForChart(rows, requiredColumns) {
            if (!requiredColumns.length) {
                return rows;
            }
            return rows.map((row) => {
                const next = {};
                requiredColumns.forEach((c) => {
                    next[c] = row[c];
                });
                return next;
            });
        }

        async function renderChart() {
            if (!renderChartUrl) {
                loadStatus.textContent = 'Render API URL missing';
                return;
            }

            if (!chartTypeEl.value) {
                return;
            }

            const chartDataset = getChartSourceDataset();
            const activeRows = chartDataset.rows;

            if (!activeRows.length) {
                if (chartRenderer) {
                    chartRenderer.showMessage(chartDataset.groupError || 'No rows to render', false);
                }
                return;
            }

            const reqId = ++chartRequestNonce;
            renderBtn.disabled = true;
            loadStatus.textContent = 'Rendering chart...';

            try {
                const chartConfig = getChartConfig();
                const requiredColumns = getChartRequiredColumns(chartConfig);
                const chartRows = projectRowsForChart(activeRows, requiredColumns);

                const response = await fetch(renderChartUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                    },
                    body: JSON.stringify({
                        rows: chartRows,
                        column_definitions: chartDataset.columnDefinitions,
                        chart_config: chartConfig,
                    }),
                });

                const payload = await readJsonResponse(response, 'Chart API');
                if (!response.ok || !payload.option) {
                    throw new Error(payload.error || 'Failed to render chart');
                }

                if (reqId !== chartRequestNonce) {
                    return;
                }

                if (chartRenderer) {
                    chartRenderer.render(payload.option);
                }

                loadStatus.textContent = `Rendered ${activeRows.length.toLocaleString()} rows`;
            } catch (error) {
                loadStatus.textContent = 'Could not render chart';
                if (chartRenderer) {
                    chartRenderer.showMessage(String(error.message || error), true);
                }
            } finally {
                renderBtn.disabled = false;
            }
        }

        function autoRenderChart() {
            if (renderBtn.disabled || !state.columns.length) {
                return;
            }
            renderChart();
        }

        async function loadDataset() {
            loadBtn.disabled = true;
            loadStatus.textContent = 'Loading...';

            const params = new URLSearchParams();
            params.set('format', 'json');
            params.set('limit', '1500');

            const selectedColumns = getSelectedColumns();
            if (selectedColumns.length) {
                params.set('columns', JSON.stringify(selectedColumns));
            }

            if (savedQueryEl && savedQueryEl.value) {
                params.set('saved_query', savedQueryEl.value);
            }
            if (searchEl && searchEl.value.trim()) {
                params.set('search', searchEl.value.trim());
            }
            if (schoolYearEl && schoolYearEl.value.trim()) {
                params.set('school_year', schoolYearEl.value.trim());
            }
            if (gradeEl && gradeEl.value.trim()) {
                params.set('grade', gradeEl.value.trim());
            }
            if (statusEl && statusEl.value !== null) {
                params.set('status', statusEl.value);
            }

            try {
                const firstResponse = await fetch(`${loadStudentsUrl}?${params.toString()}`);
                const firstPayload = await readJsonResponse(firstResponse, 'Students API (page 1)');

                if (!firstResponse.ok || !firstPayload.students) {
                    throw new Error('Failed to load dataset');
                }

                const allRows = Array.isArray(firstPayload.students) ? firstPayload.students.slice() : [];
                let mergedDefinitions = { ...(firstPayload.column_definitions || {}) };
                const totalPages = Math.max(1, Number(firstPayload.total_pages || 1));

                for (let pageNum = 2; pageNum <= totalPages; pageNum += 1) {
                    const pageParams = new URLSearchParams(params.toString());
                    pageParams.set('page', String(pageNum));
                    loadStatus.textContent = `Loading page ${pageNum} of ${totalPages}...`;

                    const pageResponse = await fetch(`${loadStudentsUrl}?${pageParams.toString()}`);
                    const pagePayload = await readJsonResponse(pageResponse, `Students API (page ${pageNum})`);
                    if (!pageResponse.ok || !Array.isArray(pagePayload.students)) {
                        throw new Error(`Failed to load page ${pageNum}`);
                    }

                    allRows.push(...pagePayload.students);
                    mergedDefinitions = { ...mergedDefinitions, ...(pagePayload.column_definitions || {}) };
                }

                state.rows = allRows;
                state.columns = firstPayload.selected_columns || (allRows[0] ? Object.keys(allRows[0]) : []);
                state.columnDefinitions = { ...state.columnDefinitions, ...mergedDefinitions };

                state.columnTypes = {};
                state.columns.forEach((column) => {
                    const values = state.rows.map((row) => row[column]);
                    state.columnTypes[column] = inferColumnType(values);
                });

                state.columns.forEach((column) => {
                    if (column.endsWith('_PL')) {
                        state.columnTypes[column] = 'string';
                    } else if (column.endsWith('_SS')) {
                        state.columnTypes[column] = 'number';
                    } else if (String(column).toLowerCase() === 'grade') {
                        state.columnTypes[column] = 'string';
                    }
                });

                state.filterState = { search: '', columns: {} };
                state.filteredRows = state.rows.slice();
                state.previewPage = 1;

                emptyState.classList.add('hidden');
                chartWrap.classList.remove('hidden');
                chartControlsWrap.classList.remove('opacity-40');

                [xColEl, yColEl, stackColEl, aggEl, renderBtn, chartTypeEl, chartSourceEl].forEach((el) => {
                    if (el) {
                        el.disabled = false;
                    }
                });

                buildRecommendations();
                syncControlsForChartType();
                buildFilterBar();
                renderPreview();
                renderChart();

                const selectedAssessmentCols = selectedColumns.filter((col) => col.startsWith('test_') || col.startsWith('mark_')).length;
                if (selectedAssessmentCols > 0) {
                    loadStatus.textContent = `Loaded ${firstPayload.total || state.rows.length} rows • ${selectedAssessmentCols} assessment/mark columns`;
                } else {
                    loadStatus.textContent = `Loaded ${firstPayload.total || state.rows.length} rows`;
                }
            } catch (_) {
                loadStatus.textContent = 'Could not load dataset';
            } finally {
                loadBtn.disabled = false;
            }
        }

        window.addEventListener('resize', () => {
            if (chartRenderer) {
                chartRenderer.resize();
            }
        });

        if (typeof ResizeObserver !== 'undefined' && chartEl) {
            new ResizeObserver(() => {
                if (chartRenderer) {
                    chartRenderer.resize();
                }
            }).observe(chartEl);
        }

        if (tabDataBtn) {
            tabDataBtn.addEventListener('click', () => switchTab('data'));
        }
        if (tabSummaryBtn) {
            tabSummaryBtn.addEventListener('click', () => switchTab('summary'));
        }

        if (groupFilterClearEl) {
            groupFilterClearEl.addEventListener('click', () => {
                state.groupState.groupKeys.forEach((k) => {
                    delete state.filterState.columns[k];
                });

                buildFilterBar();

                if (groupFilterBadgeEl) {
                    groupFilterBadgeEl.classList.add('hidden');
                    groupFilterBadgeEl.classList.remove('flex');
                }

                applyFilters();
            });
        }

        if (useDefaultBtn) {
            useDefaultBtn.addEventListener('click', applyDefaultColumns);
        }
        if (loadBtn) {
            loadBtn.addEventListener('click', loadDataset);
        }
        if (renderBtn) {
            renderBtn.addEventListener('click', renderChart);
        }

        if (previewPrevBtn) {
            previewPrevBtn.addEventListener('click', () => {
                if (state.previewPage > 1) {
                    state.previewPage -= 1;
                    renderPreview();
                }
            });
        }

        if (previewNextBtn) {
            previewNextBtn.addEventListener('click', () => {
                const totalRows = getActiveRows().length;
                const totalPages = Math.max(1, Math.ceil(totalRows / (state.previewPageSize || 100)));
                if (state.previewPage < totalPages) {
                    state.previewPage += 1;
                    renderPreview();
                }
            });
        }

        chartTypeEl.addEventListener('change', function () {
            syncControlsForChartType();
            updateChartTypeHelp();
            autoRenderChart();
        });

        if (chartSourceEl) {
            chartSourceEl.addEventListener('change', () => {
                state.chartSource = chartSourceEl.value || 'dataset';
                state.groupSeriesCols = [];
                state.chartXCols = [];
                if (aggEl) {
                    if (state.chartSource === 'group') {
                        aggEl.value = 'sum';
                    }
                }
                syncControlsForChartType();
                autoRenderChart();
            });
        }

        if (groupMetricModeEl) {
            state.groupReport.metricMode = groupMetricModeEl.value || 'all';
            groupMetricModeEl.addEventListener('change', () => {
                state.groupReport.metricMode = groupMetricModeEl.value || 'all';
                renderGroupSummary();
            });
        }

        if (groupViewModeEl) {
            state.groupReport.viewMode = groupViewModeEl.value || 'table';
            groupViewModeEl.addEventListener('change', () => {
                state.groupReport.viewMode = groupViewModeEl.value || 'table';
            });
        }

        if (groupApplyViewBtn) {
            groupApplyViewBtn.addEventListener('click', () => {
                applyGroupReportViewToChart();
            });
        }

        [xColEl, yColEl, stackColEl, aggEl].forEach((control) => {
            control.addEventListener('change', () => {
                if (control === xColEl) {
                    if (!state.chartXCols.length) {
                        state.chartXCols = [xColEl.value].filter(Boolean);
                    } else if (!state.chartXCols.includes(xColEl.value)) {
                        state.chartXCols[0] = xColEl.value;
                    }
                }
                autoRenderChart();
            });
        });

        let titleTimer;
        if (optTitleEl) {
            optTitleEl.addEventListener('input', () => {
                clearTimeout(titleTimer);
                titleTimer = setTimeout(() => {
                    styleState.title = optTitleEl.value.trim();
                    autoRenderChart();
                }, 300);
            });
        }
        if (optPaletteEl) {
            optPaletteEl.addEventListener('change', () => {
                styleState.palette = optPaletteEl.value;
                autoRenderChart();
            });
        }
        if (optBgEl) {
            optBgEl.addEventListener('change', () => {
                styleState.background = optBgEl.value;
                autoRenderChart();
            });
        }
        if (optLegendEl) {
            optLegendEl.addEventListener('change', () => {
                styleState.legendPos = optLegendEl.value;
                autoRenderChart();
            });
        }
        if (optLabelsEl) {
            optLabelsEl.addEventListener('change', () => {
                styleState.dataLabels = optLabelsEl.checked;
                autoRenderChart();
            });
        }
        if (optSmoothEl) {
            optSmoothEl.addEventListener('change', () => {
                styleState.smooth = optSmoothEl.checked;
                autoRenderChart();
            });
        }

        initTreeToggles();
        applyDefaultColumns();

        const axisPlaceholderHtml = '<option value="">— load dataset first —</option>';
        [xColEl, yColEl, stackColEl].forEach((el) => {
            el.innerHTML = axisPlaceholderHtml;
            el.disabled = true;
        });
        aggEl.disabled = true;
        renderBtn.disabled = true;
        chartTypeEl.disabled = true;
        if (chartSourceEl) {
            chartSourceEl.disabled = true;
            state.chartSource = 'dataset';
        }

        updateChartTypeHelp();

        if (!supportedCharts.includes(chartTypeEl.value)) {
            chartTypeEl.value = 'bar';
        }
    });
})();
