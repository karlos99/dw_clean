/**
 * dashboard-builder.js — Alpine.js component + GridStack integration.
 *
 * Expects global constants set by the template:
 *   _DASHBOARD_ID, _SLOTS_JSON, _SAVE_URL, _SLOTS_URL_TPL,
 *   _SLOT_URL_TPL, _CHART_LIST_URL, _CHART_RENDER_TPL, _CSRF,
 *   _DB_NAME, _DB_DESC
 */
function dashboardBuilder() {
  return {
    /* ── State ──────────────────────────────────────────────────── */
    dashboardId: _DASHBOARD_ID,
    name: _DB_NAME,
    description: _DB_DESC,
    slots: _SLOTS_JSON.map(s => ({
      id: s.slot_id || s.id,
      chart_id: s.chart ? s.chart.id : null,
      chart_name: s.chart ? s.chart.name : null,
      element_type: s.element_type || 'chart',
      html_content: s.html_content || '',
      html_preview: (s.html_content || '').replace(/<[^>]*>/g, '').slice(0, 30),
      show_title: s.show_title !== false,
      gs_x: s.gs_x || 0, gs_y: s.gs_y || 0,
      gs_w: s.gs_w || 6, gs_h: s.gs_h || 4,
    })),
    selectedSlot: null,
    gridReady: false,
    grid: null,

    pickerOpen: false,
    pickerSearch: '',

    toast: '',
    toastType: 'success',
    lastSaved: '',

    /* ── Init ───────────────────────────────────────────────────── */
    init() {
      // Global bridge so the chart picker partial can call addChart
      window._dbAddChart = (chartId, chartName) => this.addChart(chartId, chartName);

      this.$nextTick(() => {
        this._initGrid();
        if (this.dashboardId) {
          this._renderExistingSlots();
          this.loadChartList();
        }
      });
      // Watch pickerOpen to load chart list when opened
      this.$watch('pickerOpen', (open) => {
        if (open) this.loadChartList();
      });
    },

    _initGrid() {
      const el = document.getElementById('dashboard-grid');
      if (!el) return;

      this.grid = GridStack.init({
        column: 12,
        cellHeight: 80,
        animate: true,
        margin: 8,
        disableOneColumnMode: true,
        resizable: { handles: 'se,sw,e,w' },
        float: false,
      }, el);
      this.gridReady = true;
    },

    _renderExistingSlots() {
      if (!this.grid) return;
      // Batch-add all widgets so float:false compaction sees them together
      this.grid.batchUpdate(true);
      this.slots.forEach(slot => this._addWidgetToGrid(slot));
      this.grid.batchUpdate(false);
      this.$nextTick(() => {
        const gridEl = document.getElementById('dashboard-grid');
        if (window.htmx && gridEl) htmx.process(gridEl);
      });
    },

    /** Build a real DOM element and add it to GridStack */
    _addWidgetToGrid(slot, opts) {
      const x = opts?.x ?? slot.gs_x;
      const y = opts?.y ?? slot.gs_y;
      const w = opts?.w ?? slot.gs_w;
      const h = opts?.h ?? slot.gs_h;

      const item = document.createElement('div');
      item.classList.add('grid-stack-item');
      item.setAttribute('gs-id', String(slot.id));
      item.setAttribute('gs-x', String(x));
      item.setAttribute('gs-y', String(y));
      item.setAttribute('gs-w', String(w));
      item.setAttribute('gs-h', String(h));

      const content = document.createElement('div');
      content.classList.add('grid-stack-item-content',
        'bg-white', 'border', 'border-slate-200', 'rounded-xl',
        'overflow-hidden', 'shadow-sm', 'flex', 'flex-col');
      content.innerHTML = this._buildSlotHtml(slot);
      item.appendChild(content);

      this.grid.addWidget(item);
      return item;
    },

    _buildSlotHtml(slot) {
      if (slot.element_type === 'html') {
        return `<div class="p-4 text-sm text-slate-600">${slot.html_content || '<p>Text block</p>'}</div>`;
      }
      const dbId = this.dashboardId;
      const dbName = encodeURIComponent(this.name);
      const chartId = slot.chart_id;
      const renderUrl = _CHART_RENDER_TPL.replace('CHARTID', chartId)
        + `?source=dashboard&source_id=${dbId}&source_name=${dbName}`;
      const titleBar = slot.show_title !== false
        ? `<div class="flex items-center justify-between px-3 py-1.5 border-b border-slate-100 bg-slate-50/50">
             <span class="text-xs font-semibold text-slate-600 truncate">${this._esc(slot.chart_name || 'Chart')}</span>
             <a href="/dataviz/charts/${chartId}/edit/" class="text-[10px] text-blue-500 hover:underline" title="Edit chart">edit</a>
           </div>`
        : '';
      return `${titleBar}
        <div class="flex-1 min-h-0"
             hx-get="${renderUrl}"
             hx-trigger="load"
             hx-swap="innerHTML">
          <div class="flex items-center justify-center h-full text-slate-300 text-xs">Loading…</div>
        </div>`;
    },

    _esc(s) {
      const d = document.createElement('div');
      d.textContent = s || '';
      return d.innerHTML;
    },

    /* ── Dashboard CRUD ─────────────────────────────────────────── */
    async _ensureDashboard() {
      if (this.dashboardId) return this.dashboardId;
      if (!this.name.trim()) {
        this.showToast('Enter a dashboard name first', 'error');
        return null;
      }
      const res = await this._post(_SAVE_URL, { name: this.name.trim(), description: this.description });
      if (res.error) { this.showToast(res.error, 'error'); return null; }
      this.dashboardId = res.dashboard.id;
      // Update URL without reload
      history.replaceState(null, '', `/dataviz/dashboards/${this.dashboardId}/edit/`);
      this._initGrid();
      return this.dashboardId;
    },

    async autoSave() {
      if (!this.dashboardId || !this.name.trim()) return;
      await this._post(_SAVE_URL, {
        id: this.dashboardId,
        name: this.name.trim(),
        description: this.description,
      });
      this.lastSaved = new Date().toLocaleTimeString();
    },

    async saveLayout() {
      const dbId = await this._ensureDashboard();
      if (!dbId) return;

      // Save name/description
      await this._post(_SAVE_URL, {
        id: dbId,
        name: this.name.trim(),
        description: this.description,
      });

      // Save GridStack layout
      if (this.grid && this.slots.length > 0) {
        const items = this.grid.save(false) || [];
        const layout = items.map(item => ({
          slot_id: parseInt(item.id, 10),
          gs_x: item.x ?? 0, gs_y: item.y ?? 0,
          gs_w: item.w ?? 6, gs_h: item.h ?? 4,
        })).filter(i => i.slot_id);

        // Sort by visual position (top-to-bottom, left-to-right) for stable ordering
        layout.sort((a, b) => a.gs_y - b.gs_y || a.gs_x - b.gs_x);
        layout.forEach((item, idx) => item.position = idx);

        if (layout.length > 0) {
          const url = _SLOTS_URL_TPL.replace('DBID', dbId);
          await this._post(url, { action: 'reorder', layout });
        }

        // Sync local slot state
        layout.forEach(l => {
          const s = this.slots.find(s => s.id === l.slot_id);
          if (s) { s.gs_x = l.gs_x; s.gs_y = l.gs_y; s.gs_w = l.gs_w; s.gs_h = l.gs_h; }
        });
      }

      this.lastSaved = new Date().toLocaleTimeString();
      this.showToast('Dashboard saved');
    },

    /* ── Chart picker ───────────────────────────────────────────── */
    async loadChartList() {
      const body = document.getElementById('chart-picker-body');
      if (!body) return;
      const q = this.pickerSearch.trim();
      const url = q ? `${_CHART_LIST_URL}?q=${encodeURIComponent(q)}` : _CHART_LIST_URL;
      try {
        const res = await fetch(url, { credentials: 'same-origin' });
        body.innerHTML = await res.text();
      } catch (e) {
        body.innerHTML = '<p class="text-xs text-red-500 text-center py-4">Failed to load charts</p>';
      }
    },

    /* ── Add chart to dashboard ─────────────────────────────────── */
    async addChart(chartId, chartName) {
      const dbId = await this._ensureDashboard();
      if (!dbId) return;

      const url = _SLOTS_URL_TPL.replace('DBID', dbId);
      const res = await this._post(url, { action: 'add', chart_id: chartId, gs_w: 6, gs_h: 4 });
      if (res.error) { this.showToast(res.error, 'error'); return; }

      const slot = {
        id: res.slot.slot_id || res.slot.id,
        chart_id: chartId,
        chart_name: chartName,
        element_type: 'chart',
        html_content: '',
        html_preview: '',
        show_title: true,
        gs_x: res.slot.gs_x || 0, gs_y: res.slot.gs_y || 0,
        gs_w: res.slot.gs_w || 6, gs_h: res.slot.gs_h || 4,
      };
      this.slots.push(slot);

      // Add to GridStack
      if (this.grid) {
        this._addWidgetToGrid(slot);
        this.$nextTick(() => {
          if (window.htmx) htmx.process(document.getElementById('dashboard-grid'));
        });
      }

      this.pickerOpen = false;
      this.showToast(`Added: ${chartName}`);
    },

    /* ── Add HTML text block ────────────────────────────────────── */
    async addHtmlBlock() {
      const dbId = await this._ensureDashboard();
      if (!dbId) return;

      const url = _SLOTS_URL_TPL.replace('DBID', dbId);
      const res = await this._post(url, {
        action: 'add_html',
        html_content: '<h3>Title</h3><p>Description text…</p>',
        gs_w: 12, gs_h: 2,
      });
      if (res.error) { this.showToast(res.error, 'error'); return; }

      const slot = {
        id: res.slot.slot_id || res.slot.id,
        chart_id: null,
        chart_name: null,
        element_type: 'html',
        html_content: '<h3>Title</h3><p>Description text…</p>',
        html_preview: 'Title Description text…',
        show_title: false,
        gs_x: 0, gs_y: 0,
        gs_w: 12, gs_h: 2,
      };
      this.slots.push(slot);

      if (this.grid) {
        this._addWidgetToGrid(slot);
      }
      this.showToast('Text block added');
    },

    /* ── Remove slot ────────────────────────────────────────────── */
    async removeSlot(slotId) {
      const url = _SLOT_URL_TPL.replace('SLOTID', slotId);
      await fetch(url, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': _CSRF },
      });

      // Remove from grid
      if (this.grid) {
        const el = document.querySelector(`[gs-id="${slotId}"]`);
        if (el) this.grid.removeWidget(el, false);
      }

      this.slots = this.slots.filter(s => s.id !== slotId);
      if (this.selectedSlot === slotId) this.selectedSlot = null;
    },

    /* ── Slot helpers ───────────────────────────────────────────── */
    focusSlot(slotId) {
      this.selectedSlot = this.selectedSlot === slotId ? null : slotId;
    },

    getSlot(slotId) {
      return this.slots.find(s => s.id === slotId) || null;
    },

    async updateSlotProp(slotId, prop, value) {
      const slot = this.getSlot(slotId);
      if (slot) {
        slot[prop] = value;
        if (prop === 'show_title') {
          this._rerenderSlot(slot);
          this.$nextTick(() => {
            if (window.htmx) htmx.process(document.getElementById('dashboard-grid'));
          });
        }
      }
      const url = _SLOT_URL_TPL.replace('SLOTID', slotId);
      await this._post(url, { [prop]: value });
    },

    async updateHtmlContent(slotId, content) {
      const slot = this.getSlot(slotId);
      if (!slot) return;
      slot.html_content = content;
      slot.html_preview = content.replace(/<[^>]*>/g, '').slice(0, 30);
      this._rerenderSlot(slot);
      const url = _SLOT_URL_TPL.replace('SLOTID', slotId);
      await this._post(url, { html_content: content });
    },

    _rerenderSlot(slot) {
      const el = document.querySelector(`[gs-id="${slot.id}"] .grid-stack-item-content`);
      if (el) el.innerHTML = this._buildSlotHtml(slot);
    },

    /* ── Toast ──────────────────────────────────────────────────── */
    showToast(msg, type = 'success') {
      this.toast = msg;
      this.toastType = type;
      setTimeout(() => { this.toast = ''; }, 3000);
    },

    /* ── Fetch helper ───────────────────────────────────────────── */
    async _post(url, body) {
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _CSRF },
          body: JSON.stringify(body),
        });
        return await res.json();
      } catch (e) {
        return { error: e.message };
      }
    },
  };
}
