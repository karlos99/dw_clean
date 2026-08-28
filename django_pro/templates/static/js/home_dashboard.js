/* Home dashboard — per-user charts grid on the landing page.
 *
 * - Sticky "User Charts" dock on the right edge with search and instant "+ Add" buttons.
 * - Rich drag-and-drop experience with live snapped grid placeholder preview.
 * - Medium default size (6 columns x 4 rows on 12-col grid, ~340px height).
 * - Proper responsive chart container sizing and auto-resizing.
 */
(function () {
    'use strict';

    var app = document.getElementById('home-dash-app');
    if (!app) return;

    var dashUrl         = app.dataset.dashboardUrl;
    var renderUrlTpl    = app.dataset.renderUrlTemplate;    // .../999999999/render/
    var csrf            = app.dataset.csrf;
    var gridEl          = document.getElementById('home-grid');
    var emptyEl         = document.getElementById('home-grid-empty');
    var dropPreviewEl   = document.getElementById('home-grid-drop-preview');
    var toastEl         = document.getElementById('home-toast');

    var maxCharts = parseInt(app.dataset.maxCharts || '0', 10) || 0;
    var forcedChartIds = [];
    try { forcedChartIds = JSON.parse(app.dataset.forcedChartIds || '[]') || []; } catch (e) { forcedChartIds = []; }

    var grid = null;
    var saveTimer = null;
    var skipNextChange = false;
    var toastTimer = null;

    /* ── Toast notification ───────────────────────────────────── */
    function showToast(msg, type) {
        if (!toastEl) return;
        clearTimeout(toastTimer);
        toastEl.textContent = msg;
        toastEl.className = 'fixed bottom-6 right-6 z-50 transition-all duration-200 transform translate-y-0 opacity-100 flex items-center gap-2 text-xs px-4 py-2.5 rounded-xl shadow-xl font-medium ' +
            (type === 'error' ? 'bg-red-900 text-red-100 ring-1 ring-red-700' :
             type === 'success' ? 'bg-emerald-900 text-emerald-100 ring-1 ring-emerald-700' :
             'bg-slate-900 text-white ring-1 ring-slate-700');
        toastTimer = setTimeout(function () {
            toastEl.className = 'fixed bottom-6 right-6 z-50 transition-all duration-200 transform translate-y-8 opacity-0 pointer-events-none flex items-center gap-2 text-xs px-4 py-2.5 rounded-xl shadow-xl font-medium bg-slate-900 text-white';
        }, 2800);
    }

    /* ── Helpers ─────────────────────────────────────────────── */
    function postJson(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
        }).then(function (r) { return r.json(); });
    }

    var schoolId = app.dataset.schoolId || '';
    var showLocation = app.dataset.showLocation !== '0';

    function renderUrl(chartId) {
        var url = renderUrlTpl.replace('999999999', String(chartId)) +
            '?source=dashboard&source_id=home&source_name=' + encodeURIComponent('My Charts');
        if (schoolId && schoolId !== 'all') url += '&school_id=' + encodeURIComponent(schoolId);
        return url;
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function buildWidget(data, opts) {
        opts = opts || {};
        var w = opts.w || data.gs_w || 6;
        var h = opts.h || data.gs_h || 4;
        var x = opts.x != null ? opts.x : (data.gs_x != null ? data.gs_x : 0);
        var y = opts.y != null ? opts.y : (data.gs_y != null ? data.gs_y : 0);
        var isForced = !!data.is_forced;

        var el = document.createElement('div');
        el.className = 'grid-stack-item';
        el.setAttribute('gs-id', String(data.slot_id));
        el.setAttribute('gs-x', String(x));
        el.setAttribute('gs-y', String(y));
        el.setAttribute('gs-w', String(w));
        el.setAttribute('gs-h', String(h));
        el.setAttribute('gs-min-w', '4');
        el.setAttribute('gs-min-h', '3');
        if (isForced) el.setAttribute('data-forced', '1');

        var lockIcon = isForced
            ? '<svg class="w-3 h-3 shrink-0 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" title="Required chart"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>'
            : '';
        var requiredBadge = isForced
            ? '<span class="shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-amber-50 text-amber-700 ring-1 ring-amber-200 text-[10px] font-semibold">Required</span>'
            : '';
        var tools = isForced
            ? ''
            : '<div class="home-slot-tools absolute top-1.5 right-1.5 z-20 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition">' +
              '<button type="button" class="home-slot-remove w-6 h-6 rounded-md bg-white/90 shadow-sm ring-1 ring-slate-200 hover:bg-red-50 text-slate-400 hover:text-red-600 text-lg leading-none" aria-label="Remove chart">&times;</button></div>';

        el.innerHTML =
            '<div class="grid-stack-item-content bg-white rounded-xl shadow-sm ring-1 ring-slate-200 flex flex-col relative group">' +
            tools +
            '  <div class="home-slot-location flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/80 rounded-t-xl' + (showLocation ? '' : ' hidden') + '">' +
            '    <span class="flex items-center gap-1.5 min-w-0 text-xs font-semibold text-slate-700 truncate" title="' + esc(data.title) + '">' + lockIcon + '<span class="truncate">' + esc(data.title) + '</span></span>' +
            '    ' + requiredBadge +
            '  </div>' +
            '  <div class="home-slot-body flex-1 min-h-0 p-2">' +
            (data.html ? data.html : '<div class="text-xs text-red-500 p-2">' + esc(data.error || 'Could not load chart') + '</div>') +
            '  </div>' +
            '</div>';
        return el;
    }

    function addWidgetToGrid(data, opts) {
        opts = opts || {};
        var w = data.gs_w || 6;
        var h = data.gs_h || 4;
        var x = (opts.x != null) ? opts.x : (data.gs_x != null ? data.gs_x : 0);
        var y = (opts.y != null) ? opts.y : (data.gs_y != null ? data.gs_y : 0);

        var el = buildWidget(data, { w: w, h: h, x: x, y: y });
        var cfg = {
            w: w,
            h: h,
            minW: 4,
            minH: 3,
            autoPosition: !!opts.autoPosition,
        };
        if (!opts.autoPosition) {
            cfg.x = x;
            cfg.y = y;
        }
        cfg.id = String(data.slot_id);

        var node = grid.addWidget(el, cfg);
        if (!node) node = el;
        node.setAttribute('gs-id', String(data.slot_id));
        wireSlotControls(node);
        
        // Initialize the chart directly on insertion
        var c = node.querySelector('.chart-container');
        if (c) {
            c._echartsInited = false;
            c._echartsObserved = false;
            if (window.initChart) window.initChart(c);
        }

        // Schedule multiple resize passes across the GridStack animation window
        function resizeChart() {
            var c = node.querySelector('.chart-container');
            if (c && c._echartsInstance) {
                c._echartsInstance.resize();
            }
        }
        requestAnimationFrame(function () {
            resizeChart();
            setTimeout(resizeChart, 50);
            setTimeout(resizeChart, 150);
            setTimeout(resizeChart, 350);
            setTimeout(resizeChart, 600);
        });
        return node;
    }

    function updateEmpty() {
        if (!emptyEl) return;
        var nodes = (grid && grid.save(false)) || [];
        if (nodes.length > 0) {
            emptyEl.classList.add('hidden');
        } else {
            emptyEl.classList.remove('hidden');
        }
    }

    function saveLayout() {
        if (skipNextChange) { skipNextChange = false; return; }
        var nodes = grid.save(false) || [];
        var layout = nodes.map(function (n, i) {
            return {
                slot_id: n.id,
                gs_x: n.x || 0,
                gs_y: n.y || 0,
                gs_w: n.w || 6,
                gs_h: n.h || 4,
                position: i,
            };
        }).filter(function (l) { return l.slot_id; });
        if (layout.length) postJson(dashUrl, { action: 'reorder', layout: layout });
    }

    function debouncedSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveLayout, 400);
    }

    function wireSlotControls(node) {
        var rm = node.querySelector('.home-slot-remove');
        if (rm && !rm._wired) {
            rm._wired = true;
            rm.addEventListener('click', function () { removeSlot(node); });
        }
    }

    function removeSlot(node) {
        if (node.getAttribute('data-forced') === '1') {
            showToast('This chart is required by your administrator and cannot be removed.', 'error');
            return;
        }
        var slotId = node.getAttribute('gs-id') || node.getAttribute('data-gs-id');
        if (!slotId) {
            var nodes = grid.save(false) || [];
            for (var i = 0; i < nodes.length; i++) {
                var el = gridEl.querySelector('[gs-id="' + nodes[i].id + '"]') || gridEl.querySelector('.grid-stack-item:nth-child(' + (i + 1) + ')');
                if (el === node) { slotId = nodes[i].id; break; }
            }
        }
        if (!slotId) { showToast('Could not identify this chart.', 'error'); return; }
        postJson(dashUrl, { action: 'remove', slot_id: slotId }).then(function (data) {
            if (data.success) {
                skipNextChange = true;
                grid.removeWidget(node, true);
                updateEmpty();
                showToast('Chart removed from homepage.');
            } else {
                showToast(data.error || 'Could not remove chart.', 'error');
            }
        });
    }

    function initGrid() {
        if (!gridEl) return;
        grid = GridStack.init({
            column: 12,
            margin: 8,
            cellHeight: 80,
            float: false,
            animate: true,
            minRow: 2,
            resizable: { handles: 'e,se,s,sw,w' },
        }, gridEl);
        grid.on('change', debouncedSave);
        grid.on('resizestop dragstop added', function (event, el) {
            if (el) {
                var chartEl = el.querySelector('.chart-container');
                if (chartEl && chartEl._echartsInstance) {
                    chartEl._echartsInstance.resize();
                }
            } else {
                gridEl.querySelectorAll('.chart-container').forEach(function (chartEl) {
                    if (chartEl._echartsInstance) chartEl._echartsInstance.resize();
                });
            }
        });
        window.addEventListener('resize', function () {
            if (!gridEl) return;
            gridEl.querySelectorAll('.chart-container').forEach(function (chartEl) {
                if (chartEl._echartsInstance) chartEl._echartsInstance.resize();
            });
        });
    }

    function wireExisting() {
        if (!gridEl) return;
        gridEl.querySelectorAll('.grid-stack-item').forEach(function (node) {
            wireSlotControls(node);
        });
        updateEmpty();
        if (window.initAllCharts) window.initAllCharts();
    }

    /* ── Direct Add Chart (One-Click or from Drag) ─────────────── */
    function countUserPlacedCharts() {
        if (!grid) return 0;
        var count = 0;
        gridEl.querySelectorAll('.grid-stack-item').forEach(function (el) {
            if (el.getAttribute('data-forced') !== '1') count++;
        });
        return count;
    }

    function addChartById(chartId, chartName, targetCoords) {
        chartId = parseInt(chartId, 10);
        if (!chartId) return;

        if (maxCharts > 0 && countUserPlacedCharts() >= maxCharts) {
            showToast('You have reached the maximum of ' + maxCharts + ' charts on your home dashboard. Remove a chart first or ask your admin to raise the limit.', 'error');
            return;
        }

        showToast('Adding ' + (chartName || 'chart') + '…');
        var payload = {
            action: 'add_chart',
            chart_id: chartId,
            gs_w: 6,
            gs_h: 4,
        };

        postJson(dashUrl, payload).then(function (data) {
            if (!data.success) {
                showToast(data.error || 'Could not add chart.', 'error');
                return;
            }
            var slot = data.slot;
            var chart = slot.chart || {};
            fetch(renderUrl(chart.id || chartId), { credentials: 'same-origin' })
                .then(function (r) { return r.text(); })
                .then(function (html) {
                    var opts = { autoPosition: !targetCoords };
                    if (targetCoords) {
                        opts.x = targetCoords.x;
                        opts.y = targetCoords.y;
                    }
                    addWidgetToGrid({
                        slot_id: slot.slot_id,
                        title: chart.name || chartName || 'Chart',
                        html: html,
                        error: '',
                        gs_w: slot.gs_w || 6,
                        gs_h: slot.gs_h || 4,
                        is_forced: !!slot.is_forced,
                    }, opts);
                    updateEmpty();
                    showToast('Added ' + (chart.name || chartName || 'chart') + ' to homepage!', 'success');
                    if (window.initAllCharts) window.initAllCharts();
                });
        }).catch(function () {
            showToast('Failed to add chart.', 'error');
        });
    }

    /* ── Custom pointer drag from the dock into the grid ───────── */
    var ucList = document.getElementById('user-charts-list');
    var dock = document.getElementById('uc-dock');
    var dockTab = document.getElementById('uc-tab');

    var dragState = null;

    function getGridDropCoordinates(e) {
        if (!grid || !gridEl) return null;
        var rect = gridEl.getBoundingClientRect();
        if (e.clientX < rect.left - 20 || e.clientX > rect.right + 20 ||
            e.clientY < rect.top - 20 || e.clientY > rect.bottom + 60) {
            return null;
        }

        try {
            var cell = grid.getCellFromPixel({ clientX: e.clientX, clientY: e.clientY });
            if (cell) {
                // Snap to 2-column layout (x = 0 or 6) for clean medium side-by-side fit
                var col = cell.x < 6 ? 0 : 6;
                var row = Math.max(0, cell.y || 0);
                return { x: col, y: row };
            }
        } catch (err) { /* fallback below */ }

        // Fallback relative position
        var relX = Math.max(0, e.clientX - rect.left);
        var colFb = relX < rect.width / 2 ? 0 : 6;
        return { x: colFb, y: 0 };
    }

    function updateDropPreview(coords) {
        if (!dropPreviewEl || !gridEl) return;
        if (!coords) {
            dropPreviewEl.style.display = 'none';
            return;
        }
        var rect = gridEl.getBoundingClientRect();
        var margin = 8;
        var cellHeight = 80;
        var colWidth = (rect.width - margin * 11) / 12;

        var w = 6; // medium width
        var h = 4; // medium height

        var previewWidth = colWidth * w + margin * (w - 1);
        var previewHeight = cellHeight * h + margin * (h - 1);
        var previewLeft = coords.x * (colWidth + margin);
        var previewTop = coords.y * (cellHeight + margin);

        dropPreviewEl.style.display = 'block';
        dropPreviewEl.style.left = previewLeft + 'px';
        dropPreviewEl.style.top = previewTop + 'px';
        dropPreviewEl.style.width = previewWidth + 'px';
        dropPreviewEl.style.height = previewHeight + 'px';
    }

    function createGhost(name, type) {
        var g = document.createElement('div');
        g.className = 'fixed z-50 pointer-events-none bg-white/95 backdrop-blur shadow-2xl border-2 border-blue-500 rounded-xl p-3 w-64 ring-4 ring-blue-500/10 flex flex-col gap-2 transition-transform';
        g.innerHTML =
            '<div class="flex items-center gap-2.5">' +
            '  <div class="w-7 h-7 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center flex-shrink-0 ring-1 ring-blue-200">' +
            '    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>' +
            '  </div>' +
            '  <div class="min-w-0 flex-1">' +
            '    <div class="text-xs font-bold text-slate-800 truncate">' + esc(name) + '</div>' +
            '    <div class="text-[10px] text-blue-600 font-medium capitalize">' + esc(type || 'Chart') + '</div>' +
            '  </div>' +
            '</div>' +
            '<div class="flex items-center justify-between text-[10px] text-slate-500 bg-slate-50 px-2 py-1 rounded-md border border-slate-100">' +
            '  <span>Placement size:</span>' +
            '  <span class="font-semibold text-slate-700">Medium (6 × 4)</span>' +
            '</div>';
        g.style.left = '-9999px';
        g.style.top = '-9999px';
        document.body.appendChild(g);
        return g;
    }

    function finishDrag(e) {
        if (!dragState) return;
        var state = dragState;
        dragState = null;
        document.body.classList.remove('select-none', 'is-dragging-chart');
        if (state.ghost) state.ghost.remove();
        updateDropPreview(null);

        var coords = getGridDropCoordinates(e);
        if (!coords) {
            closeDock();
            return;
        }

        addChartById(state.id, state.name, coords);
        closeDock();
    }

    function wireDrag() {
        if (!ucList) return;
        ucList.addEventListener('pointerdown', function (e) {
            if (e.button !== 0) return;
            // Ignore click on Add button (handled separately)
            if (e.target.closest('.uc-add-btn')) return;

            var item = e.target.closest('.uc-item');
            if (!item) return;
            var id = item.dataset.chartId;
            if (!id) return;
            dragState = {
                id: id,
                name: item.dataset.chartName || 'Chart',
                type: item.dataset.chartType || 'Chart',
                startX: e.clientX,
                startY: e.clientY,
                ghost: null,
                active: false,
            };
        });

        window.addEventListener('pointermove', function (e) {
            if (!dragState) return;
            if (!dragState.active) {
                var dx = e.clientX - dragState.startX;
                var dy = e.clientY - dragState.startY;
                if (Math.abs(dx) + Math.abs(dy) < 6) return;
                dragState.active = true;
                dragState.ghost = createGhost(dragState.name, dragState.type);
                document.body.classList.add('select-none', 'is-dragging-chart');
                hideChartPreview();
            }
            var g = dragState.ghost;
            if (g) {
                g.style.left = (e.clientX + 16) + 'px';
                g.style.top = (e.clientY + 16) + 'px';
            }

            var coords = getGridDropCoordinates(e);
            updateDropPreview(coords);
        });

        window.addEventListener('pointerup', finishDrag);
        window.addEventListener('pointercancel', function () {
            if (dragState && dragState.ghost) dragState.ghost.remove();
            dragState = null;
            document.body.classList.remove('select-none', 'is-dragging-chart');
            updateDropPreview(null);
        });

        // Click on + Add button on items
        ucList.addEventListener('click', function (e) {
            var addBtn = e.target.closest('.uc-add-btn');
            if (!addBtn) return;
            e.stopPropagation();
            var item = addBtn.closest('.uc-item');
            if (!item) return;
            var chartId = item.dataset.chartId;
            var chartName = item.dataset.chartName || 'Chart';
            addChartById(chartId, chartName);
        });
    }

    /* ── User Charts Search ─────────────────────────────────────── */
    function wireSearch() {
        var searchInput = document.getElementById('uc-search');
        var noResults = document.getElementById('uc-no-search-results');
        var countBadge = document.getElementById('uc-count-badge');

        if (searchInput && ucList) {
            searchInput.addEventListener('input', function () {
                var query = searchInput.value.toLowerCase().trim();
                var items = ucList.querySelectorAll('.uc-item');
                var visibleCount = 0;
                items.forEach(function (it) {
                    var title = (it.dataset.chartName || '').toLowerCase();
                    var match = !query || title.indexOf(query) !== -1;
                    it.style.display = match ? '' : 'none';
                    if (match) visibleCount++;
                });
                if (noResults) noResults.classList.toggle('hidden', visibleCount > 0);
                if (countBadge) countBadge.textContent = visibleCount + ' chart' + (visibleCount === 1 ? '' : 's');
            });
        }
    }

    function wireDock() {
        if (!dock || !dockTab) return;
        dockTab.addEventListener('click', function (e) {
            e.stopPropagation();
            dock.classList.toggle('open');
        });
        document.addEventListener('click', function (e) {
            if (dock.classList.contains('open') && !dock.contains(e.target)) {
                dock.classList.remove('open');
            }
        });
    }

    function closeDock() {
        if (dock) dock.classList.remove('open');
    }

    /* ── Hover previews ────────────────────────────────────────── */
    var previewEl = document.getElementById('chart-preview');
    var previewBody = document.getElementById('chart-preview-body');
    var previewCache = {};
    var previewTimer = null;
    var activePreviewId = null;

    function loadChartHtml(chartId) {
        if (previewCache[chartId]) return Promise.resolve(previewCache[chartId]);
        return fetch(renderUrl(chartId), { credentials: 'same-origin' })
            .then(function (r) { return r.text(); })
            .then(function (html) { previewCache[chartId] = html; return html; });
    }

    function positionPreview(itemEl) {
        var r = itemEl.getBoundingClientRect();
        var w = previewEl.offsetWidth || 420;
        var h = previewEl.offsetHeight || 260;
        var left = r.left - w - 12;
        var top = r.top;
        if (left < 8 || left + w > window.innerWidth - 8) {
            left = Math.max(8, r.left);
            top = Math.min(r.bottom + 8, window.innerHeight - h - 8);
        }
        if (top < 8) top = 8;
        previewEl.style.left = left + 'px';
        previewEl.style.top = top + 'px';
    }

    function showChartPreview(chartId, itemEl) {
        if (dragState && dragState.active) return;
        clearTimeout(previewTimer);
        if (activePreviewId === chartId && !previewEl.classList.contains('hidden')) return;
        activePreviewId = chartId;
        previewEl.classList.remove('hidden');
        positionPreview(itemEl);
        previewBody.innerHTML = '<div class="p-6 text-xs text-slate-400">Loading preview…</div>';
        loadChartHtml(chartId).then(function (html) {
            if (activePreviewId !== chartId) return;
            previewBody.innerHTML = html;
            positionPreview(itemEl);
            if (window.initAllCharts) window.initAllCharts();
        }).catch(function () {
            if (activePreviewId !== chartId) return;
            previewBody.innerHTML = '<div class="p-4 text-xs text-red-500">Could not load preview.</div>';
        });
    }

    function hideChartPreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(function () {
            if (previewEl) previewEl.classList.add('hidden');
            activePreviewId = null;
        }, 200);
    }

    function wirePreview() {
        if (!ucList) return;
        ucList.addEventListener('mouseover', function (e) {
            if (dragState && dragState.active) return;
            var item = e.target.closest('.uc-item');
            if (item) showChartPreview(item.dataset.chartId, item);
        });
        ucList.addEventListener('mouseleave', hideChartPreview);
        if (previewEl) {
            previewEl.addEventListener('mouseenter', function () { clearTimeout(previewTimer); });
            previewEl.addEventListener('mouseleave', hideChartPreview);
        }
    }

    /* ── Document ready ────────────────────────────────────────── */
    document.addEventListener('DOMContentLoaded', function () {
        initGrid();
        wireExisting();
        wireDrag();
        wireDock();
        wirePreview();
        wireSearch();
    });
})();

