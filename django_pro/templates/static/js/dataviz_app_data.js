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
                        state.chartGroupByCols = [];
                        renderGroupByChips();
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
            // Build multi-dimensional x_cols: primary X + any group-level additions
            const primaryX = xColEl.value || '';
            const extraGroupCols = (state.chartGroupByCols || []).filter(Boolean);
            const xCols = primaryX
                ? [primaryX, ...extraGroupCols]
                : (extraGroupCols.length ? extraGroupCols : []);
            return {
                chart_type: chartTypeEl.value || 'bar',
                x_col: primaryX,
                x_cols: xCols,
                y_col: yColEl.value || '',
                y_cols: state.groupSeriesCols.slice(),
                stack_col: stackColEl.value || '',
                size_col: sizeColEl.value || '',
                agg: effectiveAgg,
                title: styleState.title || '',
                palette: styleState.palette || 'default',
                background: styleState.background || 'white',
                legend_pos: styleState.legendPos || 'bottom',
                data_labels: !!styleState.dataLabels,
                smooth: !!styleState.smooth,
                show_points: !!styleState.showPoints,
                bins: Number(styleState.bins) || 12,
                curve: styleState.curve || 'linear',
                point_size: Number(styleState.pointSize) || 6,
                opacity: Number(styleState.opacity) || 0.9,
                zoom_pan: !!styleState.zoomPan,
                line_width: Number(styleState.lineWidth) || 2,
                border_radius: Number(styleState.borderRadius) || 6,
                stacking_mode: styleState.stackMode === 'percent' ? 'percent' : 'normal',
                tooltip_shared: !!styleState.tooltipShared,
                animate: !!styleState.animate,
                inverted: !!styleState.inverted,
                polar: !!styleState.polar,
                source: 'dataset',
            };
        }

        function getChartRequiredColumns(chartConfig) {
            const cols = new Set();
            [chartConfig.x_col, chartConfig.y_col, chartConfig.stack_col, chartConfig.size_col].forEach((c) => {
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

            // also include any extra group-by cols
            (state.chartGroupByCols || []).forEach((c) => {
                if (c) cols.add(c);
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

        function setLoadProgress(pct, msg) {
            if (loadProgressWrap) loadProgressWrap.classList.remove('hidden');
            if (loadProgressBar)  loadProgressBar.style.width = pct + '%';
            if (loadStatus)       loadStatus.textContent = msg || '';
        }

        function resetLoadUI() {
            if (loadProgressWrap) loadProgressWrap.classList.add('hidden');
            if (loadProgressBar)  loadProgressBar.style.width = '0%';
            if (loadSummaryEl)    loadSummaryEl.classList.add('hidden');
            if (loadBtnIcon)      loadBtnIcon.textContent = '\u25B6';
            if (loadBtnText)      loadBtnText.textContent = 'Load Dataset';
        }

        async function loadDataset() {
            loadBtn.disabled = true;
            resetLoadUI();
            if (loadBtnIcon) loadBtnIcon.innerHTML = '<svg class="animate-spin" style="width:12px;height:12px;display:inline" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-dasharray="31.4" stroke-dashoffset="10"/></svg>';
            if (loadBtnText) loadBtnText.textContent = 'Loading...';
            setLoadProgress(10, 'Connecting...');

            const params = new URLSearchParams();
            params.set('format', 'json');
            params.set('limit', '1500');

            const hasSavedQuery = savedQueryEl && savedQueryEl.value;

            if (hasSavedQuery) {
                // Let the backend use the saved query's column list;
                // only override if the user also explicitly ticked columns.
                const uiCols = getSelectedColumns();
                const opt = savedQueryEl.options[savedQueryEl.selectedIndex];
                let sqCols = [];
                try { sqCols = (JSON.parse((opt && opt.dataset.config) || '{}')).columns || []; } catch (_) {}
                // Only send columns param when the user has diverged from the saved-query default
                const uiSet = JSON.stringify(uiCols.slice().sort());
                const sqSet = JSON.stringify(sqCols.slice().sort());
                if (uiCols.length && uiSet !== sqSet) {
                    params.set('columns', JSON.stringify(uiCols));
                }
                params.set('saved_query', savedQueryEl.value);
            } else {
                const selectedColumns = getSelectedColumns();
                if (selectedColumns.length) {
                    params.set('columns', JSON.stringify(selectedColumns));
                }
            }
            if (searchEl && searchEl.value.trim()) {
                params.set('search', searchEl.value.trim());
            }
            // Multi-select filters — send as JSON arrays
            const _syVals = getMultiSelectValues(schoolYearDd, 'dv-school-year-opt');
            if (_syVals.length) params.set('school_year', JSON.stringify(_syVals));
            const _grVals = getMultiSelectValues(gradeDd, 'dv-grade-opt');
            if (_grVals.length) params.set('grade', JSON.stringify(_grVals));
            const _stVals = getMultiSelectValues(statusDd, 'dv-status-opt');
            if (_stVals.length) {
                params.set('status', JSON.stringify(_stVals));
            } else {
                params.set('status', '');  // empty = all
            }

            try {
                setLoadProgress(20, 'Fetching page 1...');
                const firstResponse = await fetch(`${loadStudentsUrl}?${params.toString()}`);
                const firstPayload = await readJsonResponse(firstResponse, 'Students API (page 1)');

                if (!firstResponse.ok || !firstPayload.students) {
                    throw new Error('Failed to load dataset');
                }

                const allRows = Array.isArray(firstPayload.students) ? firstPayload.students.slice() : [];
                let mergedDefinitions = { ...(firstPayload.column_definitions || {}) };
                const totalPages = Math.max(1, Number(firstPayload.total_pages || 1));
                setLoadProgress(20 + Math.round(60 / totalPages), `Page 1 of ${totalPages} • ${allRows.length.toLocaleString()} rows`);

                // Preserve the complete result set, but fetch independent
                // pages concurrently. The old sequential loop made large
                // explorer loads pay one network round-trip per page.
                const pageNumbers = [];
                for (let pageNum = 2; pageNum <= totalPages; pageNum += 1) pageNumbers.push(pageNum);
                const pageBatchSize = 4;
                for (let start = 0; start < pageNumbers.length; start += pageBatchSize) {
                    const batch = pageNumbers.slice(start, start + pageBatchSize);
                    const payloads = await Promise.all(batch.map(async (pageNum) => {
                        const pageParams = new URLSearchParams(params.toString());
                        pageParams.set('page', String(pageNum));
                        const pageResponse = await fetch(`${loadStudentsUrl}?${pageParams.toString()}`);
                        const pagePayload = await readJsonResponse(pageResponse, `Students API (page ${pageNum})`);
                        if (!pageResponse.ok || !Array.isArray(pagePayload.students)) {
                            throw new Error(`Failed to load page ${pageNum}`);
                        }
                        return { pageNum, payload: pagePayload };
                    }));
                    payloads.sort((a, b) => a.pageNum - b.pageNum);
                    payloads.forEach(({ pageNum, payload }) => {
                        allRows.push(...payload.students);
                        mergedDefinitions = { ...mergedDefinitions, ...(payload.column_definitions || {}) };
                    });
                    const loadedPage = Math.min(totalPages, start + batch.length + 1);
                    const pct = 20 + Math.round((loadedPage / totalPages) * 60);
                    setLoadProgress(pct, `Page ${loadedPage} of ${totalPages} • ${allRows.length.toLocaleString()} rows loaded...`);
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

                // ── Inject synthetic meta-columns for assessment analysis ────────────────
                // When test/mark columns are present the backend can melt them into long
                // form. These synthetic column names trigger that melt and appear in all
                // dropdowns as selectable dimensions/values.
                //
                // For tests the label format is:  "Name Subject (YYYY-YYYY) SS[\nWindow]"
                // For marks:                       "Course (Subject) Period\nYYYY-YYYY"

                const _testSsCols = state.columns.filter(
                    (c) => /^test_\d{4}_\d{4}_/.test(c) && c.endsWith('_SS'),
                );
                const _markCols = state.columns.filter((c) => /^mark_\d{4}_\d{4}_/.test(c));

                if (_testSsCols.length > 0 || _markCols.length > 0) {
                    // Parse assessment display names from col_defs so the _assessment
                    // dropdown shows recognisable names (not raw keys).
                    const _yearRe = /\((\d{4}-\d{4})\)/;
                    const _assessmentNames = new Set();

                    _testSsCols.forEach((c) => {
                        const lbl = (state.columnDefinitions[c] || '').split('\n')[0];
                        const ym = lbl.match(_yearRe);
                        if (ym) {
                            let name = lbl.slice(0, ym.index).trim();
                            for (const sfx of [' SS', ' PL', ' Scale Score', ' Perf Lvl']) {
                                if (name.endsWith(sfx)) { name = name.slice(0, -sfx.length).trim(); break; }
                            }
                            if (name) _assessmentNames.add(name);
                        }
                    });
                    _markCols.forEach((c) => {
                        const lbl = (state.columnDefinitions[c] || '').split('\n')[0];
                        if (lbl.includes(' (')) {
                            const name = lbl.slice(0, lbl.lastIndexOf(' (')).trim();
                            if (name) _assessmentNames.add(name);
                        }
                    });

                    // Build a human-readable hint for the _assessment column
                    const _assessList = [..._assessmentNames].sort();
                    const _assessHint = _assessList.length
                        ? `Assessment (${_assessList.slice(0, 3).join(', ')}${_assessList.length > 3 ? '…' : ''})`
                        : 'Assessment';

                    [
                        ['_assessment',       'string',  _assessHint],
                        ['_test_school_year', 'string',  'Test School Year'],
                        ['_test_window',      'string',  'Test Window / Period'],
                        ['_test_score',       'number',  'Test Score (SS)'],
                        ['_mark_school_year', 'string',  'Mark School Year'],
                        ['_mark_period',      'string',  'Mark Period'],
                        ['_mark_score',       'number',  'Mark Value'],
                    ].forEach(([col, type, label]) => {
                        if (!state.columns.includes(col)) state.columns.push(col);
                        state.columnTypes[col] = type;
                        state.columnDefinitions[col] = label;
                    });
                }

                state.filterState = { search: '', columns: {} };
                state.filteredRows = state.rows.slice();
                state.previewPage = 1;
                state.sourceType = 'query';
                state.sourceSql  = '';

                if (emptyState)        emptyState.classList.add('hidden');
                if (chartWrap)         chartWrap.classList.remove('hidden');
                if (chartControlsWrap) chartControlsWrap.classList.remove('opacity-40');

                [xColEl, yColEl, stackColEl, aggEl, renderBtn, chartTypeEl, chartSourceEl].forEach((el) => {
                    if (el) {
                        el.disabled = false;
                    }
                });
                setLoadProgress(85, 'Building chart columns...');
                buildRecommendations();
                updateChartTypeOptions();
                syncControlsForChartType();
                buildFilterBar();
                renderPreview();
                updateBuildAssistant();
                state.chartGroupByCols = [];
                renderGroupByChips();
                buildDimMetricPanels();   // directly rebuild DIMENSIONS / METRICS chips
                renderChart();
                setLoadProgress(100, '');

                const totalRows = firstPayload.total || state.rows.length;
                const numCols   = state.columns.length;
                const assessCols = state.columns.filter(function (c) { return c.startsWith('test_') || c.startsWith('mark_') || c.startsWith('_test') || c.startsWith('_mark'); }).length;
                const summaryMsg = totalRows.toLocaleString() + ' rows • '
                    + numCols + ' columns'
                    + (assessCols > 0 ? ' • ' + assessCols + ' assess.' : '');

                if (loadStatus)      loadStatus.textContent = '';
                if (loadSummaryEl)   loadSummaryEl.classList.remove('hidden');
                if (loadSummaryText) loadSummaryText.textContent = '\u2713 ' + summaryMsg;
                if (loadBtnIcon)     loadBtnIcon.textContent = '\u2713';
                if (loadBtnText)     loadBtnText.textContent = 'Reload';

                // fade progress bar out after a moment
                setTimeout(function () {
                    if (loadProgressWrap) loadProgressWrap.classList.add('hidden');
                }, 800);

            } catch (err) {
                setLoadProgress(0, '');
                if (loadProgressWrap) loadProgressWrap.classList.add('hidden');
                if (loadStatus)   loadStatus.textContent = '\u26A0 ' + (err.message || 'Could not load dataset');
                if (loadBtnIcon)  loadBtnIcon.textContent = '\u9654';
                if (loadBtnText)  loadBtnText.textContent = 'Retry';
                if (loadSummaryEl) loadSummaryEl.classList.add('hidden');
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
