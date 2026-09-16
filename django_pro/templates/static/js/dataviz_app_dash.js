        // ──────────────────────────────────────────────────────────────────
        // ── DASHBOARD ─────────────────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────

        let _dashboards = [];
        let _savedCharts = [];
        let _activeDashboard = null;

        const dashSelectEl     = document.getElementById('dv-dashboard-select');
        const dashNewBtn       = document.getElementById('dv-dashboard-new');
        const dashAddChartBtn  = document.getElementById('dv-dashboard-add-chart');
        const dashRunAllBtn    = document.getElementById('dv-dashboard-run-all');
        const dashStatusEl     = document.getElementById('dv-dashboard-status');
        const dashGridEl       = document.getElementById('dv-dash-grid');

        const addToDashModal   = document.getElementById('dv-add-to-dash-modal');
        const addDashSelectEl  = document.getElementById('dv-add-dash-select');
        const addDashWidthEl   = document.getElementById('dv-add-dash-width');
        const addDashErrorEl   = document.getElementById('dv-add-dash-error');
        const addDashCancelBtn = document.getElementById('dv-add-dash-cancel');
        const addDashConfirmBtn= document.getElementById('dv-add-dash-confirm');

        const dashAddHtmlBtn   = document.getElementById('dv-dashboard-add-html');
        const dashPreviewToggle = document.getElementById('dv-dashboard-preview-toggle');
        const dashSidebar      = document.getElementById('dv-dash-sidebar');
        const dashFilterBar    = document.getElementById('dv-dash-filter-bar');
        const dashFilterChipsContainer = document.getElementById('dv-dash-filter-chips');
        const layoutTemplatesEl = document.getElementById('dv-layout-templates');
        let _isPreview = false;
        let _draggedCard = null;

        function refreshAddToDashSelect() {
            if (!addDashSelectEl) return;
            addDashSelectEl.innerHTML = '<option value="">— select —</option>';
            _dashboards.forEach((d) => {
                const opt = document.createElement('option');
                opt.value = d.id;
                opt.textContent = d.name;
                addDashSelectEl.appendChild(opt);
            });
        }

        function loadDashboards() {
            if (!dashboardsUrl) return;
            Promise.all([
                fetch(dashboardsUrl,         { credentials: 'same-origin' }).then((r) => r.json()),
                chartsUrl ? fetch(chartsUrl, { credentials: 'same-origin' }).then((r) => r.json()) : Promise.resolve({ charts: [] }),
            ])
                .then(([dashData, chartsData]) => {
                    _dashboards  = dashData.dashboards || [];
                    _savedCharts = chartsData.charts || [];

                    if (dashSelectEl) {
                        dashSelectEl.innerHTML = '<option value="">— select or create —</option>';
                        _dashboards.forEach((d) => {
                            const opt = document.createElement('option');
                            opt.value = d.id;
                            opt.textContent = d.name;
                            dashSelectEl.appendChild(opt);
                        });
                    }
                    refreshAddToDashSelect();

                    if (_dashboards.length > 0) {
                        selectDashboard(_dashboards[0].id);
                    }
                })
                .catch(() => {});
        }

        function selectDashboard(id) {
            _activeDashboard = _dashboards.find((d) => d.id === id) || null;
            if (dashSelectEl) dashSelectEl.value = id || '';
            const hasActive = !!_activeDashboard;
            if (dashAddChartBtn) dashAddChartBtn.classList.toggle('hidden', !hasActive);
            if (dashAddHtmlBtn)  dashAddHtmlBtn.classList.toggle('hidden', !hasActive);
            if (dashRunAllBtn)   dashRunAllBtn.classList.toggle('hidden', !hasActive);
            if (dashPreviewToggle) dashPreviewToggle.classList.toggle('hidden', !hasActive);
            const _renBtn = document.getElementById('dv-dashboard-rename');
            const _delBtn = document.getElementById('dv-dashboard-delete');
            if (_renBtn) _renBtn.classList.toggle('hidden', !hasActive);
            if (_delBtn) _delBtn.classList.toggle('hidden', !hasActive);
            renderDashGrid();
            _updateSidebar();
            _updateFilterSummaryBar();
        }

        function renderDashGrid() {
            if (!dashGridEl) return;
            if (!_activeDashboard) {
                dashGridEl.innerHTML = '<div class="w-full text-center py-16 text-slate-400"><p class="text-lg mb-2">&#9638;</p><p class="text-sm font-semibold">No dashboard selected</p><p class="text-xs mt-1">Create one below or select from the dropdown.</p></div>';
                return;
            }
            const slots = _activeDashboard.charts || [];
            if (slots.length === 0) {
                dashGridEl.innerHTML = '<div class="w-full text-center py-16 text-slate-400"><p class="text-sm font-semibold">No charts yet</p><p class="text-xs mt-1">Save a chart in Explore, then click + Add Chart.</p></div>';
                return;
            }
            dashGridEl.innerHTML = '';
            slots.forEach((slot) => {
                const isHtml = slot.element_type === 'html';
                const chart  = slot.chart;
                const card   = document.createElement('div');
                const hClass = slot.height_class || 'md';
                const showTitle = slot.show_title !== false;
                card.className = `dv-dash-card width-${slot.width_class} height-${hClass}${isHtml ? ' dv-dash-html-card' : ''}`;
                card.dataset.slotId  = slot.slot_id;
                if (chart) card.dataset.chartId = chart.id;

                // ── Shared helpers ──
                function _patchSlot(slotId, payload) {
                    fetch(`${dashboardsUrl.replace('api/dashboards/', 'api/slots/')}${slotId}/`, {
                        method: 'POST', credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                        body: JSON.stringify(payload),
                    }).catch(() => {});
                }

                // ── HTML element card ─────────────────────────────────────
                if (isHtml) {
                    card.innerHTML = `
                        <div class="dv-dash-card-header">
                            <span class="dv-drag-handle" title="Drag to reorder">&#9776;</span>
                            <span class="text-violet-600 font-bold text-[10px] uppercase tracking-wide">HTML</span>
                            <div class="flex items-center gap-1.5 shrink-0 ml-2">
                                <select class="dash-card-width text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white" data-slot="${slot.slot_id}" title="Width">
                                    <option value="third" ${slot.width_class==='third' ?'selected':''}>⅓</option>
                                    <option value="half"  ${slot.width_class==='half'  ?'selected':''}>½</option>
                                    <option value="full"  ${slot.width_class==='full'  ?'selected':''}>Full</option>
                                </select>
                                <select class="dash-card-height text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white" data-slot="${slot.slot_id}" title="Height">
                                    <option value="sm" ${hClass==='sm' ?'selected':''}>S</option>
                                    <option value="md" ${hClass==='md' ?'selected':''}>M</option>
                                    <option value="lg" ${hClass==='lg' ?'selected':''}>L</option>
                                    <option value="xl" ${hClass==='xl' ?'selected':''}>XL</option>
                                </select>
                                <button class="dash-card-remove text-slate-400 hover:text-red-500 text-sm leading-none" data-slot="${slot.slot_id}" title="Remove">✕</button>
                            </div>
                        </div>
                        <div class="dv-html-toolbar">
                            <button data-cmd="bold" title="Bold"><b>B</b></button>
                            <button data-cmd="italic" title="Italic"><i>I</i></button>
                            <button data-cmd="underline" title="Underline"><u>U</u></button>
                            <button data-cmd="insertHorizontalRule" title="Divider">―</button>
                            <button data-cmd="formatBlock:H2" title="Heading">H</button>
                            <button data-cmd="formatBlock:P" title="Paragraph">P</button>
                        </div>
                        <div class="dv-dash-card-body">
                            <div class="dv-html-editable" contenteditable="true">${slot.html_content || ''}</div>
                        </div>
                    `;
                    dashGridEl.appendChild(card);

                    // Toolbar commands
                    card.querySelectorAll('.dv-html-toolbar button[data-cmd]').forEach(btn => {
                        btn.addEventListener('click', () => {
                            var parts = btn.dataset.cmd.split(':');
                            document.execCommand(parts[0], false, parts[1] || null);
                        });
                    });

                    // Auto-save HTML content on blur
                    var editable = card.querySelector('.dv-html-editable');
                    if (editable) {
                        var _saveTimer = null;
                        editable.addEventListener('input', () => {
                            clearTimeout(_saveTimer);
                            _saveTimer = setTimeout(() => {
                                _patchSlot(slot.slot_id, { html_content: editable.innerHTML });
                            }, 800);
                        });
                    }

                    // Width change
                    card.querySelector('.dash-card-width').addEventListener('change', function() {
                        card.className = card.className.replace(/width-\w+/, `width-${this.value}`);
                        _patchSlot(slot.slot_id, { width_class: this.value });
                    });
                    // Height change
                    card.querySelector('.dash-card-height').addEventListener('change', function() {
                        card.className = card.className.replace(/height-\w+/, `height-${this.value}`);
                        _patchSlot(slot.slot_id, { height_class: this.value });
                    });
                    // Remove
                    card.querySelector('.dash-card-remove').addEventListener('click', function() {
                        fetch(`${dashboardsUrl.replace('api/dashboards/', 'api/slots/')}${slot.slot_id}/`, {
                            method: 'DELETE', credentials: 'same-origin',
                            headers: { 'X-CSRFToken': csrfToken },
                        }).then(r => r.json()).then(() => {
                            if (_activeDashboard) _activeDashboard.charts = (_activeDashboard.charts || []).filter(s => s.slot_id !== parseInt(slot.slot_id));
                            card.remove();
                            _updateSidebar();
                            _updateFilterSummaryBar();
                        }).catch(() => {});
                    });

                    _initDragDrop(card);
                    return; // done with HTML card
                }

                // ── Chart card (flip-card) ────────────────────────────────
                var _slotFilters = (slot.slot_filters || []).slice(); // local copy

                card.innerHTML = `
                    <div class="dv-dash-card-inner">
                        <!-- FRONT FACE -->
                        <div class="dv-dash-card-front">
                            <div class="dv-dash-card-header${showTitle ? '' : ' hidden-title'}">
                                <span class="dv-drag-handle" title="Drag to reorder">&#9776;</span>
                                <span class="truncate" title="${escapeHtml(chart.name)}">${escapeHtml(chart.name)}</span>
                                ${_slotFilters.length ? '<span class="text-[9px] text-blue-500 ml-1" title="Has per-chart filters">&#9679;</span>' : ''}
                            </div>
                            <div class="dv-dash-card-body" id="dash-chart-${slot.slot_id}"></div>
                            <button class="dv-flip-trigger" title="Card options">&#9881;</button>
                        </div>
                        <!-- BACK FACE -->
                        <div class="dv-dash-card-back">
                            <div class="flex items-center justify-between mb-2">
                                <h4 class="m-0">${escapeHtml(chart.name)}</h4>
                                <button class="dv-flip-back text-xs text-slate-500 hover:text-slate-800" title="Back to chart">&#10140; Chart</button>
                            </div>

                            <!-- Layout section -->
                            <div class="dv-back-section">
                                <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1">Layout</div>
                                <label>Width
                                    <select class="back-card-width" data-slot="${slot.slot_id}">
                                        <option value="third" ${slot.width_class==='third' ?'selected':''}>⅓</option>
                                        <option value="half"  ${slot.width_class==='half'  ?'selected':''}>½</option>
                                        <option value="full"  ${slot.width_class==='full'  ?'selected':''}>Full</option>
                                    </select>
                                </label>
                                <label>Height
                                    <select class="back-card-height" data-slot="${slot.slot_id}">
                                        <option value="sm" ${hClass==='sm' ?'selected':''}>S</option>
                                        <option value="md" ${hClass==='md' ?'selected':''}>M</option>
                                        <option value="lg" ${hClass==='lg' ?'selected':''}>L</option>
                                        <option value="xl" ${hClass==='xl' ?'selected':''}>XL</option>
                                    </select>
                                </label>
                                <label>
                                    <input type="checkbox" class="back-card-title-toggle rounded border-slate-300" ${showTitle ? 'checked' : ''} data-slot="${slot.slot_id}">
                                    Show title bar
                                </label>
                            </div>

                            <!-- Per-chart filters section -->
                            <div class="dv-back-section">
                                <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1">Per-Chart Filters</div>
                                <div class="dv-back-filter-chips flex flex-wrap gap-1 mb-1"></div>
                                <div class="flex gap-1 items-end flex-wrap">
                                    <select class="back-filter-col text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white max-w-[110px]">
                                        <option value="">Column…</option>
                                    </select>
                                    <select class="back-filter-op text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white">
                                        <option value="eq">=</option>
                                        <option value="ne">≠</option>
                                        <option value="contains">contains</option>
                                        <option value="gt">&gt;</option>
                                        <option value="lt">&lt;</option>
                                    </select>
                                    <input type="text" class="back-filter-val text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white w-[80px]" placeholder="Value…">
                                    <button class="back-filter-add text-[10px] px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500 font-semibold">+</button>
                                </div>
                            </div>

                            <!-- Actions -->
                            <div class="dv-back-section flex gap-2 mt-auto pt-2">
                                <button class="back-card-reload text-[10px] px-3 py-1 bg-emerald-600 text-white rounded font-semibold hover:bg-emerald-500">&#9654; Reload</button>
                                <button class="back-card-remove text-[10px] px-3 py-1 bg-red-50 text-red-600 border border-red-200 rounded font-semibold hover:bg-red-100">Remove</button>
                            </div>
                        </div>
                    </div>
                `;
                dashGridEl.appendChild(card);

                // ── Flip triggers ──
                card.querySelector('.dv-flip-trigger').addEventListener('click', () => {
                    card.classList.add('flipped');
                    // Populate filter column dropdown with chart's column defs
                    _populateBackFilterCols(card, chart);
                    _renderBackFilterChips(card, _slotFilters, slot);
                });
                card.querySelector('.dv-flip-back').addEventListener('click', () => {
                    card.classList.remove('flipped');
                });

                // ── Back face: Layout controls ──
                card.querySelector('.back-card-width').addEventListener('change', function() {
                    card.className = card.className.replace(/width-\w+/, `width-${this.value}`);
                    _patchSlot(slot.slot_id, { width_class: this.value });
                    slot.width_class = this.value;
                    var body = card.querySelector('.dv-dash-card-body');
                    if (body && body._dashRenderer) body._dashRenderer.resize();
                });
                card.querySelector('.back-card-height').addEventListener('change', function() {
                    card.className = card.className.replace(/height-\w+/, `height-${this.value}`);
                    _patchSlot(slot.slot_id, { height_class: this.value });
                    slot.height_class = this.value;
                    var body = card.querySelector('.dv-dash-card-body');
                    if (body && body._dashRenderer) setTimeout(() => body._dashRenderer.resize(), 50);
                });
                card.querySelector('.back-card-title-toggle').addEventListener('change', function() {
                    var header = card.querySelector('.dv-dash-card-header');
                    if (this.checked) header.classList.remove('hidden-title');
                    else header.classList.add('hidden-title');
                    _patchSlot(slot.slot_id, { show_title: this.checked });
                    slot.show_title = this.checked;
                    var body = card.querySelector('.dv-dash-card-body');
                    if (body && body._dashRenderer) setTimeout(() => body._dashRenderer.resize(), 50);
                });

                // ── Back face: Per-chart filter add ──
                card.querySelector('.back-filter-add').addEventListener('click', () => {
                    var col = card.querySelector('.back-filter-col').value;
                    var op  = card.querySelector('.back-filter-op').value;
                    var val = card.querySelector('.back-filter-val').value.trim();
                    if (!col || !val) return;
                    _slotFilters.push({ col: col, op: op, val: val, values: [val] });
                    slot.slot_filters = _slotFilters;
                    _patchSlot(slot.slot_id, { slot_filters: _slotFilters });
                    _renderBackFilterChips(card, _slotFilters, slot);
                    card.querySelector('.back-filter-val').value = '';
                    // Update the indicator dot on front face
                    _updateFilterIndicator(card, _slotFilters);
                });

                // ── Back face: Reload ──
                card.querySelector('.back-card-reload').addEventListener('click', () => {
                    card.classList.remove('flipped');
                    var containerEl = card.querySelector('.dv-dash-card-body');
                    if (containerEl) loadAndRenderDashCard(containerEl, chart, _slotFilters);
                });

                // ── Back face: Remove ──
                card.querySelector('.back-card-remove').addEventListener('click', function() {
                    fetch(`${dashboardsUrl.replace('api/dashboards/', 'api/slots/')}${slot.slot_id}/`, {
                        method: 'DELETE', credentials: 'same-origin',
                        headers: { 'X-CSRFToken': csrfToken },
                    }).then(r => r.json()).then(() => {
                        if (_activeDashboard) _activeDashboard.charts = (_activeDashboard.charts || []).filter(s => s.slot_id !== parseInt(slot.slot_id));
                        card.remove();
                        _updateSidebar();
                        _updateFilterSummaryBar();
                    }).catch(() => {});
                });

                _initDragDrop(card);
            });

            // Auto-render mini charts
            requestAnimationFrame(() => renderDashboardCharts(slots));
        }

        /** Populate filter column dropdown — restricted to columns originally filtered in chart */
        function _populateBackFilterCols(card, chart) {
            var sel = card.querySelector('.back-filter-col');
            if (!sel) return;
            var sourceFilters = chart.source_filters || {};
            // Only show columns that had active filters in the original chart
            var cols = Object.keys(sourceFilters).filter(function (k) {
                var v = sourceFilters[k];
                if (!v) return false;
                if (Array.isArray(v)) return v.length > 0;
                return String(v).trim() !== '';
            });
            // Fallback: if no source filters exist, use chart config dimensions
            if (!cols.length) {
                var cfg = chart.config || {};
                [cfg.x_col, cfg.y_col, cfg.stack_col, cfg.size_col].forEach(function (c) {
                    if (c && cols.indexOf(c) < 0) cols.push(c);
                });
                (cfg.x_cols || []).forEach(function (c) { if (c && cols.indexOf(c) < 0) cols.push(c); });
                (cfg.y_cols || []).forEach(function (c) { if (c && cols.indexOf(c) < 0) cols.push(c); });
            }
            sel.innerHTML = '<option value="">Column…</option>';
            cols.forEach(function (c) {
                var opt = document.createElement('option');
                opt.value = c;
                opt.textContent = colDisplayLabel ? colDisplayLabel(c) : c;
                sel.appendChild(opt);
            });
        }

        /** Render the per-chart filter chips on the back face */
        function _renderBackFilterChips(card, filters, slot) {
            var container = card.querySelector('.dv-back-filter-chips');
            if (!container) return;
            container.innerHTML = '';
            if (!filters.length) {
                container.innerHTML = '<span class="text-[10px] text-slate-400">None</span>';
                return;
            }
            filters.forEach((f, idx) => {
                var chip = document.createElement('span');
                chip.className = 'dv-back-filter-chip';
                chip.innerHTML = escapeHtml(colDisplayLabel ? colDisplayLabel(f.col) : f.col)
                    + ' ' + f.op + ' ' + escapeHtml(f.val)
                    + ' <span class="chip-x" data-fidx="' + idx + '">\u00d7</span>';
                chip.querySelector('.chip-x').addEventListener('click', () => {
                    filters.splice(idx, 1);
                    slot.slot_filters = filters;
                    _patchSlot(slot.slot_id, { slot_filters: filters });
                    _renderBackFilterChips(card, filters, slot);
                    _updateFilterIndicator(card, filters);
                });
                container.appendChild(chip);
            });
        }

        /** Update the small blue dot on the front face indicating active filters */
        function _updateFilterIndicator(card, filters) {
            var header = card.querySelector('.dv-dash-card-header');
            if (!header) return;
            var existing = header.querySelector('.dv-filter-dot');
            if (existing) existing.remove();
            if (filters.length) {
                var dot = document.createElement('span');
                dot.className = 'dv-filter-dot text-[9px] text-blue-500 ml-1';
                dot.title = 'Has per-chart filters';
                dot.innerHTML = '&#9679;';
                var titleSpan = header.querySelector('.truncate');
                if (titleSpan) titleSpan.after(dot);
            }
        }

        function _patchSlotGlobal(slotId, payload) {
            fetch(`${dashboardsUrl.replace('api/dashboards/', 'api/slots/')}${slotId}/`, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify(payload),
            }).catch(() => {});
        }

        // ──────────────────────────────────────────────────────────────────
        // ── Drag-and-drop reordering ──────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────
        function _initDragDrop(card) {
            var handle = card.querySelector('.dv-drag-handle');
            if (handle) {
                handle.addEventListener('mousedown', function () { card.draggable = true; });
                handle.addEventListener('mouseup', function () { card.draggable = false; });
            }
            card.addEventListener('dragstart', function (e) {
                _draggedCard = card;
                card.classList.add('dv-dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', card.dataset.slotId || '');
            });
            card.addEventListener('dragend', function () {
                card.classList.remove('dv-dragging');
                card.draggable = false;
                _draggedCard = null;
                if (dashGridEl) dashGridEl.querySelectorAll('.dv-drag-over').forEach(function (c) { c.classList.remove('dv-drag-over'); });
            });
            card.addEventListener('dragover', function (e) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (card !== _draggedCard) card.classList.add('dv-drag-over');
            });
            card.addEventListener('dragleave', function () {
                card.classList.remove('dv-drag-over');
            });
            card.addEventListener('drop', function (e) {
                e.preventDefault();
                card.classList.remove('dv-drag-over');
                if (!_draggedCard || card === _draggedCard || !dashGridEl) return;
                var cards = Array.from(dashGridEl.children);
                var fromIdx = cards.indexOf(_draggedCard);
                var toIdx = cards.indexOf(card);
                if (fromIdx < 0 || toIdx < 0) return;
                // Move the DOM element
                if (fromIdx < toIdx) { card.after(_draggedCard); } else { card.before(_draggedCard); }
                // Reorder slots in data model
                if (_activeDashboard && _activeDashboard.charts) {
                    var moved = _activeDashboard.charts.splice(fromIdx, 1)[0];
                    _activeDashboard.charts.splice(toIdx, 0, moved);
                    _persistDragOrder();
                    _updateSidebarCardList();
                }
            });
        }

        function _persistDragOrder() {
            if (!_activeDashboard) return;
            var order = (_activeDashboard.charts || []).map(function (s) { return s.slot_id; });
            fetch(dashboardsUrl + _activeDashboard.id + '/charts/', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ action: 'reorder', order: order }),
            }).catch(function () {});
        }

        // ──────────────────────────────────────────────────────────────────
        // ── Layout templates ──────────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────
        function _applyLayoutTemplate(layout) {
            if (!_activeDashboard) return;
            var slots = _activeDashboard.charts || [];
            if (!slots.length) return;
            slots.forEach(function (slot, i) {
                var w;
                switch (layout) {
                    case 'single':          w = 'full'; break;
                    case 'two-col':         w = 'half'; break;
                    case 'three-col':       w = 'third'; break;
                    case 'sidebar-right':   w = (i % 2 === 0) ? 'half' : 'third'; break;
                    case 'sidebar-left':    w = (i % 2 === 0) ? 'third' : 'half'; break;
                    case 'top-two-bottom':  w = (i === 0) ? 'full' : 'half'; break;
                    default: w = 'half';
                }
                slot.width_class = w;
                _patchSlotGlobal(slot.slot_id, { width_class: w });
            });
            renderDashGrid();
        }

        if (layoutTemplatesEl) {
            layoutTemplatesEl.addEventListener('click', function (e) {
                var tmpl = e.target.closest('.dv-layout-tmpl');
                if (!tmpl) return;
                var layout = tmpl.dataset.layout;
                if (!layout) return;
                // Highlight selected template
                layoutTemplatesEl.querySelectorAll('.dv-layout-tmpl').forEach(function (t) { t.classList.remove('selected'); });
                tmpl.classList.add('selected');
                _applyLayoutTemplate(layout);
            });
        }

        // ──────────────────────────────────────────────────────────────────
        // ── Preview mode toggle ───────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────
        if (dashPreviewToggle) {
            dashPreviewToggle.addEventListener('click', function () {
                _isPreview = !_isPreview;
                var gridContainer = document.getElementById('dv-dashboard-grid');
                if (gridContainer) gridContainer.classList.toggle('dv-dash-preview', _isPreview);
                if (dashSidebar) dashSidebar.classList.toggle('hidden', _isPreview);
                dashPreviewToggle.innerHTML = _isPreview ? '&#9998; Edit' : '&#128065; Preview';
                dashPreviewToggle.classList.toggle('bg-blue-600', _isPreview);
                dashPreviewToggle.classList.toggle('text-white', _isPreview);
                dashPreviewToggle.classList.toggle('border-blue-600', _isPreview);
                dashPreviewToggle.classList.toggle('border-slate-300', !_isPreview);
                // Resize charts after sidebar hides/shows (layout changes)
                if (dashGridEl) {
                    dashGridEl.querySelectorAll('.dv-dash-card-body').forEach(function (body) {
                        if (body._dashRenderer) setTimeout(function () { body._dashRenderer.resize(); }, 120);
                    });
                }
            });
        }

        // ──────────────────────────────────────────────────────────────────
        // ── Sidebar helpers ───────────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────
        function _updateSidebar() {
            _updateSidebarInfo();
            _updateSidebarCardList();
        }

        function _updateSidebarInfo() {
            var infoEl = document.getElementById('dv-dash-info');
            if (!infoEl) return;
            if (!_activeDashboard) {
                infoEl.innerHTML = '<p class="italic">Select a dashboard</p>';
                return;
            }
            var slots = _activeDashboard.charts || [];
            var chartCount = slots.filter(function (s) { return s.element_type !== 'html'; }).length;
            var htmlCount = slots.filter(function (s) { return s.element_type === 'html'; }).length;
            infoEl.innerHTML = '<p><strong>' + escapeHtml(_activeDashboard.name) + '</strong></p>'
                + '<p>' + chartCount + ' chart' + (chartCount !== 1 ? 's' : '') + ', '
                + htmlCount + ' text block' + (htmlCount !== 1 ? 's' : '') + '</p>'
                + '<p class="text-slate-400">'
                + (_activeDashboard.created_at ? new Date(_activeDashboard.created_at).toLocaleDateString() : '')
                + '</p>';
        }

        function _updateSidebarCardList() {
            var listEl = document.getElementById('dv-dash-card-list');
            var countEl = document.getElementById('dv-dash-card-count');
            if (!listEl) return;
            var slots = (_activeDashboard && _activeDashboard.charts) || [];
            if (countEl) countEl.textContent = '(' + slots.length + ')';
            if (!slots.length) {
                listEl.innerHTML = '<p class="text-slate-400 italic">No cards yet</p>';
                return;
            }
            listEl.innerHTML = '';
            slots.forEach(function (slot) {
                var isHtml = slot.element_type === 'html';
                var item = document.createElement('div');
                item.className = 'flex items-center gap-1.5 px-2 py-1 rounded hover:bg-slate-100 cursor-pointer';
                item.innerHTML = '<span class="text-[10px] ' + (isHtml ? 'text-violet-500' : 'text-blue-500') + '">'
                    + (isHtml ? '&#9638;' : '&#9679;') + '</span>'
                    + '<span class="truncate flex-1">'
                    + escapeHtml(isHtml ? 'Text block' : (slot.chart ? slot.chart.name : 'Unknown'))
                    + '</span>'
                    + '<span class="text-[9px] text-slate-400">' + (slot.width_class || '') + '</span>';
                item.addEventListener('click', function () {
                    if (!dashGridEl) return;
                    var card = dashGridEl.querySelector('[data-slot-id="' + slot.slot_id + '"]');
                    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
                listEl.appendChild(item);
            });
        }

        // ──────────────────────────────────────────────────────────────────
        // ── Dashboard filter summary bar ──────────────────────────────────
        // ──────────────────────────────────────────────────────────────────
        function _updateFilterSummaryBar() {
            if (!dashFilterBar || !dashFilterChipsContainer) return;
            if (!_activeDashboard) { dashFilterBar.classList.add('hidden'); return; }
            var slots = _activeDashboard.charts || [];
            var allFilters = [];
            var seen = new Set();
            slots.forEach(function (slot) {
                if (slot.element_type === 'html' || !slot.chart) return;
                var sf = slot.chart.source_filters || {};
                Object.keys(sf).forEach(function (key) {
                    var v = sf[key];
                    if (!v || (Array.isArray(v) && !v.length) || String(v).trim() === '') return;
                    var display = Array.isArray(v) ? v.join(', ') : String(v);
                    var uniqueKey = key + '::' + display;
                    if (seen.has(uniqueKey)) return;
                    seen.add(uniqueKey);
                    allFilters.push({ col: key, val: display, chartName: slot.chart.name });
                });
            });
            if (!allFilters.length) { dashFilterBar.classList.add('hidden'); return; }
            dashFilterBar.classList.remove('hidden');
            dashFilterChipsContainer.innerHTML = '';
            allFilters.forEach(function (f) {
                var chip = document.createElement('span');
                chip.className = 'dv-dash-filter-chip';
                chip.innerHTML = escapeHtml(colDisplayLabel ? colDisplayLabel(f.col) : f.col) + ': ' + escapeHtml(f.val);
                chip.title = 'From: ' + f.chartName;
                dashFilterChipsContainer.appendChild(chip);
            });
        }

        function renderDashboardCharts(slots) {
            slots.forEach((slot) => {
                if (slot.element_type === 'html') return; // HTML cards don't need chart rendering
                const containerEl = document.getElementById(`dash-chart-${slot.slot_id}`);
                if (!containerEl) return;
                const chart = slot.chart;
                if (!chart) return;
                const cfg   = chart.config || {};
                const rows  = [];   // Charts on dashboard render empty if no live data; fetch on demand.

                // Attempt to re-load data and render
                loadAndRenderDashCard(containerEl, chart, slot.slot_filters || []);
            });
        }

        function loadAndRenderDashCard(containerEl, chart, slotFilters) {
            if (!window.DVChartRenderer) return;
            const cfg     = chart.config || {};
            const filters = chart.source_filters || {};
            const colDefs = chart.column_defs || {};
            const srcType = chart.source_type || 'query';

            var hadChart = !!containerEl._dashRenderer;
            var busy = containerEl.querySelector('.dv-dash-refresh-status');
            if (!busy) {
                busy = document.createElement('span');
                busy.className = 'dv-dash-refresh-status';
                busy.style.cssText = 'position:absolute;top:6px;right:8px;z-index:2;font-size:10px;color:#64748b;background:rgba(255,255,255,.9);padding:2px 6px;border-radius:999px;';
                containerEl.style.position = 'relative';
                containerEl.appendChild(busy);
            }
            busy.textContent = 'Updating…';
            if (!hadChart) {
                containerEl.style.display = 'flex';
                containerEl.style.alignItems = 'center';
                containerEl.style.justifyContent = 'center';
                containerEl.innerHTML = '<span style="font-size:11px;color:#94a3b8">Loading…</span>';
                containerEl.appendChild(busy);
            }

            const renderUrl = (appDataEl && appDataEl.dataset.renderChartUrl) || '';
            const chartRenderTemplate = (appDataEl && appDataEl.dataset.chartRenderUrlTemplate) || '';

            // Saved charts can use the server's cached chart pipeline directly.
            // This avoids downloading every student page and then issuing a
            // second request just to turn those rows into a chart option.
            if (chartRenderTemplate && chart.id && !slotFilters.length) {
                if (containerEl._dashRequestController) containerEl._dashRequestController.abort();
                var directController = new AbortController();
                containerEl._dashRequestController = directController;
                var directUrl = chartRenderTemplate.replace('/0/render/', '/' + chart.id + '/render/');
                var directParams = new URLSearchParams({ source: 'dashboard', source_id: String(_activeDashboard ? _activeDashboard.id : '') });
                Object.entries(filters || {}).forEach(function ([key, value]) {
                    if (value == null || value === '' || (Array.isArray(value) && !value.length)) return;
                    (Array.isArray(value) ? value : [value]).forEach(function (item) {
                        directParams.append('pf_' + key, String(item));
                    });
                });
                fetch(directUrl + '?' + directParams.toString(), {
                    credentials: 'same-origin',
                    signal: directController.signal,
                }).then(function (response) {
                    if (!response.ok) throw new Error('Chart request failed');
                    return response.text();
                }).then(function (html) {
                    var parsed = new DOMParser().parseFromString(html, 'text/html');
                    var chartNode = parsed.querySelector('.chart-container');
                    if (!chartNode) throw new Error('Chart render failed');
                    var option = JSON.parse(chartNode.dataset.chartOption || '{}');
                    if (!option || !Object.keys(option).length) throw new Error('Chart returned no data');
                    containerEl.style.cssText = '';
                    containerEl.innerHTML = '';
                    var renderer = window.DVChartRenderer.create(containerEl);
                    renderer.render(option, cfg);
                    containerEl._dashRenderer = renderer;
                    containerEl.appendChild(busy);
                    busy.textContent = 'Updated';
                    setTimeout(function () { if (busy.parentNode === containerEl) busy.remove(); }, 900);
                }).catch(function (error) {
                    if (error.name === 'AbortError') return;
                    busy.textContent = hadChart ? 'Refresh failed' : 'Load failed';
                    busy.style.color = '#dc2626';
                });
                return;
            }

            // ── Determine which columns the chart actually needs ──────────
            var _reqCols = new Set();
            [cfg.x_col, cfg.y_col, cfg.stack_col, cfg.size_col].forEach(function (c) { if (c) _reqCols.add(c); });
            (cfg.x_cols || []).forEach(function (c) { if (c) _reqCols.add(c); });
            (cfg.y_cols || []).forEach(function (c) { if (c) _reqCols.add(c); });
            (cfg.group_by_cols || []).forEach(function (c) { if (c) _reqCols.add(c); });
            // Include columns used in per-chart (slot) filters
            if (slotFilters && slotFilters.length) {
                slotFilters.forEach(function (f) { if (f.col) _reqCols.add(f.col); });
            }
            var requiredCols = Array.from(_reqCols);

            // ── Helper: project rows to only required cols ────────────────
            function _projectRows(rows) {
                if (!requiredCols.length) return rows;
                return rows.map(function (row) {
                    var out = {};
                    requiredCols.forEach(function (c) { out[c] = row[c]; });
                    return out;
                });
            }

            // ── Helper: render once we have rows ──────────────────────────
            function _renderWithRows(rows) {
                // ── Apply per-chart (slot) filters ────────────────────────
                if (slotFilters && slotFilters.length) {
                    rows = rows.filter(function (row) {
                        return slotFilters.every(function (f) {
                            var raw = row[f.col];
                            var s = (raw == null) ? '' : String(raw);
                            var vals = f.values || [f.val];
                            switch (f.op) {
                                case 'eq':       return vals.some(function(v){ return s === v; });
                                case 'ne':       return vals.every(function(v){ return s !== v; });
                                case 'contains': return vals.some(function(v){ return s.toLowerCase().includes(v.toLowerCase()); });
                                case 'gt':       return parseFloat(s) > parseFloat(vals[0]);
                                case 'lt':       return parseFloat(s) < parseFloat(vals[0]);
                                default:         return true;
                            }
                        });
                    });
                }

                if (!rows.length) {
                    containerEl.innerHTML = '<span style="font-size:11px;color:#94a3b8">No data</span>';
                    return;
                }
                if (!renderUrl) {
                    containerEl.innerHTML = '<span style="font-size:11px;color:#94a3b8">Render URL missing</span>';
                    return;
                }
                // Project rows to only the columns the chart needs
                var projected = _projectRows(rows);
                // Build minimal colDefs for only required cols
                var minDefs = {};
                requiredCols.forEach(function (c) { if (colDefs[c]) minDefs[c] = colDefs[c]; });

                fetch(renderUrl, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ rows: projected, column_definitions: minDefs, chart_config: cfg }),
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (!data || !data.option) {
                            containerEl.innerHTML = '<span style="font-size:11px;color:#ef4444">' + (data && data.error ? escapeHtml(data.error) : 'Render failed') + '</span>';
                            return;
                        }
                        containerEl.style.cssText = '';
                        containerEl.innerHTML = '';
                        var renderer = window.DVChartRenderer.create(containerEl);
                        renderer.render(data.option, cfg);
                        containerEl._dashRenderer = renderer;
                    })
                    .catch(function (err) {
                        containerEl.innerHTML = '<span style="font-size:11px;color:#ef4444">Render error</span>';
                    });
            }

            // ── SQL-sourced chart ─────────────────────────────────────────
            if (srcType === 'sql' && chart.source_sql) {
                var _sqlApiUrl = (appDataEl && appDataEl.dataset.sqlUrl) || sqlUrl || '';
                if (!_sqlApiUrl) {
                    containerEl.innerHTML = '<span style="font-size:11px;color:#94a3b8">SQL URL missing</span>';
                    return;
                }
                fetch(_sqlApiUrl, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ sql: chart.source_sql }),
                })
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        if (data.error) {
                            containerEl.innerHTML = '<span style="font-size:11px;color:#ef4444">SQL: ' + escapeHtml(data.error) + '</span>';
                            return;
                        }
                        _renderWithRows(data.rows || []);
                    })
                    .catch(function () {
                        containerEl.innerHTML = '<span style="font-size:11px;color:#ef4444">SQL error</span>';
                    });
                return;
            }

            // ── Query / dataset-sourced chart ─────────────────────────────
            var loadUrl = (appDataEl && appDataEl.dataset.loadStudentsUrl) || '';
            if (!loadUrl) {
                containerEl.innerHTML = '<span style="font-size:11px;color:#94a3b8">Preview unavailable</span>';
                return;
            }

            var params = new URLSearchParams({ format: 'json', limit: '1500' });
            if (filters.search) params.set('search', filters.search);
            // Multi-select filters: serialize arrays as JSON
            var _dashSy = filters.year || filters.school_year || [];
            if (Array.isArray(_dashSy) && _dashSy.length) {
                params.set('school_year', JSON.stringify(_dashSy));
            } else if (_dashSy && !Array.isArray(_dashSy)) {
                params.set('school_year', _dashSy);
            }
            var _dashGr = filters.grade || [];
            if (Array.isArray(_dashGr) && _dashGr.length) {
                params.set('grade', JSON.stringify(_dashGr));
            } else if (_dashGr && !Array.isArray(_dashGr)) {
                params.set('grade', _dashGr);
            }
            var _dashSt = filters.status || [];
            if (Array.isArray(_dashSt) && _dashSt.length) {
                params.set('status', JSON.stringify(_dashSt));
            } else if (_dashSt && !Array.isArray(_dashSt)) {
                params.set('status', _dashSt);
            } else {
                params.set('status', '');
            }
            if (chart.source_id) params.set('saved_query', chart.source_id);

            // Send only the columns the chart needs so the response is smaller
            var colsToFetch = cfg.selected_columns || [];
            // Ensure at minimum the chart-required columns are included
            if (requiredCols.length) {
                var colSet = new Set(colsToFetch);
                requiredCols.forEach(function (c) { colSet.add(c); });
                colsToFetch = Array.from(colSet);
            }
            if (colsToFetch.length) {
                params.set('columns', JSON.stringify(colsToFetch));
            }

            // Fetch first page
            fetch(loadUrl + '?' + params.toString(), { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var allRows = data.students || data.rows || [];
                    var totalPages = Math.max(1, Number(data.total_pages || 1));

                    if (totalPages <= 1) {
                        // Single page — render immediately
                        _renderWithRows(allRows);
                        return;
                    }

                    // Multi-page: fetch remaining pages
                    var pagePromises = [];
                    for (var p = 2; p <= totalPages; p++) {
                        var pp = new URLSearchParams(params.toString());
                        pp.set('page', String(p));
                        pagePromises.push(
                            fetch(loadUrl + '?' + pp.toString(), { credentials: 'same-origin' })
                                .then(function (r) { return r.json(); })
                        );
                    }
                    return Promise.all(pagePromises).then(function (pages) {
                        pages.forEach(function (pg) {
                            var pgRows = pg.students || pg.rows || [];
                            allRows = allRows.concat(pgRows);
                        });
                        _renderWithRows(allRows);
                    });
                })
                .catch(function () {
                    containerEl.innerHTML = '<span style="font-size:11px;color:#ef4444">Load error</span>';
                });
        }

        // Dashboard run all
        if (dashRunAllBtn) {
            dashRunAllBtn.addEventListener('click', () => {
                if (!_activeDashboard) return;
                renderDashboardCharts(_activeDashboard.charts || []);
            });
        }

        // Dashboard selector
        if (dashSelectEl) {
            dashSelectEl.addEventListener('change', function() {
                const id = parseInt(this.value);
                if (id) selectDashboard(id);
            });
        }

        // New dashboard
        if (dashNewBtn) {
            dashNewBtn.addEventListener('click', () => {
                const name = prompt('New dashboard name:');
                if (!name || !name.trim()) return;
                fetch(dashboardsUrl, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ name: name.trim() }),
                })
                    .then((r) => r.json())
                    .then((data) => {
                        if (!data.dashboard) return;
                        data.dashboard.charts = [];
                        _dashboards.unshift(data.dashboard);
                        const opt = document.createElement('option');
                        opt.value = data.dashboard.id;
                        opt.textContent = data.dashboard.name;
                        if (dashSelectEl) { dashSelectEl.insertBefore(opt, dashSelectEl.children[1]); }
                        selectDashboard(data.dashboard.id);
                    })
                    .catch(() => {});
            });
        }

        // Rename dashboard
        const dashRenameBtn = document.getElementById('dv-dashboard-rename');
        const dashDeleteBtn = document.getElementById('dv-dashboard-delete');

        if (dashRenameBtn) {
            dashRenameBtn.addEventListener('click', () => {
                if (!_activeDashboard) return;
                const newName = prompt('Rename dashboard:', _activeDashboard.name);
                if (!newName || !newName.trim() || newName.trim() === _activeDashboard.name) return;
                fetch(`${dashboardsUrl}${_activeDashboard.id}/`, {
                    method: 'PUT', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ name: newName.trim() }),
                })
                    .then((r) => r.json())
                    .then((data) => {
                        if (data.error) { alert('Rename failed: ' + data.error); return; }
                        _activeDashboard.name = newName.trim();
                        // Update dropdown option text
                        if (dashSelectEl) {
                            const opt = dashSelectEl.querySelector(`option[value="${_activeDashboard.id}"]`);
                            if (opt) opt.textContent = newName.trim();
                        }
                        if (dashStatusEl) { dashStatusEl.textContent = `Renamed to "${newName.trim()}"`;  setTimeout(() => { if (dashStatusEl) dashStatusEl.textContent = ''; }, 3000); }
                    })
                    .catch(() => alert('Network error.'));
            });
        }

        if (dashDeleteBtn) {
            dashDeleteBtn.addEventListener('click', () => {
                if (!_activeDashboard) return;
                if (!confirm(`Delete dashboard "${_activeDashboard.name}"? This cannot be undone.`)) return;
                fetch(`${dashboardsUrl}${_activeDashboard.id}/`, {
                    method: 'DELETE', credentials: 'same-origin',
                    headers: { 'X-CSRFToken': csrfToken },
                })
                    .then((r) => r.json())
                    .then((data) => {
                        if (data.error) { alert('Delete failed: ' + data.error); return; }
                        const deletedId = _activeDashboard.id;
                        _dashboards = _dashboards.filter((d) => d.id !== deletedId);
                        // Remove from dropdown
                        if (dashSelectEl) {
                            const opt = dashSelectEl.querySelector(`option[value="${deletedId}"]`);
                            if (opt) opt.remove();
                        }
                        _activeDashboard = null;
                        selectDashboard(_dashboards.length ? _dashboards[0].id : null);
                    })
                    .catch(() => alert('Network error.'));
            });
        }

        // Add chart to dashboard button (opens modal)
        function openAddChartToDashModal() {
            if (!addToDashModal || !_activeDashboard) return;

            const _doOpen = () => {
                const noChartsEl = document.getElementById('dv-add-dash-no-charts');

                if (addDashSelectEl) {
                    addDashSelectEl.innerHTML = '<option value="">\u2014 select saved chart \u2014</option>';
                    _savedCharts.forEach((c) => {
                        const opt = document.createElement('option');
                        opt.value = c.id;
                        opt.textContent = c.name;
                        addDashSelectEl.appendChild(opt);
                    });
                }

                const hasSavedCharts = _savedCharts.length > 0;
                if (noChartsEl) noChartsEl.classList.toggle('hidden', hasSavedCharts);
                if (addDashSelectEl) addDashSelectEl.disabled = !hasSavedCharts;

                const titleEl = document.getElementById('dv-add-to-dash-title');
                if (titleEl) titleEl.textContent = 'Add Chart to \u201c' + _activeDashboard.name + '\u201d';

                if (addDashErrorEl) addDashErrorEl.classList.add('hidden');
                addToDashModal.classList.add('open');
            };

            // If charts haven't been fetched yet, fetch first
            if (!_savedCharts.length && chartsUrl) {
                fetch(chartsUrl, { credentials: 'same-origin' })
                    .then((r) => r.json())
                    .then((data) => {
                        _savedCharts = data.charts || [];
                        _doOpen();
                    })
                    .catch(() => _doOpen());
            } else {
                _doOpen();
            }
        }

        if (dashAddChartBtn) {
            dashAddChartBtn.addEventListener('click', openAddChartToDashModal);
        }

        if (addDashCancelBtn) addDashCancelBtn.addEventListener('click', () => {
            if (addToDashModal) addToDashModal.classList.remove('open');
        });

        if (addDashConfirmBtn) {
            addDashConfirmBtn.addEventListener('click', () => {
                if (!_activeDashboard) {
                    if (addDashErrorEl) { addDashErrorEl.textContent = 'No dashboard selected.'; addDashErrorEl.classList.remove('hidden'); }
                    return;
                }
                const chartId = addDashSelectEl ? parseInt(addDashSelectEl.value) : null;
                const width   = addDashWidthEl  ? addDashWidthEl.value : 'half';
                if (!chartId || isNaN(chartId)) {
                    if (addDashErrorEl) { addDashErrorEl.textContent = 'Select a chart.'; addDashErrorEl.classList.remove('hidden'); }
                    return;
                }
                if (addDashErrorEl) addDashErrorEl.classList.add('hidden');
                if (addDashConfirmBtn) addDashConfirmBtn.disabled = true;

                fetch(`${dashboardsUrl}${_activeDashboard.id}/charts/`, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ action: 'add', chart_id: chartId, width_class: width }),
                })
                    .then((r) => r.json())
                    .then((data) => {
                        if (addDashConfirmBtn) addDashConfirmBtn.disabled = false;
                        if (data.error) {
                            if (addDashErrorEl) { addDashErrorEl.textContent = data.error; addDashErrorEl.classList.remove('hidden'); }
                            return;
                        }
                        if (!data.slot) {
                            if (addDashErrorEl) { addDashErrorEl.textContent = 'Unexpected server response.'; addDashErrorEl.classList.remove('hidden'); }
                            return;
                        }
                        if (!_activeDashboard.charts) _activeDashboard.charts = [];
                        _activeDashboard.charts.push(data.slot);
                        if (addToDashModal) addToDashModal.classList.remove('open');
                        renderDashGrid();
                        _updateSidebar();
                        _updateFilterSummaryBar();
                    })
                    .catch((err) => {
                        if (addDashConfirmBtn) addDashConfirmBtn.disabled = false;
                        if (addDashErrorEl) { addDashErrorEl.textContent = 'Network error: ' + err.message; addDashErrorEl.classList.remove('hidden'); }
                    });
            });
        }

        // ──────────────────────────────────────────────────────────────────
        // ── Right-click / "Add to Dashboard" on saved charts ──────────────
        // ──────────────────────────────────────────────────────────────────
        // Allow the Save button dropdown to also trigger "Add to dashboard"
        // We'll add a context menu on the save button

        // ── Add HTML element to dashboard ─────────────────────────────────
        if (dashAddHtmlBtn) {
            dashAddHtmlBtn.addEventListener('click', () => {
                if (!_activeDashboard) return;
                fetch(`${dashboardsUrl}${_activeDashboard.id}/charts/`, {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({ action: 'add_html', width_class: 'full', height_class: 'sm', html_content: '<h3>Section Title</h3><p>Add your description here…</p>' }),
                })
                    .then(r => r.json())
                    .then(data => {
                        if (data.error) return;
                        if (data.slot) {
                            if (!_activeDashboard.charts) _activeDashboard.charts = [];
                            _activeDashboard.charts.push(data.slot);
                            renderDashGrid();
                            _updateSidebar();
                            _updateFilterSummaryBar();
                        }
                    })
                    .catch(() => {});
            });
        }

        const saveChartBtnParent = saveChartBtn && saveChartBtn.parentNode;

        // ──────────────────────────────────────────────────────────────────
        // ── Filter builder in data controls ───────────────────────────────
        // ──────────────────────────────────────────────────────────────────

        const filterChipsEl  = document.getElementById('dv-filter-chips');
        const addFilterBtn   = document.getElementById('dv-add-filter-btn');
        const filterBuilderEl= document.getElementById('dv-filter-builder');
        const filterColSelEl = document.getElementById('dv-filter-col');
        const filterOpEl     = document.getElementById('dv-filter-op');
        const filterValSearchEl = document.getElementById('dv-filter-val-search');
        const filterValDdEl     = document.getElementById('dv-filter-val-dd');
        const filterValSelEl    = document.getElementById('dv-filter-val-selected');
        const filterApplyBtn = document.getElementById('dv-filter-apply');
        const filterCancelInlineBtn = document.getElementById('dv-filter-cancel');

        let _inlineFilters = [];  // [{col, op, val, values[]}]
        let _filterValPicked = []; // currently picked values in the builder

        function renderFilterChips() {
            if (!filterChipsEl) return;
            filterChipsEl.innerHTML = '';
            _inlineFilters.forEach((f, idx) => {
                const chip = document.createElement('span');
                chip.className = 'inline-flex items-center gap-1 px-2 py-0.5 bg-blue-100 text-blue-800 rounded-full text-[11px] font-semibold';
                chip.innerHTML = `${escapeHtml(colDisplayLabel(f.col))} ${f.op} ${escapeHtml(f.val)} <span class="cursor-pointer hover:text-red-600 ml-0.5" data-fidx="${idx}">×</span>`;
                chip.querySelector('[data-fidx]').addEventListener('click', () => {
                    _inlineFilters.splice(idx, 1);
                    renderFilterChips();
                    applyFilters();
                });
                filterChipsEl.appendChild(chip);
            });
        }

        function populateFilterColSelect() {
            if (!filterColSelEl) return;
            filterColSelEl.innerHTML = '<option value="">— select column —</option>';
            (state.columns || []).forEach((col) => {
                const opt = document.createElement('option');
                opt.value = col;
                opt.textContent = colDisplayLabel(col);
                filterColSelEl.appendChild(opt);
            });
        }

        // ── Filter value multi-select helpers ─────────────────────────────

        /** Render the picked-value chips inside the builder */
        function _renderValChips() {
            if (!filterValSelEl) return;
            filterValSelEl.innerHTML = '';
            _filterValPicked.forEach((v, i) => {
                const c = document.createElement('span');
                c.className = 'inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded text-[10px] font-semibold';
                c.innerHTML = escapeHtml(v) + ' <span class="cursor-pointer hover:text-red-600" data-vi="' + i + '">\u00d7</span>';
                c.querySelector('[data-vi]').addEventListener('click', () => {
                    _filterValPicked.splice(i, 1);
                    _renderValChips();
                    _renderValDropdown();
                });
                filterValSelEl.appendChild(c);
            });
        }

        /** Build/filter the value dropdown list for the selected column */
        function _renderValDropdown(searchTerm) {
            if (!filterValDdEl) return;
            var col = filterColSelEl ? filterColSelEl.value : '';
            if (!col || !state.rows || !state.rows.length) {
                filterValDdEl.innerHTML = '<div class="px-2 py-1 text-[10px] text-slate-400">Select a column first</div>';
                return;
            }
            // Gather unique values
            var seen = new Set();
            var uniq = [];
            state.rows.forEach(function (r) {
                var v = r[col];
                var s = (v == null || v === '') ? '(blank)' : String(v);
                if (!seen.has(s)) { seen.add(s); uniq.push(s); }
            });
            uniq.sort(function (a, b) {
                if (a === '(blank)') return -1;
                if (b === '(blank)') return 1;
                return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
            });
            // Apply search filter
            var term = (searchTerm || '').toLowerCase().trim();
            var filtered = term ? uniq.filter(function (v) { return v.toLowerCase().includes(term); }) : uniq;
            // Limit display
            var MAX = 200;
            var shown = filtered.slice(0, MAX);
            var pickedSet = new Set(_filterValPicked);

            filterValDdEl.innerHTML = '';
            if (shown.length === 0) {
                filterValDdEl.innerHTML = '<div class="px-2 py-1 text-[10px] text-slate-400">No matching values</div>';
                return;
            }
            shown.forEach(function (v) {
                var lbl = document.createElement('label');
                lbl.className = 'flex items-center gap-1.5 px-2 py-1 hover:bg-blue-50 text-xs cursor-pointer';
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'rounded border-slate-300 text-blue-600 focus:ring-0';
                cb.value = v;
                cb.checked = pickedSet.has(v);
                cb.addEventListener('change', function () {
                    if (cb.checked) {
                        if (!_filterValPicked.includes(v)) _filterValPicked.push(v);
                    } else {
                        _filterValPicked = _filterValPicked.filter(function (x) { return x !== v; });
                    }
                    _renderValChips();
                });
                var sp = document.createElement('span');
                sp.textContent = v;
                lbl.appendChild(cb);
                lbl.appendChild(sp);
                filterValDdEl.appendChild(lbl);
            });
            if (filtered.length > MAX) {
                var more = document.createElement('div');
                more.className = 'px-2 py-1 text-[10px] text-slate-400';
                more.textContent = '… ' + (filtered.length - MAX) + ' more — type to narrow';
                filterValDdEl.appendChild(more);
            }
        }

        /** Populate values when column is selected */
        if (filterColSelEl) {
            filterColSelEl.addEventListener('change', function () {
                _filterValPicked = [];
                _renderValChips();
                _renderValDropdown();
                if (filterValDdEl) filterValDdEl.classList.remove('hidden');
                if (filterValSearchEl) filterValSearchEl.value = '';
            });
        }

        /** Search within values */
        if (filterValSearchEl) {
            filterValSearchEl.addEventListener('input', function () {
                _renderValDropdown(filterValSearchEl.value);
                if (filterValDdEl) filterValDdEl.classList.remove('hidden');
            });
            filterValSearchEl.addEventListener('focus', function () {
                if (filterValDdEl) filterValDdEl.classList.remove('hidden');
            });
        }

        /** Close value dropdown on outside click */
        document.addEventListener('click', function (e) {
            var wrap = document.getElementById('dv-filter-val-wrap');
            if (wrap && !wrap.contains(e.target)) {
                if (filterValDdEl) filterValDdEl.classList.add('hidden');
            }
        });

        if (addFilterBtn) {
            addFilterBtn.addEventListener('click', () => {
                populateFilterColSelect();
                _filterValPicked = [];
                _renderValChips();
                if (filterValDdEl) filterValDdEl.innerHTML = '';
                if (filterValSearchEl) filterValSearchEl.value = '';
                if (filterBuilderEl) filterBuilderEl.classList.remove('hidden');
                if (addFilterBtn) addFilterBtn.classList.add('hidden');
            });
        }

        if (filterCancelInlineBtn) {
            filterCancelInlineBtn.addEventListener('click', () => {
                if (filterBuilderEl) filterBuilderEl.classList.add('hidden');
                if (addFilterBtn) addFilterBtn.classList.remove('hidden');
            });
        }

        if (filterApplyBtn) {
            filterApplyBtn.addEventListener('click', () => {
                const col = filterColSelEl ? filterColSelEl.value : '';
                const op  = filterOpEl ? filterOpEl.value : 'eq';
                // Use picked values, or fall back to search text
                var vals = _filterValPicked.length
                    ? _filterValPicked.slice()
                    : (filterValSearchEl && filterValSearchEl.value.trim())
                        ? [filterValSearchEl.value.trim()]
                        : [];
                if (!col || !vals.length) return;
                var valStr = vals.join(', ');
                _inlineFilters.push({ col, op, val: valStr, values: vals });
                renderFilterChips();
                applyFilters();
                if (filterBuilderEl) filterBuilderEl.classList.add('hidden');
                if (addFilterBtn) addFilterBtn.classList.remove('hidden');
                _filterValPicked = [];
                if (filterValSearchEl) filterValSearchEl.value = '';
            });
        }

        // ── Rebuild dim/metric panels when existing dropdowns are updated ──
        // Hook onto the fillSelect calls by watching for changes to xColEl options
        const panelRefreshObs = new MutationObserver(() => {
            if (state.columns && state.columns.length > 0) buildDimMetricPanels();
        });
        if (xColEl) panelRefreshObs.observe(xColEl, { childList: true });

    });
})();
