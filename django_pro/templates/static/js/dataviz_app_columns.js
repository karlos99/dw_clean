        // ── Column count badge ─────────────────────────────────────────────────
        const colCountBadgeEl = document.getElementById('dv-col-count-badge');
        const loadProgressWrap = document.getElementById('dv-load-progress-wrap');
        const loadProgressBar  = document.getElementById('dv-load-progress-bar');
        const loadSummaryEl    = document.getElementById('dv-load-summary');
        const loadSummaryText  = document.getElementById('dv-load-summary-text');
        const loadBtnIcon      = document.getElementById('dv-load-btn-icon');
        const loadBtnText      = document.getElementById('dv-load-btn-text');

        function updateColCount() {
            const checked = document.querySelectorAll('.dv-col:checked, input[data-column]:checked').length;
            if (colCountBadgeEl) {
                colCountBadgeEl.textContent = checked;
                colCountBadgeEl.className = checked > 0
                    ? 'ml-1 px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 text-[9px] font-black'
                    : 'ml-1 px-1.5 py-0.5 rounded-full bg-slate-200 text-slate-400 text-[9px] font-black';
            }
        }

        // Wire all existing checkboxes and watch for DOM additions (assessment tree)
        function wireColCheckboxes() {
            document.querySelectorAll('.dv-col, input[data-column]').forEach(function (inp) {
                if (!inp._colCountWired) {
                    inp._colCountWired = true;
                    inp.addEventListener('change', updateColCount);
                }
            });
            updateColCount();
        }
        wireColCheckboxes();
        // Re-wire after accordion opens (lazy-rendered assessment checkboxes)
        const colAccordion = document.getElementById('dv-col-accordion');
        if (colAccordion) {
            colAccordion.addEventListener('toggle', wireColCheckboxes);
        }

        // Per-category "all" toggle
        document.querySelectorAll('.dv-cat-toggle').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const cat = btn.dataset.category;
                const boxes = document.querySelectorAll('.dv-col[data-cat="' + cat + '"]');
                const allChecked = Array.from(boxes).every(function (b) { return b.checked; });
                boxes.forEach(function (b) { b.checked = !allChecked; });
                btn.textContent = allChecked ? 'all' : 'none';
                updateColCount();
            });
        });

        // Clear all
        const colClearAllBtn = document.getElementById('dv-col-clear-all');
        if (colClearAllBtn) {
            colClearAllBtn.addEventListener('click', function () {
                document.querySelectorAll('.dv-col, input[data-column]').forEach(function (inp) { inp.checked = false; });
                updateColCount();
            });
        }

        // "Core Columns" = apply defaults
        const colSelectAllBtn = document.getElementById('dv-col-select-all');
        if (colSelectAllBtn) {
            colSelectAllBtn.addEventListener('click', function () {
                applyDefaultColumns();
                updateColCount();
            });
        }

        if (useDefaultBtn) {
            useDefaultBtn.addEventListener('click', function () {
                applyDefaultColumns();
                updateColCount();
            });
        }

        // ── CUSTOM COLUMN GROUPS ────────────────────────────────────────────────
        let _customColGroups = [];

        const addColGroupBtnEl   = document.getElementById('dv-add-col-group-btn');
        const addColGroupModalEl = document.getElementById('dv-add-col-group-modal');
        const colGroupNameEl     = document.getElementById('dv-col-group-name');
        const colGroupColListEl  = document.getElementById('dv-col-group-col-list');
        const colGroupManualEl   = document.getElementById('dv-col-group-manual');
        const colGroupErrorEl    = document.getElementById('dv-col-group-error');
        const colGroupCancelEl   = document.getElementById('dv-col-group-cancel');
        const colGroupConfirmEl  = document.getElementById('dv-col-group-confirm');
        const customColGroupsEl  = document.getElementById('dv-custom-col-groups');

        function openAddColGroupModal() {
            if (!addColGroupModalEl) return;
            if (colGroupNameEl) colGroupNameEl.value = '';
            if (colGroupManualEl) colGroupManualEl.value = '';
            if (colGroupErrorEl) {
                colGroupErrorEl.textContent = '';
                colGroupErrorEl.classList.add('hidden');
            }
            if (colGroupColListEl) {
                if (!state.columns.length) {
                    colGroupColListEl.innerHTML = '<p class="text-[11px] text-slate-400 italic">Load a dataset first to see available columns here.</p>';
                } else {
                    colGroupColListEl.innerHTML = '';
                    state.columns.forEach(function (col) {
                        var label = colDisplayLabel ? colDisplayLabel(col) : col;
                        var lbl = document.createElement('label');
                        lbl.className = 'flex items-center gap-1.5 px-1 py-0.5 rounded hover:bg-blue-50 cursor-pointer';
                        lbl.innerHTML = '<input type="checkbox" class="cg-pick rounded border-slate-300 text-blue-600 w-3 h-3 shrink-0" value="' + escapeHtml(col) + '">'
                            + '<span class="text-[11px] text-slate-700 leading-tight">' + escapeHtml(label) + '</span>';
                        colGroupColListEl.appendChild(lbl);
                    });
                }
            }
            addColGroupModalEl.classList.add('open');
        }

        function closeAddColGroupModal() {
            if (addColGroupModalEl) addColGroupModalEl.classList.remove('open');
        }

        function renderCustomGroupInAccordion(group) {
            if (!customColGroupsEl) return;
            var catKey = 'custom_' + group.id;
            var details = document.createElement('details');
            details.className = 'rounded border border-slate-100';
            details.dataset.customGroupId = group.id;
            details.setAttribute('open', '');

            var header = document.createElement('summary');
            header.className = 'flex items-center justify-between px-2 py-1 bg-slate-50 rounded-t cursor-pointer list-none';
            header.innerHTML = '<span class="text-[9px] font-black uppercase tracking-widest text-slate-500">' + escapeHtml(group.name) + '</span>'
                + '<div class="flex items-center gap-1">'
                + '<button type="button" class="cg-all-btn text-[9px] text-blue-500 hover:text-blue-800">all</button>'
                + '<button type="button" class="cg-del-btn text-[9px] text-red-400 hover:text-red-600 ml-1" title="Remove group">\u2715</button>'
                + '</div>';

            var body = document.createElement('div');
            body.className = 'px-1 py-1 space-y-0';
            group.columns.forEach(function (col) {
                var label = colDisplayLabel ? colDisplayLabel(col) : col;
                var lbl = document.createElement('label');
                lbl.className = 'flex items-start gap-1.5 px-1 py-0.5 rounded hover:bg-blue-50 cursor-pointer';
                lbl.innerHTML = '<input type="checkbox" class="dv-col rounded border-slate-300 text-blue-600 w-3 h-3 mt-0.5 shrink-0" value="' + escapeHtml(col) + '" data-cat="' + escapeHtml(catKey) + '" data-column="' + escapeHtml(col) + '">'
                    + '<span class="text-[11px] text-slate-700 leading-tight">' + escapeHtml(label) + '</span>';
                lbl.querySelector('input').addEventListener('change', updateColCount);
                body.appendChild(lbl);
            });

            // All/None toggle
            header.querySelector('.cg-all-btn').addEventListener('click', function (e) {
                e.preventDefault();
                var boxes = body.querySelectorAll('.dv-col');
                var allChecked = Array.from(boxes).every(function (b) { return b.checked; });
                boxes.forEach(function (b) { b.checked = !allChecked; });
                updateColCount();
            });

            // Delete group
            header.querySelector('.cg-del-btn').addEventListener('click', function (e) {
                e.preventDefault();
                _customColGroups = _customColGroups.filter(function (g) { return g.id !== group.id; });
                details.remove();
                updateColCount();
            });

            details.appendChild(header);
            details.appendChild(body);
            customColGroupsEl.appendChild(details);
        }

        if (addColGroupBtnEl) {
            addColGroupBtnEl.addEventListener('click', openAddColGroupModal);
        }
        if (colGroupCancelEl) {
            colGroupCancelEl.addEventListener('click', closeAddColGroupModal);
        }
        if (colGroupConfirmEl) {
            colGroupConfirmEl.addEventListener('click', function () {
                var name = colGroupNameEl ? colGroupNameEl.value.trim() : '';
                if (!name) {
                    if (colGroupErrorEl) {
                        colGroupErrorEl.textContent = 'Group name is required.';
                        colGroupErrorEl.classList.remove('hidden');
                    }
                    return;
                }
                var pickedCols = colGroupColListEl
                    ? Array.from(colGroupColListEl.querySelectorAll('.cg-pick:checked')).map(function (cb) { return cb.value; })
                    : [];
                var manualCols = colGroupManualEl && colGroupManualEl.value.trim()
                    ? colGroupManualEl.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean)
                    : [];
                var allCols = Array.from(new Set(pickedCols.concat(manualCols)));
                if (!allCols.length) {
                    if (colGroupErrorEl) {
                        colGroupErrorEl.textContent = 'Select at least one column.';
                        colGroupErrorEl.classList.remove('hidden');
                    }
                    return;
                }
                var group = { id: Date.now(), name: name, columns: allCols };
                _customColGroups.push(group);
                renderCustomGroupInAccordion(group);
                updateColCount();
                closeAddColGroupModal();
            });
        }
        // ── END CUSTOM COLUMN GROUPS ────────────────────────────────────────────

        // ── Saved-query auto-populate ──────────────────────────────────────────
        if (savedQueryEl) {
            savedQueryEl.addEventListener('change', function () {
                const opt = savedQueryEl.options[savedQueryEl.selectedIndex];
                const badge = document.getElementById('dv-query-badge');

                if (!opt || !opt.value) {
                    if (badge) { badge.textContent = ''; badge.classList.add('hidden'); }
                    return;
                }

                let cfg = {};
                try { cfg = JSON.parse(opt.dataset.config || '{}'); } catch (_) {}

                const qType = opt.dataset.queryType || 'filter';
                const filters = cfg.filters || {};

                // Pre-fill standard filter inputs
                if (searchEl)     searchEl.value     = filters.search     || cfg.search     || '';
                setMultiSelectValues(schoolYearBtn, schoolYearDd, 'dv-school-year-opt',
                    filters.school_year || cfg.school_year || [], 'All');
                setMultiSelectValues(gradeBtn, gradeDd, 'dv-grade-opt',
                    filters.grade || cfg.grade || [], 'All');
                setMultiSelectValues(statusBtn, statusDd, 'dv-status-opt',
                    filters.status || cfg.status || [], 'All');

                // Pre-check column checkboxes from saved query columns list
                const savedCols = cfg.columns || [];
                if (savedCols.length) {
                    const colSet = new Set(savedCols);
                    document.querySelectorAll('.dv-col, input[data-column]').forEach(function (inp) {
                        const key = inp.dataset.column || inp.value;
                        inp.checked = colSet.has(key);
                    });
                    wireColCheckboxes();
                    updateColCount();
                }

                // Badge
                if (badge) {
                    if (qType === 'student_list') {
                        const count = (cfg.student_ids || []).length;
                        badge.textContent = '\u2713 Student list (' + count + ' students)';
                    } else {
                        const colCount = (cfg.columns || []).length;
                        badge.textContent = '\u2713 Query loaded' + (colCount ? ' \u2022 ' + colCount + ' columns' : '');
                    }
                    badge.classList.remove('hidden');
                }
            });
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
            updateBuildAssistant();
            autoRenderChart();
        });

        // Re-sync agg options whenever the Y column changes so numeric-only
        // aggs (sum/avg/min/max/median) are shown or hidden based on the
        // actual selected column type.
        yColEl.addEventListener('change', function () {
            syncControlsForChartType();
            autoRenderChart();
        });

        if (chartSourceEl) {
            chartSourceEl.addEventListener('change', () => {
                state.chartSource = chartSourceEl.value || 'dataset';
                state.groupSeriesCols = [];
                state.chartXCols = [];
                state.chartGroupByCols = [];
                renderGroupByChips();
                if (aggEl) {
                    if (state.chartSource === 'group') {
                        aggEl.value = 'sum';
                    }
                }
                updateChartTypeOptions();
                syncControlsForChartType();
                updateBuildAssistant();
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

        [xColEl, yColEl, stackColEl, sizeColEl, aggEl].forEach((control) => {
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
        if (optMarkersEl) {
            optMarkersEl.addEventListener('change', () => {
                styleState.showPoints = optMarkersEl.checked;
                autoRenderChart();
            });
        }
        if (optBinsEl) {
            styleState.bins = Number(optBinsEl.value) || 12;
            optBinsEl.addEventListener('change', () => {
                styleState.bins = Number(optBinsEl.value) || 12;
                autoRenderChart();
            });
        }
        if (optCurveEl) {
            styleState.curve = optCurveEl.value || 'linear';
            optCurveEl.addEventListener('change', () => {
                styleState.curve = optCurveEl.value || 'linear';
                autoRenderChart();
            });
        }
        if (optPointSizeEl) {
            styleState.pointSize = Number(optPointSizeEl.value) || 6;
            optPointSizeEl.addEventListener('change', () => {
                styleState.pointSize = Number(optPointSizeEl.value) || 6;
                autoRenderChart();
            });
        }
        if (optOpacityEl) {
            styleState.opacity = Number(optOpacityEl.value) || 0.9;
            optOpacityEl.addEventListener('input', () => {
                styleState.opacity = Number(optOpacityEl.value) || 0.9;
                autoRenderChart();
            });
        }
        if (optZoomEl) {
            styleState.zoomPan = !!optZoomEl.checked;
            optZoomEl.addEventListener('change', () => {
                styleState.zoomPan = !!optZoomEl.checked;
                autoRenderChart();
            });
        }
        if (optLineWidthEl) {
            styleState.lineWidth = Number(optLineWidthEl.value) || 2;
            optLineWidthEl.addEventListener('change', () => {
                styleState.lineWidth = Number(optLineWidthEl.value) || 2;
                autoRenderChart();
            });
        }
        if (optRadiusEl) {
            styleState.borderRadius = Number(optRadiusEl.value) || 6;
            optRadiusEl.addEventListener('change', () => {
                styleState.borderRadius = Number(optRadiusEl.value) || 6;
                autoRenderChart();
            });
        }
        if (optStackModeEl) {
            styleState.stackMode = optStackModeEl.value || 'normal';
            optStackModeEl.addEventListener('change', () => {
                styleState.stackMode = optStackModeEl.value || 'normal';
                autoRenderChart();
            });
        }
        if (optTooltipSharedEl) {
            styleState.tooltipShared = !!optTooltipSharedEl.checked;
            optTooltipSharedEl.addEventListener('change', () => {
                styleState.tooltipShared = !!optTooltipSharedEl.checked;
                autoRenderChart();
            });
        }
        if (optAnimateEl) {
            styleState.animate = !!optAnimateEl.checked;
            optAnimateEl.addEventListener('change', () => {
                styleState.animate = !!optAnimateEl.checked;
                autoRenderChart();
            });
        }
        if (optInvertedEl) {
            styleState.inverted = !!optInvertedEl.checked;
            optInvertedEl.addEventListener('change', () => {
                styleState.inverted = !!optInvertedEl.checked;
                autoRenderChart();
            });
        }
        if (optPolarEl) {
            styleState.polar = !!optPolarEl.checked;
            optPolarEl.addEventListener('change', () => {
                styleState.polar = !!optPolarEl.checked;
                autoRenderChart();
            });
        }

        initTreeToggles();
        applyDefaultColumns();

        const axisPlaceholderHtml = '<option value="">— load dataset first —</option>';
        [xColEl, yColEl, stackColEl, sizeColEl].forEach((el) => {
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

        // ── Initialise multi-select filter dropdowns ──────────
        initMultiSelect(schoolYearBtn, schoolYearDd, 'dv-school-year-opt', 'All');
        initMultiSelect(gradeBtn, gradeDd, 'dv-grade-opt', 'All');
        initMultiSelect(statusBtn, statusDd, 'dv-status-opt', 'All');

        updateChartTypeHelp();
        updateChartTypeOptions();
        if (!supportedCharts.includes(chartTypeEl.value)) {
            chartTypeEl.value = 'bar';
        }
        updateBuildAssistant();
