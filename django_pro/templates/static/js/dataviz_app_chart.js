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
            const allCols = [...pools.nonNumber, ...pools.number];

            let xPool = allCols;
            let yPool = allCols;   // all columns allowed as Y; string cols auto-force count agg
            let stackPool = allCols;
            let sizePool = pools.number;
            let showX = true;
            let showY = true;
            let showStack = true;
            let showSize = false;

            if (chartType === 'scatter') {
                xPool = pools.number;
                yPool = pools.number;
                stackPool = pools.nonNumber;
                showSize = false;
            } else if (chartType === 'jitter') {
                xPool = allCols;
                yPool = pools.number;
                stackPool = pools.nonNumber;
                showSize = false;
                showStack = true;
            } else if (chartType === 'bubble') {
                xPool = pools.number;
                yPool = pools.number;
                stackPool = pools.nonNumber;
                sizePool = pools.number;
                showSize = true;
            } else if (chartType === 'heatmap') {
                xPool = pools.nonNumber.length ? pools.nonNumber : allCols;
                yPool = pools.nonNumber.length ? pools.nonNumber : allCols;
                stackPool = [];
                showStack = false;
            } else if (chartType === 'pie' || chartType === 'donut' || chartType === 'funnel' || chartType === 'pyramid' || chartType === 'treemap' || chartType === 'radar') {
                xPool = allCols;
                yPool = allCols;   // string Y → count automatically
                stackPool = [];
                showStack = false;
            } else if (chartType === 'histogram') {
                xPool = allCols;
                yPool = [];
                showY = false;
                stackPool = allCols;
            } else if (chartType === 'box') {
                xPool = allCols;
                yPool = pools.number;
                stackPool = [];
                showStack = false;
            } else if (chartType === 'gauge' || chartType === 'solid_gauge') {
                xPool = [];
                yPool = pools.number;
                stackPool = [];
                showX = false;
                showStack = false;
            } else if (chartType === 'waterfall') {
                xPool = allCols;
                yPool = allCols;
                stackPool = [];
                showStack = false;
            } else if (chartType === 'polar') {
                xPool = allCols;
                yPool = allCols;
                stackPool = allCols;
            }

            const xCurrent = xColEl.value;
            const yCurrent = yColEl.value;
            const sCurrent = stackColEl.value;
            const zCurrent = sizeColEl.value;

            fillSelect(xColEl, xPool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'Select...');
            fillSelect(yColEl, yPool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'Select...');
            fillSelect(stackColEl, stackPool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'None');
            fillSelect(sizeColEl, sizePool.map((c) => ({ value: c, label: chartDataset.columnDefinitions[c] || c })), 'Auto');

            if (xPool.includes(xCurrent)) {
                xColEl.value = xCurrent;
            }
            if (yPool.includes(yCurrent)) {
                yColEl.value = yCurrent;
            }
            if (stackPool.includes(sCurrent)) {
                stackColEl.value = sCurrent;
            }
            if (sizePool.includes(zCurrent)) {
                sizeColEl.value = zCurrent;
            }

            if (!xColEl.value && xPool.length) {
                xColEl.value = xPool[0];
            }
            if (!yColEl.value && yPool.length) {
                yColEl.value = yPool[0];
            }

            const aggByChart = {
                bar: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                grouped_bar: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                stacked_bar: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                line: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                area: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                stacked_area: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                pie: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                donut: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                treemap: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                funnel: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                radar: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                heatmap: ['count'],
                gauge: ['count', 'sum', 'avg', 'min', 'max', 'median'],
                histogram: ['count'],
                box: ['count'],
                scatter: ['count'],
                jitter: ['count'],
                bubble: ['count'],
            };
            const numericAggs = new Set(['sum', 'avg', 'min', 'max', 'median']);
            const selectedAgg = aggEl.value || 'count';
            const chartAggs = aggByChart[chartType] || ['count'];

            // Only allow numeric aggs when the currently-selected Y column is actually numeric.
            // If no Y is selected (or Y is a string col), restrict to count.
            const ySelected = yColEl.value || '';
            const yIsNumeric = !!ySelected && (chartDataset.columnTypes[ySelected] || 'string') === 'number';
            const allowedAggs = chartAggs.filter((agg) => agg === 'count' || yIsNumeric);
            fillSelect(
                aggEl,
                aggOptions
                    .filter((opt) => allowedAggs.includes(opt.value))
                    .map((opt) => ({ value: opt.value, label: opt.label })),
                'Count',
            );
            if (allowedAggs.includes(selectedAgg)) {
                aggEl.value = selectedAgg;
            } else if (allowedAggs.includes('count')) {
                aggEl.value = 'count';
            } else if (allowedAggs.length) {
                aggEl.value = allowedAggs[0];
            }

            if (numericAggs.has(aggEl.value) && !yIsNumeric) {
                aggEl.value = 'count';
            }

            setControlVisible(xWrap, showX);
            setControlVisible(yWrap, showY);
            setControlVisible(stackWrap, showStack);
            setControlVisible(sizeWrap, showSize);

            xColEl.disabled = !showX;
            yColEl.disabled = !showY;
            stackColEl.disabled = !showStack;
            sizeColEl.disabled = !showSize;
            aggEl.disabled = false;

            const supports = {
                bins: chartType === 'histogram',
                curve: ['line', 'area', 'stacked_area', 'radar'].includes(chartType),
                markers: ['line', 'area', 'stacked_area', 'scatter', 'jitter', 'bubble', 'radar'].includes(chartType),
                pointSize: ['line', 'area', 'stacked_area', 'scatter', 'jitter', 'bubble', 'radar'].includes(chartType),
                lineWidth: ['line', 'area', 'stacked_area', 'scatter', 'jitter', 'bubble', 'radar'].includes(chartType),
                radius: ['bar', 'grouped_bar', 'stacked_bar', 'pie', 'donut', 'funnel', 'treemap'].includes(chartType),
                stackMode: ['stacked_bar', 'stacked_area'].includes(chartType),
                tooltipShared: !['pie', 'donut', 'treemap', 'funnel', 'gauge'].includes(chartType),
                polar: ['line', 'area', 'stacked_area', 'bar', 'grouped_bar', 'stacked_bar', 'scatter', 'jitter', 'radar'].includes(chartType),
                inverted: !['pie', 'donut', 'treemap', 'funnel', 'gauge', 'radar'].includes(chartType),
                zoomPan: !['gauge', 'pie', 'donut', 'funnel'].includes(chartType),
            };

            function setStyleEnabled(controlEl, enabled, fallbackValue) {
                if (!controlEl) {
                    return;
                }
                controlEl.disabled = !enabled;
                const wrap = controlEl.closest('div, label');
                if (wrap) {
                    wrap.classList.toggle('opacity-50', !enabled);
                }
                if (!enabled && fallbackValue !== undefined) {
                    if (controlEl.type === 'checkbox') {
                        controlEl.checked = !!fallbackValue;
                    } else {
                        controlEl.value = String(fallbackValue);
                    }
                }
            }

            setStyleEnabled(optBinsEl, supports.bins, 12);
            if (!supports.bins) {
                styleState.bins = 12;
            }

            setStyleEnabled(optCurveEl, supports.curve, 'linear');
            if (!supports.curve) {
                styleState.curve = 'linear';
            }

            setStyleEnabled(optMarkersEl, supports.markers, false);
            if (!supports.markers) {
                styleState.showPoints = false;
            }

            setStyleEnabled(optPointSizeEl, supports.pointSize, 6);
            if (!supports.pointSize) {
                styleState.pointSize = 6;
            }

            setStyleEnabled(optLineWidthEl, supports.lineWidth, 2);
            if (!supports.lineWidth) {
                styleState.lineWidth = 2;
            }

            setStyleEnabled(optRadiusEl, supports.radius, 6);
            if (!supports.radius) {
                styleState.borderRadius = 6;
            }

            setStyleEnabled(optStackModeEl, supports.stackMode, 'normal');
            if (!supports.stackMode) {
                styleState.stackMode = 'normal';
            }

            setStyleEnabled(optTooltipSharedEl, supports.tooltipShared, true);
            if (!supports.tooltipShared) {
                styleState.tooltipShared = true;
            }

            setStyleEnabled(optPolarEl, supports.polar, false);
            if (!supports.polar) {
                styleState.polar = false;
            }

            setStyleEnabled(optInvertedEl, supports.inverted, false);
            if (!supports.inverted) {
                styleState.inverted = false;
            }

            setStyleEnabled(optZoomEl, supports.zoomPan, true);
            if (!supports.zoomPan) {
                styleState.zoomPan = true;
            }
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

