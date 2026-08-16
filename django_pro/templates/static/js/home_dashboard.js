/* Home dashboard — per-user charts grid on the landing page.
 *
 * - Sticky "User Charts" dock on the right edge (collapsed; expands on hover
 *   or click) with per-chart hover previews.
 * - Charts are dragged from the dock into the GridStack grid using a custom
 *   pointer-based drag (reliable placement via grid.getCellFromPixel).
 * - GridStack lets users reorder/resize; layout is persisted on change.
 * - School filter switches the session active school.
 */
(function () {
    'use strict';

    var app = document.getElementById('home-dash-app');
    if (!app) return;

    var dashUrl         = app.dataset.dashboardUrl;
    var renderUrlTpl    = app.dataset.renderUrlTemplate;    // .../999999999/render/
    var switchSchoolUrl = app.dataset.switchSchoolUrl;
    var csrf            = app.dataset.csrf;
    var gridEl          = document.getElementById('home-grid');
    var emptyEl         = document.getElementById('home-grid-empty');

    var grid = null;
    var saveTimer = null;
    var skipNextChange = false;

    /* ── Helpers ─────────────────────────────────────────────── */
    function postJson(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
        }).then(function (r) { return r.json(); });
    }

    function renderUrl(chartId) {
        return renderUrlTpl.replace('999999999', String(chartId)) +
            '?source=dashboard&source_id=home&source_name=' + encodeURIComponent('My Charts');
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function buildWidget(data) {
        var el = document.createElement('div');
        el.className = 'grid-stack-item';
        el.innerHTML =
            '<div class="grid-stack-item-content bg-white rounded-xl shadow-sm ring-1 ring-slate-200 flex flex-col">' +
            '  <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/80 rounded-t-xl">' +
            '    <span class="text-xs font-semibold text-slate-700 truncate" title="' + esc(data.title) + '">' + esc(data.title) + '</span>' +
            '    <button type="button" class="home-slot-remove flex-shrink-0 w-6 h-6 rounded-md hover:bg-red-50 text-slate-400 hover:text-red-600 text-lg leading-none" aria-label="Remove chart">&times;</button>' +
            '  </div>' +
            '  <div class="home-slot-body flex-1 min-h-0 p-2">' +
            (data.html ? data.html : '<div class="text-xs text-red-500 p-2">' + esc(data.error || 'Could not load chart') + '</div>') +
            '  </div>' +
            '</div>';
        return el;
    }

    function addWidgetToGrid(data, opts) {
        opts = opts || {};
        var cfg = { w: data.gs_w || 8, h: data.gs_h || 5 };
        if (opts.autoPosition) {
            cfg.autoPosition = true;
        } else {
            cfg.x = (opts.x != null) ? opts.x : (data.gs_x || 0);
            cfg.y = (opts.y != null) ? opts.y : (data.gs_y || 0);
        }
        cfg.id = data.slot_id;
        var node = grid.addWidget(buildWidget(data), cfg);
        node.setAttribute('gs-id', String(data.slot_id));
        var rm = node.querySelector('.home-slot-remove');
        if (rm) rm.addEventListener('click', function () { removeSlot(node); });
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
        // grid.save(false) is the authoritative source for current x/y/w/h
        // (GridStack 11 keeps these in its engine and writes gs-* attrs).
        var nodes = grid.save(false) || [];
        var layout = nodes.map(function (n, i) {
            return {
                slot_id: n.id,
                gs_x: n.x || 0,
                gs_y: n.y || 0,
                gs_w: n.w || 8,
                gs_h: n.h || 5,
                position: i,
            };
        }).filter(function (l) { return l.slot_id; });
        if (layout.length) postJson(dashUrl, { action: 'reorder', layout: layout });
    }

    function debouncedSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveLayout, 400);
    }

    function removeSlot(node) {
        var slotId = node.getAttribute('gs-id') || node.getAttribute('data-gs-id');
        if (!slotId) {
            // Fallback: match by GridStack widget id
            var nodes = grid.save(false) || [];
            for (var i = 0; i < nodes.length; i++) {
                var el = gridEl.querySelector('[gs-id="' + nodes[i].id + '"]') || gridEl.querySelector('.grid-stack-item:nth-child(' + (i + 1) + ')');
                if (el === node) { slotId = nodes[i].id; break; }
            }
        }
        if (!slotId) { alert('Could not identify this chart.'); return; }
        postJson(dashUrl, { action: 'remove', slot_id: slotId }).then(function (data) {
            if (data.success) {
                skipNextChange = true;
                grid.removeWidget(node, true);
                updateEmpty();
            } else {
                alert(data.error || 'Could not remove chart.');
            }
        });
    }

    function initGrid() {
        grid = GridStack.init({
            column: 12,
            margin: 6,
            cellHeight: 80,
            float: false,
            animate: true,
        }, gridEl);
        grid.on('change', debouncedSave);
    }

    function wireExisting() {
        gridEl.querySelectorAll('.grid-stack-item').forEach(function (node) {
            var rm = node.querySelector('.home-slot-remove');
            if (rm) rm.addEventListener('click', function () { removeSlot(node); });
        });
        updateEmpty();
        if (window.initAllCharts) window.initAllCharts();
    }

    /* ── Custom pointer drag from the dock into the grid ───────── */
    var ucList = document.getElementById('user-charts-list');
    var dock = document.getElementById('uc-dock');
    var dockTab = document.getElementById('uc-tab');

    var dragState = null;

    function cellFromEvent(e) {
        if (!grid) return null;
        try {
            return grid.getCellFromPixel({ clientX: e.clientX, clientY: e.clientY });
        } catch (err) {
            return null;
        }
    }

    function createGhost(name) {
        var g = document.createElement('div');
        g.className = 'fixed z-50 pointer-events-none bg-slate-900 text-white rounded-lg shadow-2xl px-3 py-2 text-sm font-medium flex items-center gap-2';
        g.innerHTML =
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>' +
            '<span>' + esc(name) + '</span>';
        g.style.left = '-9999px';
        g.style.top = '-9999px';
        document.body.appendChild(g);
        return g;
    }

    function finishDrag(e) {
        if (!dragState) return;
        var state = dragState;
        dragState = null;
        document.body.classList.remove('select-none');
        if (state.ghost) state.ghost.remove();

        var rect = gridEl.getBoundingClientRect();
        var overGrid = e.clientX >= rect.left && e.clientX <= rect.right &&
                       e.clientY >= rect.top && e.clientY <= rect.bottom;
        if (!overGrid) { closeDock(); return; }

        var cell = cellFromEvent(e);
        postJson(dashUrl, { action: 'add_chart', chart_id: parseInt(state.id, 10) }).then(function (data) {
            if (!data.success) { alert(data.error || 'Could not add chart.'); return; }
            var slot = data.slot;
            var chart = slot.chart || {};
            fetch(renderUrl(chart.id), { credentials: 'same-origin' })
                .then(function (r) { return r.text(); })
                .then(function (html) {
                    addWidgetToGrid({
                        slot_id: slot.slot_id,
                        title: chart.name || 'Chart',
                        html: html,
                        error: '',
                        gs_w: slot.gs_w,
                        gs_h: slot.gs_h,
                    }, { autoPosition: false, x: cell ? cell.x : undefined, y: cell ? cell.y : undefined });
                    updateEmpty();
                    if (window.initAllCharts) window.initAllCharts();
                });
        });
    }

    function wireDrag() {
        if (!ucList) return;
        ucList.addEventListener('pointerdown', function (e) {
            if (e.button !== 0) return;
            var item = e.target.closest('.uc-item');
            if (!item) return;
            var id = item.dataset.chartId;
            if (!id) return;
            dragState = {
                id: id,
                name: item.dataset.chartName || 'Chart',
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
                if (Math.abs(dx) + Math.abs(dy) < 8) return;
                dragState.active = true;
                dragState.ghost = createGhost(dragState.name);
                document.body.classList.add('select-none');
            }
            var g = dragState.ghost;
            g.style.left = (e.clientX + 12) + 'px';
            g.style.top = (e.clientY + 12) + 'px';
        });

        window.addEventListener('pointerup', finishDrag);
        window.addEventListener('pointercancel', function () {
            if (dragState && dragState.ghost) dragState.ghost.remove();
            dragState = null;
            document.body.classList.remove('select-none');
        });
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
            previewEl.classList.add('hidden');
            activePreviewId = null;
        }, 200);
    }

    function wirePreview() {
        if (!ucList) return;
        ucList.addEventListener('mouseover', function (e) {
            if (dragState) return;
            var item = e.target.closest('.uc-item');
            if (item) showChartPreview(item.dataset.chartId, item);
        });
        ucList.addEventListener('mouseleave', hideChartPreview);
        if (previewEl) {
            previewEl.addEventListener('mouseenter', function () { clearTimeout(previewTimer); });
            previewEl.addEventListener('mouseleave', hideChartPreview);
        }
    }

    /* ── School filter ─────────────────────────────────────────── */
    function wireSchoolFilter() {
        var sel = document.getElementById('home-school-filter');
        if (!sel) return;
        sel.addEventListener('change', function () {
            var fd = new FormData();
            fd.append('school_id', sel.value || 'all');
            fetch(switchSchoolUrl, {
                method: 'POST',
                body: fd,
                headers: { 'X-CSRFToken': csrf },
                credentials: 'same-origin',
            }).then(function () { window.location.reload(); });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        initGrid();
        wireExisting();
        wireDrag();
        wireDock();
        wirePreview();
        wireSchoolFilter();
    });
})();
