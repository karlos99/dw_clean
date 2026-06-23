        // ══════════════════════════════════════════════════════════════════
        // ── SUPERSET-STYLE EXTENSIONS ─────────────────────────────────────
        // ══════════════════════════════════════════════════════════════════

        const appDataEl      = document.getElementById('dataviz-app-data');
        const sqlUrl         = (appDataEl && appDataEl.dataset.sqlUrl)        || '';
        const schemaUrl      = (appDataEl && appDataEl.dataset.schemaUrl)     || '';
        const chartsUrl      = (appDataEl && appDataEl.dataset.chartsUrl)     || '';
        const saveChartUrl   = (appDataEl && appDataEl.dataset.saveChartUrl)  || '';
        const dashboardsUrl  = (appDataEl && appDataEl.dataset.dashboardsUrl) || '';

        // ─── Mode switching ───────────────────────────────────────────────
        const modeExploreBtnEl   = document.getElementById('dv-mode-explore');
        const modeSqlBtnEl       = document.getElementById('dv-mode-sql');
        const modeDashboardBtnEl = document.getElementById('dv-mode-dashboard');
        const explorePane        = document.getElementById('dv-explore-mode');
        const sqlPane            = document.getElementById('dv-sql-mode');
        const dashPane           = document.getElementById('dv-dashboard-mode');

        let appMode = 'explore';

        function switchMode(mode) {
            appMode = mode;
            [explorePane, sqlPane, dashPane].forEach((p) => { p && p.classList.remove('active'); });
            [modeExploreBtnEl, modeSqlBtnEl, modeDashboardBtnEl].forEach((b) => {
                if (!b) return;
                b.classList.remove('bg-blue-600', 'text-white');
                b.classList.add('text-slate-500');
            });
            const paneMap = { explore: explorePane, sql: sqlPane, dashboard: dashPane };
            const btnMap  = { explore: modeExploreBtnEl, sql: modeSqlBtnEl, dashboard: modeDashboardBtnEl };
            if (paneMap[mode]) paneMap[mode].classList.add('active');
            if (btnMap[mode]) {
                btnMap[mode].classList.add('bg-blue-600', 'text-white');
                btnMap[mode].classList.remove('text-slate-500');
            }
            if (mode === 'sql'       && !sqlSchemaLoaded)   { loadSqlSchema(); sqlSchemaLoaded = true; }
            if (mode === 'dashboard' && !dashboardsLoaded)  { loadDashboards(); dashboardsLoaded = true; }
        }

        let sqlSchemaLoaded   = false;
        let dashboardsLoaded  = false;

        if (modeExploreBtnEl)   modeExploreBtnEl.addEventListener('click',   () => switchMode('explore'));
        if (modeSqlBtnEl)       modeSqlBtnEl.addEventListener('click',       () => switchMode('sql'));
        if (modeDashboardBtnEl) modeDashboardBtnEl.addEventListener('click', () => switchMode('dashboard'));

        // ─── Dimension / Metric column classification ──────────────────────
        // Called after data loads to build the left-panel chips.

        const dimListEl      = document.getElementById('dv-dim-list');
        const metricListEl   = document.getElementById('dv-metric-list');
        const dimCountEl     = document.getElementById('dv-dim-count');
        const metricCountEl  = document.getElementById('dv-metric-count');
        const dimSearchEl    = document.getElementById('dv-dim-search');

        // Slot elements
        const slotXEl      = document.getElementById('dv-slot-x');
        const slotSeriesEl = document.getElementById('dv-slot-series');
        const slotYEl      = document.getElementById('dv-slot-y');
        const slotSizeEl   = document.getElementById('dv-slot-size');

        // Current slot assignments  { x: col|'', series: col|'', y: col|'', size: col|'' }
        const slotState = { x: '', series: '', y: '', size: '' };

        function colDisplayLabel(col) {
            const raw = state.columnDefinitions[col] || col;
            return String(raw).split('\n')[0].split(' — ')[0].trim().slice(0, 40);
        }

        function buildDimMetricPanels() {
            const cols = state.columns || [];
            const types = state.columnTypes || {};
            const searchQ = (dimSearchEl ? dimSearchEl.value.toLowerCase() : '');

            const dimCols    = [];
            const metricCols = [];
            const assessCols = [];

            cols.forEach((col) => {
                if (!col) return;
                const label = colDisplayLabel(col).toLowerCase();
                if (searchQ && !label.includes(searchQ) && !col.toLowerCase().includes(searchQ)) return;

                if (/^_assessment$/.test(col) || /^_test_|^_mark_/.test(col)) {
                    assessCols.push(col);
                    return;
                }
                if (types[col] === 'number') {
                    metricCols.push(col);
                } else {
                    dimCols.push(col);
                }
            });

            function makeChip(col, kind) {
                const isAssigned = Object.values(slotState).includes(col) && col !== '';
                const cls = kind === 'metric' ? 'metric' : (kind === 'assess' ? 'assessment' : 'dim');
                const icon = kind === 'metric' ? '∑' : (kind === 'assess' ? '★' : '◆');
                const lbl  = colDisplayLabel(col);
                const chip = document.createElement('span');
                chip.className = `dv-chip ${cls}${isAssigned ? ' assigned' : ''}`;
                chip.dataset.col = col;
                chip.dataset.kind = kind;
                chip.title = state.columnDefinitions[col] || col;
                chip.innerHTML = `<span class="opacity-60">${icon}</span>${escapeHtml(lbl)}`;
                chip.addEventListener('click', () => handleChipClick(col, kind));
                // ── Drag support ──────────────────────────────────────────
                chip.draggable = true;
                chip.addEventListener('dragstart', function(e) {
                    e.dataTransfer.setData('application/dv-chip', JSON.stringify({ col: col, kind: kind }));
                    e.dataTransfer.effectAllowed = 'copy';
                    chip.classList.add('dragging');
                });
                chip.addEventListener('dragend', function() {
                    chip.classList.remove('dragging');
                });
                return chip;
            }

            // Render DIMENSIONS
            if (dimListEl) {
                dimListEl.innerHTML = '';
                if (dimCols.length === 0 && assessCols.length === 0) {
                    dimListEl.innerHTML = '<span class="text-[11px] text-slate-400 italic px-1">None</span>';
                } else {
                    [...dimCols, ...assessCols].forEach((col) => {
                        const kind = assessCols.includes(col) ? 'assess' : 'dim';
                        dimListEl.appendChild(makeChip(col, kind));
                    });
                }
            }
            if (dimCountEl) dimCountEl.textContent = (dimCols.length + assessCols.length) || '';

            // Render METRICS
            if (metricListEl) {
                metricListEl.innerHTML = '';
                if (metricCols.length === 0) {
                    metricListEl.innerHTML = '<span class="text-[11px] text-slate-400 italic px-1">None</span>';
                } else {
                    metricCols.forEach((col) => metricListEl.appendChild(makeChip(col, 'metric')));
                }
            }
            if (metricCountEl) metricCountEl.textContent = metricCols.length || '';
        }

        // Auto-rebuild panels when state changes (hook onto after-load)
        const _origRenderPreview = typeof renderPreview === 'function' ? renderPreview : null;

        // ─── Chart type → slot configuration ──────────────────────────────
        const CHART_SLOT_CONFIG = {
            bar:          { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Series / Group',      yLabel:'Value',         xKinds:['dim','assess'] },
            column:       { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Series / Group',      yLabel:'Value',         xKinds:['dim','assess'] },
            grouped_bar:  { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Group Column',        yLabel:'Value',         xKinds:['dim','assess'] },
            stacked_bar:  { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Stack Column',        yLabel:'Value',         xKinds:['dim','assess'] },
            waterfall:    { show:['x','y'],          xLabel:'Category',       seriesLabel:'',                   yLabel:'Value',         xKinds:['dim','assess'] },
            line:         { show:['x','series','y'], xLabel:'X / Category',   seriesLabel:'Series (optional)',   yLabel:'Y Value',       xKinds:['dim','assess'] },
            spline:       { show:['x','series','y'], xLabel:'X / Category',   seriesLabel:'Series (optional)',   yLabel:'Y Value',       xKinds:['dim','assess'] },
            area:         { show:['x','series','y'], xLabel:'X / Category',   seriesLabel:'Series (optional)',   yLabel:'Y Value',       xKinds:['dim','assess'] },
            areaspline:   { show:['x','series','y'], xLabel:'X / Category',   seriesLabel:'Series (optional)',   yLabel:'Y Value',       xKinds:['dim','assess'] },
            stacked_area: { show:['x','series','y'], xLabel:'X / Category',   seriesLabel:'Stack by',            yLabel:'Y Value',       xKinds:['dim','assess'] },
            pie:          { show:['x','y'],          xLabel:'Label / Category',seriesLabel:'',                   yLabel:'Value',         xKinds:['dim','assess'] },
            donut:        { show:['x','y'],          xLabel:'Label / Category',seriesLabel:'',                   yLabel:'Value',         xKinds:['dim','assess'] },
            scatter:      { show:['x','series','y'], xLabel:'X Value',        seriesLabel:'Color / Group',       yLabel:'Y Value',       xKinds:['metric','dim','assess'] },
            bubble:       { show:['x','series','y','size'], xLabel:'X Value', seriesLabel:'Color / Group',       yLabel:'Y Value',       xKinds:['metric','dim','assess'] },
            heatmap:      { show:['x','series','y'], xLabel:'X Axis',         seriesLabel:'Y Axis',              yLabel:'Color / Value', xKinds:['dim','assess'] },
            histogram:    { show:['x','series'],     xLabel:'Column',         seriesLabel:'Group by (optional)', yLabel:'',              xKinds:['metric','dim'] },
            box:          { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Group (optional)',    yLabel:'Value Column',  xKinds:['dim','assess'] },
            funnel:       { show:['x','y'],          xLabel:'Stage / Label',  seriesLabel:'',                   yLabel:'Value',         xKinds:['dim','assess'] },
            pyramid:      { show:['x','y'],          xLabel:'Stage / Label',  seriesLabel:'',                   yLabel:'Value',         xKinds:['dim','assess'] },
            treemap:      { show:['x','y'],          xLabel:'Category',       seriesLabel:'',                   yLabel:'Size / Value',  xKinds:['dim','assess'] },
            radar:        { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Series (optional)',   yLabel:'Value',         xKinds:['dim','assess'] },
            gauge:        { show:['y'],              xLabel:'',               seriesLabel:'',                   yLabel:'Value Metric',  xKinds:[] },
            solid_gauge:  { show:['y'],              xLabel:'',               seriesLabel:'',                   yLabel:'Value Metric',  xKinds:[] },
            polar:        { show:['x','series','y'], xLabel:'Category',       seriesLabel:'Series (optional)',   yLabel:'Value',         xKinds:['dim','assess'] },
        };

        function updateSlotsForChartType(type) {
            const cfg = CHART_SLOT_CONFIG[type] || CHART_SLOT_CONFIG['bar'];
            const xWrap      = document.getElementById('dv-wrap-x');
            const seriesWrap = document.getElementById('dv-wrap-stack');
            const yWrap      = document.getElementById('dv-wrap-y');
            const sizeWrap   = document.getElementById('dv-wrap-size');

            // Show / hide wrappers
            if (xWrap)      xWrap.style.display     = cfg.show.includes('x')      ? '' : 'none';
            if (seriesWrap) seriesWrap.style.display = cfg.show.includes('series') ? '' : 'none';
            if (yWrap)      yWrap.style.display      = cfg.show.includes('y')      ? '' : 'none';
            if (sizeWrap)   sizeWrap.style.display   = cfg.show.includes('size')   ? '' : 'none';

            // Update labels
            const xLabelEl      = xWrap      && xWrap.querySelector('label');
            const seriesLabelEl = seriesWrap && seriesWrap.querySelector('label');
            const yLabelEls     = yWrap      && yWrap.querySelectorAll('label');
            if (xLabelEl && cfg.xLabel)      xLabelEl.textContent = cfg.xLabel;
            if (seriesLabelEl && cfg.seriesLabel !== undefined)
                seriesLabelEl.textContent = cfg.seriesLabel || 'Series / Split by';
            if (yLabelEls && yLabelEls.length && cfg.yLabel) yLabelEls[0].textContent = cfg.yLabel;

            // Update SLOTS x kinds (scatter/bubble allow numeric X)
            if (SLOTS && SLOTS.x) {
                SLOTS.x.kinds = cfg.xKinds && cfg.xKinds.length ? cfg.xKinds : ['dim','assess'];
            }

            // Refresh placeholder text for empty slots
            if (SLOTS) {
                Object.entries(SLOTS).forEach(function([k, s]) {
                    if (!slotState[k] && s.el) {
                        const lbl = k === 'x' ? (cfg.xLabel || 'dimension')
                            : k === 'series' ? (cfg.seriesLabel || 'dimension / metric')
                            : k === 'y' ? (cfg.yLabel || 'metric')
                            : 'metric';
                        s.el.innerHTML = '<span class="dv-slot-placeholder">Drag or click a ' + lbl.toLowerCase() + '</span>';
                    }
                });
            }
        }

        // ─── Slot drop wiring ──────────────────────────────────────────────
        function wireSlotDrop(slotKey) {
            var slotDef = SLOTS[slotKey];
            var el = slotDef && slotDef.el;
            if (!el) return;

            el.addEventListener('dragover', function(e) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'copy';
                el.classList.add('dragover');
            });
            el.addEventListener('dragleave', function(e) {
                if (!el.contains(e.relatedTarget)) el.classList.remove('dragover');
            });
            el.addEventListener('drop', function(e) {
                e.preventDefault();
                el.classList.remove('dragover');
                try {
                    var data = JSON.parse(e.dataTransfer.getData('application/dv-chip'));
                    var col  = data.col;
                    var kind = data.kind;
                    if (slotDef.kinds.includes(kind)) {
                        assignSlot(slotKey, col);
                    } else {
                        // Visual invalid flash
                        el.classList.add('drop-invalid');
                        setTimeout(function() { el.classList.remove('drop-invalid'); }, 700);
                    }
                } catch(_) {}
            });
        }

        // ─── Slot assignment ───────────────────────────────────────────────

        // Slot meta: { select: HTMLSelect, hidden: bool, kind: 'dim'|'metric' }
        const SLOTS = {
            x:      { el: slotXEl,      selectEl: xColEl,     label: 'X Axis',     kinds: ['dim','assess'] },
            series: { el: slotSeriesEl, selectEl: stackColEl,  label: 'Series',     kinds: ['dim','assess','metric'] },
            y:      { el: slotYEl,      selectEl: yColEl,      label: 'Y Value',    kinds: ['metric','dim','assess'] },
            size:   { el: slotSizeEl,   selectEl: sizeColEl,   label: 'Bubble Size',kinds: ['metric'] },
        };

        function renderSlot(slotKey) {
            const { el, label } = SLOTS[slotKey];
            if (!el) return;
            const col = slotState[slotKey];
            if (col) {
                el.innerHTML = `<span class="dv-slot-badge">
                    ${escapeHtml(colDisplayLabel(col))}
                    <span class="rm-btn" data-slot="${slotKey}" title="Remove">×</span>
                </span>`;
                el.querySelectorAll('.rm-btn').forEach((btn) => {
                    btn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        assignSlot(btn.dataset.slot, '');
                    });
                });
            } else {
                el.innerHTML = `<span class="dv-slot-placeholder">Drop or click a ${label.toLowerCase()}</span>`;
            }
        }

        function assignSlot(slotKey, col) {
            // If col is already assigned to another slot, clear that slot first
            if (col) {
                Object.keys(slotState).forEach((k) => {
                    if (k !== slotKey && slotState[k] === col) {
                        slotState[k] = '';
                        renderSlot(k);
                        syncSelect(k);
                    }
                });
            }
            slotState[slotKey] = col;
            renderSlot(slotKey);
            syncSelect(slotKey);
            buildDimMetricPanels();   // refresh "assigned" styling on chips
        }

        function syncSelect(slotKey) {
            const { selectEl } = SLOTS[slotKey];
            if (!selectEl) return;
            // Ensure the option exists in the hidden select
            const col = slotState[slotKey];
            if (col && !selectEl.querySelector(`option[value="${CSS.escape(col)}"]`)) {
                const opt = document.createElement('option');
                opt.value = col;
                opt.textContent = colDisplayLabel(col);
                selectEl.appendChild(opt);
            }
            selectEl.value = col;
            selectEl.dispatchEvent(new Event('change'));
        }

        // Assign-popover: tiny dropdown that appears when clicking a chip
        let _popover = null;

        function closePopover() {
            if (_popover) { _popover.remove(); _popover = null; }
        }
        document.addEventListener('click', (e) => {
            if (_popover && !_popover.contains(e.target)) closePopover();
        });

        function handleChipClick(col, kind) {
            closePopover();

            // Determine eligible slots for this chip type
            const eligible = Object.entries(SLOTS).filter(([, s]) => s.kinds.includes(kind));

            // Fast-assign if only one slot or an empty slot exists
            const emptySlot = eligible.find(([k]) => !slotState[k]);
            if (emptySlot) {
                assignSlot(emptySlot[0], col);
                return;
            }

            // Show tiny popover
            const chip = (dimListEl || metricListEl).closest('#dv-explore-mode')
                ?.querySelector(`.dv-chip[data-col="${CSS.escape(col)}"]`);
            const anchor = chip || document.activeElement;
            const rect = anchor ? anchor.getBoundingClientRect() : { left: 100, bottom: 100 };

            const pop = document.createElement('div');
            pop.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.bottom + 4}px;
                background:#1e293b;color:#fff;border-radius:8px;padding:4px 0;
                font-size:11px;font-weight:600;z-index:200;box-shadow:0 4px 12px rgba(0,0,0,.3);min-width:160px;`;
            pop.innerHTML = `<div style="padding:4px 12px;opacity:.5;font-size:10px;text-transform:uppercase;letter-spacing:.08em;">Assign "${colDisplayLabel(col)}" to</div>`;
            eligible.forEach(([k, s]) => {
                const current = slotState[k] ? `<span style="opacity:.5;font-size:10px"> (${colDisplayLabel(slotState[k])})</span>` : '';
                const item = document.createElement('div');
                item.style.cssText = 'padding:6px 16px;cursor:pointer;border-radius:4px;';
                item.innerHTML = `→ ${s.label}${current}`;
                item.addEventListener('mouseenter', () => { item.style.background = '#3b82f6'; });
                item.addEventListener('mouseleave', () => { item.style.background = ''; });
                item.addEventListener('click', (e) => {
                    e.stopPropagation();
                    assignSlot(k, col);
                    closePopover();
                });
                pop.appendChild(item);
            });
            document.body.appendChild(pop);
            _popover = pop;
        }

        // ─── Dimension search filter ────────────────────────────────────────
        if (dimSearchEl) {
            dimSearchEl.addEventListener('input', buildDimMetricPanels);
        }

        // ─── Hook onto data load to rebuild dim/metric panels ───────────────
        // Patch the existing loadData function's success callback by listening on
        // the load button's custom loadComplete event or via MutationObserver on
        // previewBody. We use a simple marker: after state.rows is populated.

        const _origLoadBtn = document.getElementById('dv-load-data');
        if (_origLoadBtn) {
            // MutationObserver on the preview table: after rows appear, rebuild
            if (previewBody) {
                const obs = new MutationObserver(() => {
                    if (state.rows && state.rows.length > 0) {
                        buildDimMetricPanels();
                    }
                });
                obs.observe(previewBody, { childList: true });
            }
        }

        // Also rebuild panels whenever existing dropdowns are repopulated (fillSelect calls)
        if (xColEl) {
            xColEl.addEventListener('_panelRefresh', buildDimMetricPanels);
        }

        // Initialise all slot renders + drag/drop wiring
        Object.keys(SLOTS).forEach(renderSlot);
        Object.keys(SLOTS).forEach(wireSlotDrop);

        // ── MULTI-DIMENSIONAL GROUP-BY ────────────────────────────────────
        function renderGroupByChips() {
            if (!groupByChipsEl) return;
            const cols = state.chartGroupByCols || [];
            groupByChipsEl.innerHTML = '';
            if (!cols.length) {
                groupByChipsEl.innerHTML = '<span class="dv-slot-placeholder">Drag dimensions to add group levels</span>';
                if (groupByHintEl) groupByHintEl.classList.add('hidden');
                return;
            }
            cols.forEach(function (col) {
                var lbl = colDisplayLabel ? colDisplayLabel(col) : col;
                var chip = document.createElement('span');
                chip.className = 'dv-slot-badge inline-flex items-center gap-1';
                chip.innerHTML = escapeHtml(lbl)
                    + '<span class="rm-btn cursor-pointer hover:text-red-600 ml-0.5" data-gbcol="'
                    + escapeHtml(col) + '" title="Remove">\xd7</span>';
                chip.querySelector('.rm-btn').addEventListener('click', function () {
                    removeGroupByCol(col);
                });
                groupByChipsEl.appendChild(chip);
            });
            // Show hint
            if (groupByHintEl && groupByPreviewEl) {
                var primaryX = xColEl ? xColEl.value : '';
                var allLevels = [primaryX].concat(cols).filter(Boolean).map(function (c) {
                    return colDisplayLabel ? colDisplayLabel(c) : c;
                });
                groupByPreviewEl.textContent = allLevels.join(' \xb7 ');
                groupByHintEl.classList.remove('hidden');
            }
        }

        function addGroupByCol(col) {
            if (!col) return;
            // Don't duplicate or shadow the primary X slot
            if ((state.chartGroupByCols || []).includes(col)) return;
            // Remove from any existing slot assignment first
            Object.keys(slotState).forEach(function (k) {
                if (slotState[k] === col) {
                    assignSlot(k, '');
                }
            });
            state.chartGroupByCols = (state.chartGroupByCols || []).concat([col]);
            renderGroupByChips();
            buildDimMetricPanels();
            autoRenderChart();
        }

        function removeGroupByCol(col) {
            state.chartGroupByCols = (state.chartGroupByCols || []).filter(function (c) { return c !== col; });
            renderGroupByChips();
            buildDimMetricPanels();
            autoRenderChart();
        }

        function clearGroupByCols() {
            state.chartGroupByCols = [];
            renderGroupByChips();
        }

        // Drag-and-drop: the group-by zone accepts dim/assess chips
        if (groupByChipsEl) {
            groupByChipsEl.addEventListener('dragover', function (e) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'copy';
                groupByChipsEl.classList.add('dragover');
            });
            groupByChipsEl.addEventListener('dragleave', function (e) {
                if (!groupByChipsEl.contains(e.relatedTarget)) groupByChipsEl.classList.remove('dragover');
            });
            groupByChipsEl.addEventListener('drop', function (e) {
                e.preventDefault();
                groupByChipsEl.classList.remove('dragover');
                try {
                    var data = JSON.parse(e.dataTransfer.getData('application/dv-chip'));
                    if (data.kind === 'dim' || data.kind === 'assess') {
                        addGroupByCol(data.col);
                    } else {
                        groupByChipsEl.classList.add('drop-invalid');
                        setTimeout(function () { groupByChipsEl.classList.remove('drop-invalid'); }, 700);
                    }
                } catch (_) {}
            });
        }

        // Patch handleChipClick to offer "Add as group level" when X is already filled
        var _origHandleChipClick = handleChipClick;
        handleChipClick = function (col, kind) {
            // If this is a dim/assess chip and X is already assigned, offer group-by
            if ((kind === 'dim' || kind === 'assess') && slotState['x'] && slotState['x'] !== col) {
                // Check if all eligible slots for this kind are filled
                var eligible = Object.entries(SLOTS).filter(function (e) { return e[1].kinds.includes(kind); });
                var emptySlot = eligible.find(function (e) { return !slotState[e[0]]; });
                if (!emptySlot) {
                    // All slots full — directly add as group level
                    addGroupByCol(col);
                    return;
                }
                // There's an empty slot, but also show group-by option via custom popover
                // Fall through to original handler which will assign the empty slot,
                // but also add our "group level" option to the popover
                var _origClosePopover = closePopover;
                // Let the original build the popover first
                _origHandleChipClick(col, kind);
                // Then inject the group-level option into the existing popover if shown
                if (_popover) {
                    var groupItem = document.createElement('div');
                    groupItem.style.cssText = 'padding:6px 16px;cursor:pointer;border-radius:4px;border-top:1px solid rgba(255,255,255,.1);margin-top:2px;';
                    var alreadyGrouped = (state.chartGroupByCols || []).includes(col);
                    groupItem.innerHTML = (alreadyGrouped ? '\u2714 ' : '\u2295 ') + 'Add as Group Level';
                    groupItem.addEventListener('mouseenter', function () { groupItem.style.background = '#10b981'; });
                    groupItem.addEventListener('mouseleave', function () { groupItem.style.background = ''; });
                    groupItem.addEventListener('click', function (e) {
                        e.stopPropagation();
                        if (!alreadyGrouped) addGroupByCol(col);
                        closePopover();
                    });
                    _popover.appendChild(groupItem);
                }
                return;
            }
            _origHandleChipClick(col, kind);
        };

        // Update buildDimMetricPanels to style group-by chips as assigned
        var _origBuildDimMetricPanels = buildDimMetricPanels;
        buildDimMetricPanels = function () {
            _origBuildDimMetricPanels();
            // Re-style chips that are in chartGroupByCols
            var gbCols = new Set(state.chartGroupByCols || []);
            if (!gbCols.size) return;
            var allChips = document.querySelectorAll('.dv-chip');
            allChips.forEach(function (chip) {
                if (gbCols.has(chip.dataset.col)) {
                    chip.classList.add('assigned');
                }
            });
        };

        // Initial render of (empty) group-by zone
        renderGroupByChips();
        // ── END MULTI-DIMENSIONAL GROUP-BY ────────────────────────────────

        // ── Chart type change → update slot labels / visibility ───────────────
        const _chartTypeSelectEl = document.getElementById('dv-chart-type');
        if (_chartTypeSelectEl) {
            _chartTypeSelectEl.addEventListener('change', function() {
                updateSlotsForChartType(_chartTypeSelectEl.value);
            });
            // Apply initial state (bar is default)
            updateSlotsForChartType(_chartTypeSelectEl.value || 'bar');
        }

