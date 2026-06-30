        // ── Multi-select dropdown helpers ────────────────────────────────

        /** Read checked values from a multi-select dropdown container */
        function getMultiSelectValues(ddEl, optClass) {
            if (!ddEl) return [];
            return Array.from(ddEl.querySelectorAll('.' + optClass + ':checked'))
                        .map(function (cb) { return cb.value; });
        }

        /** Set checked values on a multi-select dropdown + update button label */
        function setMultiSelectValues(btnEl, ddEl, optClass, values, allLabel) {
            if (!ddEl) return;
            var valSet = new Set(Array.isArray(values) ? values : [values]);
            ddEl.querySelectorAll('.' + optClass).forEach(function (cb) {
                cb.checked = valSet.has(cb.value);
            });
            _syncMsLabel(btnEl, ddEl, optClass, allLabel || 'All');
        }

        /** Sync the button label text from current checkbox state */
        function _syncMsLabel(btnEl, ddEl, optClass, allLabel) {
            if (!btnEl) return;
            var lblSpan = btnEl.querySelector('.dv-ms-label');
            if (!lblSpan) return;
            var checked = Array.from(ddEl.querySelectorAll('.' + optClass + ':checked'));
            if (checked.length === 0) {
                lblSpan.textContent = allLabel || 'All';
                lblSpan.classList.add('text-slate-400');
                lblSpan.classList.remove('text-slate-700');
            } else {
                lblSpan.textContent = checked.map(function (c) { return c.value; }).join(', ');
                lblSpan.classList.remove('text-slate-400');
                lblSpan.classList.add('text-slate-700');
            }
        }

        /** Wire up a multi-select dropdown button + checkbox list */
        function initMultiSelect(btnEl, ddEl, optClass, allLabel) {
            if (!btnEl || !ddEl) return;
            // Toggle dropdown
            btnEl.addEventListener('click', function (e) {
                e.stopPropagation();
                // Close other open multi-selects first
                document.querySelectorAll('.dv-ms-dropdown').forEach(function (d) {
                    if (d !== ddEl) d.classList.add('hidden');
                });
                ddEl.classList.toggle('hidden');
            });
            // Update label on checkbox change
            ddEl.addEventListener('change', function () {
                _syncMsLabel(btnEl, ddEl, optClass, allLabel);
            });
            // Close on outside click
            document.addEventListener('click', function (e) {
                if (!btnEl.contains(e.target) && !ddEl.contains(e.target)) {
                    ddEl.classList.add('hidden');
                }
            });
            // Initial sync
            _syncMsLabel(btnEl, ddEl, optClass, allLabel);
        }

        function updateChartTypeHelp() {
            if (!chartTypeHelpEl || !chartTypeEl) {
                return;
            }
            const helps = {
                bar: 'Bar: compare values across categories.',
                grouped_bar: 'Grouped Bar: compare multiple series side-by-side across categories.',
                stacked_bar: 'Stacked Bar: compare totals and composition by category.',
                line: 'Line: track trends across ordered categories.',
                area: 'Area: trend with emphasis on cumulative magnitude.',
                stacked_area: 'Stacked Area: cumulative contribution over categories.',
                pie: 'Pie: show part-to-whole distribution.',
                donut: 'Donut: pie chart with center space for cleaner labels.',
                treemap: 'Treemap: area encodes part-to-whole values by category.',
                funnel: 'Funnel: stage values from largest to smallest.',
                scatter: 'Scatter: examine relationship between two numeric variables.',
                jitter: 'Jitter: show individual data points spread within categories to reveal distribution.',
                bubble: 'Bubble: scatter plot with a third numeric variable as bubble size.',
                heatmap: 'Heatmap: compare intensity across a category matrix.',
                histogram: 'Histogram: inspect value distribution by bins.',
                box: 'Box plot: compare spread and quartiles by group.',
                radar: 'Radar: compare category metrics in radial form.',
                gauge: 'Gauge: display one aggregate KPI against a range.',
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

        const chartCatalog = [
            { value: 'bar', label: 'Bar', group: 'Bars', minAny: 1 },
            { value: 'column', label: 'Column', group: 'Bars', minAny: 1 },
            { value: 'grouped_bar', label: 'Grouped Bar', group: 'Bars', minAny: 1 },
            { value: 'stacked_bar', label: 'Stacked Bar', group: 'Bars', minAny: 1 },
            { value: 'waterfall', label: 'Waterfall', group: 'Bars', minAny: 1 },
            { value: 'line', label: 'Line', group: 'Lines & Areas', minAny: 1 },
            { value: 'spline', label: 'Spline', group: 'Lines & Areas', minAny: 1 },
            { value: 'area', label: 'Area', group: 'Lines & Areas', minAny: 1 },
            { value: 'areaspline', label: 'Area Spline', group: 'Lines & Areas', minAny: 1 },
            { value: 'stacked_area', label: 'Stacked Area', group: 'Lines & Areas', minAny: 1 },
            { value: 'scatter', label: 'Scatter', group: 'Distribution', minNumeric: 2 },
            { value: 'jitter', label: 'Jitter', group: 'Distribution', minAny: 1, minNumeric: 1 },
            { value: 'bubble', label: 'Bubble', group: 'Distribution', minNumeric: 3 },
            { value: 'histogram', label: 'Histogram', group: 'Distribution', minAny: 1 },
            { value: 'box', label: 'Box Plot', group: 'Distribution', minNumeric: 1 },
            { value: 'heatmap', label: 'Heatmap', group: 'Distribution', minAny: 2 },
            { value: 'pie', label: 'Pie', group: 'Part-of-Whole', minAny: 1 },
            { value: 'donut', label: 'Donut', group: 'Part-of-Whole', minAny: 1 },
            { value: 'treemap', label: 'Treemap', group: 'Part-of-Whole', minAny: 1 },
            { value: 'funnel', label: 'Funnel', group: 'Part-of-Whole', minAny: 1 },
            { value: 'pyramid', label: 'Pyramid', group: 'Part-of-Whole', minAny: 1 },
            { value: 'radar', label: 'Radar', group: 'Specialized', minAny: 1 },
            { value: 'gauge', label: 'Gauge', group: 'Specialized', minNumeric: 1 },
            { value: 'solid_gauge', label: 'Solid Gauge', group: 'Specialized', minNumeric: 1 },
            { value: 'polar', label: 'Polar', group: 'Specialized', minAny: 1 },
        ];
        const aggOptions = [
            { value: 'count', label: 'Count' },
            { value: 'sum', label: 'Sum' },
            { value: 'avg', label: 'Average' },
            { value: 'min', label: 'Minimum' },
            { value: 'max', label: 'Maximum' },
            { value: 'median', label: 'Median' },
        ];
        const supportedCharts = chartCatalog.map((c) => c.value);

        function getColumnCounts() {
            const chartDataset = getChartSourceDataset();
            const pools = getColumnPools(chartDataset.columnTypes, chartDataset.columns);
            return { numeric: pools.number.length, category: pools.nonNumber.length, any: chartDataset.columns.length };
        }

        function updateChartTypeOptions() {
            if (!chartTypeEl) {
                return;
            }

            const counts = getColumnCounts();
            const selectedValue = chartTypeEl.value;
            const grouped = {};
            const noData = counts.any === 0;

            chartCatalog.forEach((item) => {
                if (!grouped[item.group]) {
                    grouped[item.group] = [];
                }
                const okCategory = noData || !item.minCategory || counts.category >= item.minCategory;
                const okNumeric = noData || !item.minNumeric || counts.numeric >= item.minNumeric;
                const okAny = noData || !item.minAny || counts.any >= item.minAny;
                grouped[item.group].push({ ...item, available: !!(okCategory && okNumeric && okAny) });
            });

            chartTypeEl.innerHTML = '';
            Object.keys(grouped).forEach((groupName) => {
                const groupEl = document.createElement('optgroup');
                groupEl.label = groupName;
                grouped[groupName].forEach((item) => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.available ? item.label : `${item.label} (requires matching data)`;
                    opt.disabled = !item.available;
                    groupEl.appendChild(opt);
                });
                chartTypeEl.appendChild(groupEl);
            });

            const available = Array.from(chartTypeEl.querySelectorAll('option:not(:disabled)')).map((o) => o.value);
            if (available.includes(selectedValue)) {
                chartTypeEl.value = selectedValue;
            } else if (available.length) {
                chartTypeEl.value = available[0];
            }
        }

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

        function getOrderedColumnValues(col) {
            const distinct = new Set();
            for (const row of state.rows) {
                const raw = row[col];
                if (!isBlankValue(raw)) {
                    distinct.add(String(raw));
                }
            }
            const values = Array.from(distinct);
            const numeric = values.every((v) => !Number.isNaN(Number(v)));
            if (numeric) {
                values.sort((a, b) => Number(a) - Number(b));
            } else {
                values.sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
            }
            return values;
        }


        function updateBuildAssistant() {
            if (!datasetSummaryEl || !chartCompatEl || !chartTypeEl) {
                return;
            }

            const rows = getActiveRows();
            const numericCount = state.columns.filter((c) => (state.columnTypes[c] || 'string') === 'number').length;
            const categoryCount = state.columns.length - numericCount;
            datasetSummaryEl.textContent = `${rows.length.toLocaleString()} rows ready • ${state.columns.length} columns (${numericCount} numeric, ${categoryCount} categorical)`;

            const availableLabels = Array.from(chartTypeEl.querySelectorAll('option:not(:disabled)')).map((opt) => opt.textContent.replace(' (requires matching data)', ''));
            const preview = availableLabels.slice(0, 8).join(', ');
            chartCompatEl.textContent = availableLabels.length
                ? `Compatible charts: ${preview}${availableLabels.length > 8 ? '…' : ''}`
                : 'No compatible chart types with current dataset.';

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
            updateBuildAssistant();
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
            // Inline filters from the chart filter-builder
            const inlineF = (typeof _inlineFilters !== 'undefined') ? _inlineFilters : [];

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

                // ── Apply inline chart filters ({col, op, values[]}) ──
                for (var fi = 0; fi < inlineF.length; fi++) {
                    var f = inlineF[fi];
                    var rv = row[f.col];
                    var rvStr = (rv == null || rv === '') ? '(blank)' : String(rv);
                    var rvLower = rvStr.toLowerCase();
                    var fVals = f.values || [];
                    // Also support legacy single-string val field
                    if (!fVals.length && f.val) fVals = f.val.split(',').map(function(s){ return s.trim(); });
                    var matched = false;
                    switch (f.op) {
                        case 'eq':
                            matched = fVals.some(function(v){ return v === rvStr || v.toLowerCase() === rvLower; });
                            break;
                        case 'ne':
                            matched = fVals.every(function(v){ return v !== rvStr && v.toLowerCase() !== rvLower; });
                            break;
                        case 'contains':
                            matched = fVals.some(function(v){ return rvLower.includes(v.toLowerCase()); });
                            break;
                        case 'gt': {
                            var num = parseFloat(rv);
                            matched = fVals.some(function(v){ var t = parseFloat(v); return !isNaN(num) && !isNaN(t) && num > t; });
                            break;
                        }
                        case 'lt': {
                            var num2 = parseFloat(rv);
                            matched = fVals.some(function(v){ var t = parseFloat(v); return !isNaN(num2) && !isNaN(t) && num2 < t; });
                            break;
                        }
                        default:
                            matched = true;
                    }
                    if (!matched) return false;
                }

                return true;
            });

            updateFilterStatus();
            state.previewPage = 1;
            renderPreview();
            updateBuildAssistant();
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

