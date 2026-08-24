/**
 * page-builder.js — Alpine.js component for the row-based page builder.
 *
 * Globals set by the template:
 *   _PAGE_ID, _PAGE_SLUG, _PAGE_TITLE, _PAGE_DESC, _PAGE_STATUS,
 *   _PAGE_FILTERS, _ROWS_JSON, _SECTIONS_JSON, _SAVE_URL, _SECTIONS_URL,
 *   _SECTION_URL_TPL, _ROW_URL_TPL, _CHART_LIST_URL, _CHART_RENDER_TPL,
 *   _FILTER_COLS_URL, _CSRF
 */
function pageBuilder() {
  return {
    /* ── State ──────────────────────────────────────────────────── */
    pageId:   _PAGE_ID,
    pageSlug: _PAGE_SLUG,
    title:    _PAGE_TITLE,
    description: _PAGE_DESC,
    status:   _PAGE_STATUS,
    containerWidth: (typeof _PAGE_CONTAINER_WIDTH !== 'undefined' ? _PAGE_CONTAINER_WIDTH : 'xl') || 'xl',
    showHeader: typeof _PAGE_SHOW_HEADER !== 'undefined' ? _PAGE_SHOW_HEADER : true,
    filters:  (_PAGE_FILTERS || []).map(f => ({ ...f, hidden: !!f.hidden })),
    filtersEnabled: (_PAGE_FILTERS || []).length > 0,
    filterCategories: [],

    /* Sidebar accordion open/closed state */
    sidebarSections: { settings: true, filters: true, tabs: true, layout: true },
    tabs: (_TABS_JSON || []).map(t => ({ ...t })),
    newTabName: '',

    /* Filter modal */
    filterModalOpen: false,
    filterModalMode: 'user', // 'user' or 'page'
    filterColumnSearch: '',
    filterOptionsMap: {},

    /* Text editor modal */
    htmlEditorMode: 'visual',
    editorModalOpen: false,
    editorSectionId: null,

    /* Saving indicator */
    saving: false,

    /* Section drag-to-reorder */
    dragSectionId: null,
    dragOverSectionId: null,
    dragSourceRowId: null,

    /* Row drag-to-reorder */
    dragRowId: null,
    dragOverRowId: null,

    /* Editor selection save (for toolbar controls that steal focus) */
    _savedSel: null,

    rows: _ROWS_JSON.map(r => ({
      id:        r.id,
      label:     r.label || '',
      position:  r.position ?? 0,
      bg_color:  r.bg_color || '',
      collapsed: r.collapsed || false,
      col_guide: r.col_guide || 0,
      tab_id: r.tab_id || null,
      sections:  (r.sections || []).map(s => _mapSection(s)),
    })),

    activeRow: null,
    selectedSection: null,
    grids: {},
    pickerOpen: false,
    pickerSearch: '',
    pickerTargetRow: null,

    toast: '',
    toastType: 'success',
    lastSaved: '',

    /* ── Computed: filtered categories for filter modal ─────────── */
    get filteredFilterCategories() {
      const q = (this.filterColumnSearch || '').toLowerCase().trim();
      if (!q) return this.filterCategories;
      return this.filterCategories
        .map(cat => ({
          ...cat,
          columns: cat.columns.filter(col =>
            (col.label || '').toLowerCase().includes(q) ||
            (col.key  || '').toLowerCase().includes(q)
          ),
        }))
        .filter(cat => cat.columns.length > 0);
    },

    /* ── Init ───────────────────────────────────────────────────── */
    init() {
      window._dbAddChart = (chartId, chartName) => {
        if (this.pickerTargetRow) {
          this._addChartToRow(this.pickerTargetRow, chartId, chartName);
        }
      };

      // Event delegation for widget title input and show_title toggle on the canvas
      document.addEventListener('change', (e) => {
        if (e.target.matches('.widget-title-toggle')) {
          const secId = parseInt(e.target.dataset.secId, 10);
          const showTitle = e.target.checked;
          this.updateSectionProp(secId, 'show_title', showTitle);
        }
        if (e.target.matches('.widget-title-input')) {
          const secId = parseInt(e.target.dataset.secId, 10);
          const newName = e.target.value.trim();
          this.updateSectionProp(secId, 'chart_name', newName);
        }
      });
      document.addEventListener('keydown', (e) => {
        if (e.target.matches('.widget-title-input') && e.key === 'Enter') {
          e.target.blur();
        }
      });

      // Inline canvas controls — edit and delete sections directly on canvas
      document.addEventListener('click', (e) => {
        const editBtn = e.target.closest('[data-action="edit-section"]');
        const deleteBtn = e.target.closest('[data-action="delete-section"]');
        if (editBtn) {
          e.stopPropagation();
          const secId = parseInt(editBtn.dataset.secId, 10);
          const row = this.rows.find(r => r.sections.some(s => s.id === secId));
          if (row) {
            this.activeRow = row.id;
            this.selectedSection = secId;
            // Scroll sidebar into view on mobile
            const sidebar = document.querySelector('#page-builder-sidebar');
            if (sidebar && window.innerWidth < 1024) {
              sidebar.scrollIntoView({ behavior: 'smooth' });
            }
          }
        }
        if (deleteBtn) {
          e.stopPropagation();
          const secId = parseInt(deleteBtn.dataset.secId, 10);
          const row = this.rows.find(r => r.sections.some(s => s.id === secId));
          if (row && confirm('Delete this section? This cannot be undone.')) {
            this.removeSection(row.id, secId);
          }
        }
      });

      // Double-click on grid items to edit
      document.addEventListener('dblclick', (e) => {
        const gridItem = e.target.closest('.grid-stack-item');
        if (!gridItem) return;
        const gsId = gridItem.getAttribute('gs-id');
        if (!gsId) return;
        const secId = parseInt(gsId, 10);
        if (isNaN(secId)) return;
        const row = this.rows.find(r => r.sections.some(s => s.id === secId));
        if (row) {
          this.activeRow = row.id;
          this.selectedSection = secId;
          if (row.sections.find(s => s.id === secId)?.element_type === 'html') {
            this.openEditorModal(secId);
          }
        }
      });

      this.$nextTick(() => {
        if (this.pageId) {
          this._initAllGrids();
          this.loadChartList();
        }
      });
      this.$watch('pickerOpen', open => {
        if (open) this.loadChartList();
      });

      // Load available filter columns and options
      this.loadFilterColumns();
      this.filters.forEach(f => {
        if (f.source_col) {
          this.loadFilterOptions(f.source_col);
        }
      });
    },

    /* ── Grid management ────────────────────────────────────────── */
    _initAllGrids() {
      this.rows.forEach(row => this._initRowGrid(row));
    },

    _initRowGrid(row) {
      this.$nextTick(() => {
        const el = document.getElementById('row-grid-' + row.id);
        if (!el || this.grids[row.id]) return;

        const grid = GridStack.init({
          column: 12,
          cellHeight: 80,
          animate: true,
          margin: 8,
          acceptWidgets: true,
          disableOneColumnMode: true,
          resizable: { handles: 'se,s,sw,e,w,n,nw,ne' },
          float: false,
        }, el);
        this.grids[row.id] = grid;

        // Auto-persist layout changes (resize or drag) immediately
        grid.on('resizestop dragstop', (_event, gsEl) => {
          const secId = parseInt(gsEl.getAttribute('gs-id') || '0', 10);
          if (!secId) return;
          const node = gsEl.gridstackNode || {};
          const sec = this.getSection(secId);
          if (sec) {
            sec.gs_x = node.x ?? sec.gs_x;
            sec.gs_y = node.y ?? sec.gs_y;
            sec.gs_w = node.w ?? sec.gs_w;
            sec.gs_h = node.h ?? sec.gs_h;
          }
          const url = _SECTION_URL_TPL.replace('SECTIONID', String(secId));
          this._post(url, {
            gs_x: node.x, gs_y: node.y, gs_w: node.w, gs_h: node.h,
          });
          // Ensure ECharts fills the new cell size after animation completes
          const chartEl = gsEl.querySelector('.chart-container');
          if (chartEl && chartEl._echartsInstance) {
            chartEl._echartsInstance.resize();
          }
        });

        if (row.sections.length > 0) {
          grid.batchUpdate(true);
          row.sections.forEach(sec => this._addWidgetToGrid(row.id, sec));
          grid.batchUpdate(false);
          this.$nextTick(() => {
            if (window.htmx) htmx.process(el);
          });
        }
      });
    },

    _addWidgetToGrid(rowId, sec, opts) {
      const grid = this.grids[rowId];
      if (!grid) return;

      const x = opts?.x ?? sec.gs_x;
      const y = opts?.y ?? sec.gs_y;
      const w = opts?.w ?? sec.gs_w;
      const h = opts?.h ?? sec.gs_h;

      const item = document.createElement('div');
      item.classList.add('grid-stack-item');
      item.setAttribute('gs-id', String(sec.id));
      item.setAttribute('gs-x', String(x));
      item.setAttribute('gs-y', String(y));
      item.setAttribute('gs-w', String(w));
      item.setAttribute('gs-h', String(h));

      const content = document.createElement('div');
      // HTML sections need overflow-y scroll; charts clip content
      const overflowCls = sec.element_type === 'html' ? 'pb-html-block' : 'overflow-hidden';
      content.classList.add('grid-stack-item-content',
        'bg-white', 'border', 'border-slate-200', 'rounded-xl',
        overflowCls, 'shadow-sm', 'flex', 'flex-col');
      content.innerHTML = this._buildSectionHtml(sec);
      item.appendChild(content);

      grid.addWidget(item);

      // Read finalized coordinates assigned by GridStack and save them immediately
      this.$nextTick(() => {
        const node = item.gridstackNode || {};
        const newX = node.x ?? sec.gs_x;
        const newY = node.y ?? sec.gs_y;
        const newW = node.w ?? sec.gs_w;
        const newH = node.h ?? sec.gs_h;

        if (newX !== sec.gs_x || newY !== sec.gs_y || newW !== sec.gs_w || newH !== sec.gs_h) {
          sec.gs_x = newX;
          sec.gs_y = newY;
          sec.gs_w = newW;
          sec.gs_h = newH;
          const url = _SECTION_URL_TPL.replace('SECTIONID', String(sec.id));
          this._post(url, { gs_x: newX, gs_y: newY, gs_w: newW, gs_h: newH });
        }
      });

      // Only auto-fit when the saved value explicitly requests it. Running
      // this for every HTML block overwrites fixed heights on page reload.
      if (sec.element_type === 'html' && Number(sec.gs_h) === 0) {
        this.$nextTick(() => this._fitHtmlWidget(grid, item));
      }

      return item;
    },

    _fitHtmlWidget(grid, item) {
      const secId = parseInt(item.getAttribute('gs-id') || '0', 10);
      const saveHeight = (h) => {
        if (!secId || !h) return;
        const sec = this.getSection(secId);
        if (sec) sec.gs_h = h;
        this._post(_SECTION_URL_TPL.replace('SECTIONID', String(secId)), { gs_h: h });
      };

      const inner = item.querySelector('.grid-stack-item-content > div');
      if (!inner) return;
      const cellPx = grid.getCellHeight(true) || 80;
      const needed = Math.max(1, Math.ceil(inner.scrollHeight / cellPx));
      const current = parseInt(item.getAttribute('gs-h') || item.getAttribute('data-gs-h') || '2');
      if (needed !== current) {
        grid.update(item, { h: needed });
      }
      saveHeight(needed);
    },

    _buildSectionHtml(sec) {
      // Inline controls overlay (edit + delete) — shown on hover
      const controlsHtml = `
        <div class="gs-inline-controls">
          <button class="gs-ctrl-btn gs-ctrl-edit" data-action="edit-section" data-sec-id="${sec.id}" title="Edit section">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
          </button>
          <button class="gs-ctrl-btn gs-ctrl-delete" data-action="delete-section" data-sec-id="${sec.id}" title="Delete section">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>`;

      if (sec.element_type === 'html') {
        const placeholder = '<p class="text-slate-400 italic">Empty text block — click Edit Content to add text</p>';
        const content = sec.html_content || placeholder;
        const hasPadWrapper = content.includes('data-pb-p=') || content.includes('data-pb-center=');
        const padClass = hasPadWrapper ? '' : 'p-4 ';
        const centerStyle = sec.center_content
          ? 'display:flex;align-items:center;justify-content:center;height:100%;'
          : 'height:100%;';
        return controlsHtml
          + `<div class="${padClass}text-sm text-slate-700 leading-relaxed overflow-y-auto html-prose" style="${centerStyle}">${content}</div>`;
      }
      const pageSlug = encodeURIComponent(this.pageSlug);
      const chartId = sec.chart_id;
      const qs = this.buildFilterQS();
      const renderUrl = _CHART_RENDER_TPL.replace('CHARTID', chartId)
        + '?source=page&source_id=' + encodeURIComponent(this.pageId) + '&source_name=' + pageSlug
        + (qs ? '&' + qs : '');
      const isChecked = sec.show_title !== false ? 'checked' : '';
      const titleOpacity = sec.show_title !== false ? 'opacity-100' : 'opacity-60 bg-slate-100/70';
      const titleBar = `
        <div class="flex items-center justify-between px-3 py-1 border-b border-slate-100 bg-slate-50/50 ${titleOpacity} transition-opacity select-none">
          <div class="flex items-center gap-1.5 flex-1 min-w-0">
            <span class="text-[9px] font-bold text-slate-400 uppercase select-none mr-1">Title:</span>
            <input type="text" value="${this._esc(sec.chart_name || '')}" 
                   placeholder="Chart Title..."
                   class="widget-title-input text-xs font-semibold text-slate-700 bg-transparent hover:bg-white/80 focus:bg-white border border-transparent hover:border-slate-200 focus:border-slate-300 rounded px-1.5 py-0.5 w-full max-w-[240px] focus:ring-0 transition"
                   data-sec-id="${sec.id}">
          </div>
          <div class="flex items-center gap-3">
            <label class="flex items-center gap-1 cursor-pointer text-[10px] text-slate-500 select-none">
              <input type="checkbox" ${isChecked} 
                     class="widget-title-toggle rounded border-slate-300 text-blue-600 focus:ring-blue-500 w-3 h-3"
                     data-sec-id="${sec.id}">
              <span>Visible on Page</span>
            </label>
            <a href="/dataviz/charts/${chartId}/edit/" target="_blank" class="text-[10px] text-blue-500 hover:underline inline-flex items-center gap-0.5">
              <span>Edit Chart</span>
              <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
            </a>
          </div>
        </div>
      `;
      return controlsHtml + titleBar
        + '<div id="builder-chart-slot-' + sec.id + '" class="flex-1 min-h-0 page-chart-slot" hx-get="' + renderUrl + '" hx-trigger="intersect once" hx-target="#builder-chart-slot-' + sec.id + '" hx-swap="innerHTML">'
        + '<div class="flex items-center justify-center h-full text-slate-300 text-xs">'
        + '<span class="inline-block w-3 h-3 border border-slate-300 border-t-blue-500 rounded-full animate-spin mr-1.5"></span>Loading\u2026'
        + '</div></div>';
    },

    _esc(s) {
      const d = document.createElement('div');
      d.textContent = s || '';
      return d.innerHTML;
    },

    /* ── Page CRUD ──────────────────────────────────────────────── */
    _sectionsUrl() {
      return _SECTIONS_URL.replace('__slug__', this.pageSlug);
    },

    async _ensurePage() {
      if (this.pageId) return this.pageId;
      if (!this.title.trim()) {
        this.showToast('Enter a page title first', 'error');
        return null;
      }
      const res = await this._post(_SAVE_URL, {
        title: this.title.trim(),
        description: this.description,
        status: 'draft',
        container_width: this.containerWidth,
        show_header: this.showHeader,
        page_filters: this._collectFilters(),
      });
      if (res.error) { this.showToast(res.error, 'error'); return null; }
      this.pageId = res.page.id;
      this.pageSlug = res.page.slug;
      history.replaceState(null, '', '/dataviz/pages/' + this.pageSlug + '/edit/');
      return this.pageId;
    },

    _collectFilters() {
      if (!this.filtersEnabled) return [];
      return this.filters
        .filter(f => f.source_col)
        .map(f => ({
          id: f.id, label: f.label,
          source_col: f.source_col,
          source_table: f.source_table || 'current_student',
          widget: f.widget || 'select',
          default_val: f.default_val || '',
          hidden: !!f.hidden,
        }));
    },

    buildFilterQS() {
      if (!this.filtersEnabled) return '';
      const params = new URLSearchParams();
      for (const f of this.filters) {
        const val = typeof f.default_val === 'string' ? f.default_val.trim() : (f.default_val || '');
        if (val) {
          params.append('pf_' + f.source_col, val);
        }
      }
      return params.toString();
    },

    refreshCharts() {
      const qs = this.buildFilterQS();
      document.querySelectorAll('.page-chart-slot').forEach(el => {
        const hxGet = el.getAttribute('hx-get');
        if (!hxGet) return;
        try {
          // Parse hx-get URL and clean up any existing pf_ keys
          const url = new URL(hxGet, window.location.origin);
          const newParams = new URLSearchParams();
          for (const [key, val] of url.searchParams.entries()) {
            if (!key.startsWith('pf_')) {
              newParams.append(key, val);
            }
          }
          const filterParams = new URLSearchParams(qs);
          for (const [key, val] of filterParams.entries()) {
            newParams.append(key, val);
          }
          url.search = newParams.toString();
          
          el.setAttribute('hx-get', url.pathname + url.search);
          el.innerHTML = '<div class="flex items-center justify-center h-full text-slate-300 text-xs">'
            + '<span class="inline-block w-3 h-3 border border-slate-300 border-t-blue-500 rounded-full animate-spin mr-1.5"></span>Refreshing…'
            + '</div>';
          htmx.process(el);
          htmx.trigger(el, 'reload');
        } catch (e) {
          console.error(e);
        }
      });
    },

    async autoSave() {
      if (!this.pageId || !this.title.trim()) return;
      this.saving = true;
      await this._post(_SAVE_URL, {
        id: this.pageId,
        title: this.title.trim(),
        slug: this.pageSlug,
        description: this.description,
        status: this.status,
        container_width: this.containerWidth,
        show_header: this.showHeader,
        page_filters: this._collectFilters(),
      });
      this.lastSaved = new Date().toLocaleTimeString();
      this.saving = false;
      this.refreshCharts();
    },

    async savePage(status) {
      const pageId = await this._ensurePage();
      if (!pageId) return;

      this.saving = true;
      const res = await this._post(_SAVE_URL, {
        id: pageId,
        title: this.title.trim(),
        slug: this.pageSlug,
        description: this.description,
        status: status || this.status,
        container_width: this.containerWidth,
        show_header: this.showHeader,
        page_filters: this._collectFilters(),
      });
      if (res.error) { this.showToast(res.error, 'error'); this.saving = false; return; }
      if (res.page) {
        this.pageSlug = res.page.slug;
        this.status = res.page.status;
      }

      // Save row + grid layouts
      const rowsPayload = this.rows.map((row, ri) => {
        const grid = this.grids[row.id];
        let sectionLayout = [];

        if (grid) {
          const el = document.getElementById('row-grid-' + row.id);
          const items = el ? el.querySelectorAll('[gs-id]') : [];
          sectionLayout = Array.from(items).map(item => {
            const secId = parseInt(item.getAttribute('gs-id'), 10);
            const node = item.gridstackNode || {};
            return {
              section_id: secId,
              gs_x: node.x ?? parseInt(item.getAttribute('gs-x'), 10) ?? 0,
              gs_y: node.y ?? parseInt(item.getAttribute('gs-y'), 10) ?? 0,
              gs_w: node.w ?? parseInt(item.getAttribute('gs-w'), 10) ?? 6,
              gs_h: node.h ?? parseInt(item.getAttribute('gs-h'), 10) ?? 4,
            };
          }).filter(i => i.section_id);
          sectionLayout.sort((a, b) => a.gs_y - b.gs_y || a.gs_x - b.gs_x);
          sectionLayout.forEach((item, idx) => item.position = idx);
        }

        return {
          row_id: row.id,
          position: ri,
          tab_id: row.tab_id || null,
          sections: sectionLayout,
        };
      });

      const url = this._sectionsUrl();
      const reorderRes = await this._post(url, { action: 'reorder', rows: rowsPayload });

      // Sync local state
      const allSections = [];
      this.rows.forEach(r => allSections.push(...r.sections));
      rowsPayload.forEach(rp => {
        const row = this.rows.find(r => r.id === rp.row_id);
        if (!row) return;
        row.sections = rp.sections.map(l => {
          const s = allSections.find(sec => sec.id === l.section_id);
          if (s) {
            s.row_id = rp.row_id;
            s.gs_x = l.gs_x;
            s.gs_y = l.gs_y;
            s.gs_w = l.gs_w;
            s.gs_h = l.gs_h;
            return s;
          }
          return null;
        }).filter(Boolean);
      });

      this.lastSaved = new Date().toLocaleTimeString();
      this.saving = false;
      this.showToast(status === 'active' ? 'Page published' : 'Page saved');
    },

    /* ── Row management ─────────────────────────────────────────── */
    async addRow() {
      const pageId = await this._ensurePage();
      if (!pageId) return;

      const url = this._sectionsUrl();
      const res = await this._post(url, { action: 'add_row' });
      if (res.error) { this.showToast(res.error, 'error'); return; }

      const row = {
        id: res.row.id,
        label: '',
        position: this.rows.length,
        bg_color: '',
        collapsed: false,
        col_guide: 0,
        tab_id: null,
        sections: [],
      };
      this.rows.push(row);
      this.activeRow = row.id;
      this.$nextTick(() => this._initRowGrid(row));
      this.showToast('Section added');
    },

    async addTab() {
      const name = (this.newTabName || '').trim();
      if (!name) return;
      const pageId = await this._ensurePage();
      if (!pageId) return;
      const res = await this._post(this._sectionsUrl(), { action: 'add_tab', name });
      if (res.error) { this.showToast(res.error, 'error'); return; }
      this.tabs.push(res.tab);
      this.newTabName = '';
      this.showToast('Tab added');
    },

    async updateTab(tab) {
      const res = await this._post(this._sectionsUrl(), { action: 'update_tab', tab_id: tab.id, name: tab.name, position: tab.position });
      if (res.error) this.showToast(res.error, 'error');
    },

    async removeTab(tabId) {
      if (!confirm('Delete this tab? Rows will become unassigned, not deleted.')) return;
      const res = await this._post(this._sectionsUrl(), { action: 'delete_tab', tab_id: tabId });
      if (res.error) { this.showToast(res.error, 'error'); return; }
      this.tabs = this.tabs.filter(t => t.id !== tabId);
      this.rows.forEach(row => { if (row.tab_id === tabId) row.tab_id = null; });
    },

    async assignRowTab(row, tabId) {
      row.tab_id = tabId ? parseInt(tabId, 10) : null;
      const res = await this._post(_ROW_URL_TPL.replace('ROWID', row.id), { tab_id: row.tab_id });
      if (res.error) this.showToast(res.error, 'error');
    },

    async removeRow(rowId) {
      const url = this._sectionsUrl();
      await this._post(url, { action: 'remove_row', row_id: rowId });

      if (this.grids[rowId]) {
        this.grids[rowId].destroy(false);
        delete this.grids[rowId];
      }

      this.rows = this.rows.filter(r => r.id !== rowId);
      if (this.activeRow === rowId) this.activeRow = null;
      this.selectedSection = null;
    },

    getRow(rowId) {
      return this.rows.find(r => r.id === rowId) || null;
    },

    async updateRowProp(rowId, prop, value) {
      const row = this.getRow(rowId);
      if (row) row[prop] = value;
      const url = _ROW_URL_TPL.replace('ROWID', rowId);
      await this._post(url, { [prop]: value });
    },

    colGuideStyle(n) {
      if (!n) return '';
      const pct = (100 / n).toFixed(6) + '%';
      return `background-image: repeating-linear-gradient(90deg, transparent 0, transparent calc(${pct} - 1px), rgba(99,102,241,0.18) calc(${pct} - 1px), rgba(99,102,241,0.18) ${pct});`;
    },

    toggleRowCollapsed(rowId) {
      const row = this.getRow(rowId);
      if (!row) return;
      row.collapsed = !row.collapsed;
      this.updateRowProp(rowId, 'collapsed', row.collapsed);
      if (!row.collapsed) {
        // After expanding, let Alpine finish painting then re-layout the grid
        // and trigger any pending HTMX chart loads (IntersectionObserver may
        // have missed them while the row was hidden).
        this.$nextTick(() => {
          const grid = this.grids[rowId];
          if (grid) {
            try { grid.onParentResize(); } catch (_) {}
          }
          const el = document.getElementById('row-grid-' + rowId);
          if (el && window.htmx) htmx.process(el);
          // Re-fire resize on any already-initialized charts in this row
          el && el.querySelectorAll('.chart-container').forEach(c => {
            if (c._echartsInstance) {
              requestAnimationFrame(() => c._echartsInstance.resize());
            }
          });
        });
      }
    },

    /* ── Add chart to row (via picker) ──────────────────────────── */
    addChartToRow(rowId) {
      this.pickerTargetRow = rowId;
      this.pickerOpen = true;
    },

    async _addChartToRow(rowId, chartId, chartName) {
      const pageId = await this._ensurePage();
      if (!pageId) return;

      const url = this._sectionsUrl();
      const res = await this._post(url, {
        action: 'add_chart', row_id: rowId,
        chart_id: chartId, gs_w: 6, gs_h: 4,
      });
      if (res.error) { this.showToast(res.error, 'error'); return; }

      const sec = {
        id: res.section.id,
        row_id: rowId,
        chart_id: chartId,
        chart_name: chartName,
        element_type: 'chart',
        html_content: '',
        html_preview: '',
        show_title: true,
        gs_x: res.section.gs_x ?? 0, gs_y: res.section.gs_y ?? 0,
        gs_w: res.section.gs_w ?? 6, gs_h: res.section.gs_h ?? 4,
      };

      const row = this.getRow(rowId);
      if (row) row.sections.push(sec);

      this._addWidgetToGrid(rowId, sec);
      this.$nextTick(() => {
        const el = document.getElementById('row-grid-' + rowId);
        if (window.htmx && el) htmx.process(el);
      });

      this.pickerOpen = false;
      this.showToast('Added: ' + chartName);
    },

    /* ── Add HTML text block to row ─────────────────────────────── */
    async addHtmlToRow(rowId) {
      const pageId = await this._ensurePage();
      if (!pageId) return;

      const url = this._sectionsUrl();
      const res = await this._post(url, {
        action: 'add_html', row_id: rowId,
        html_content: '<p>Add your text here…</p>',
        gs_w: 12, gs_h: 2,
      });
      if (res.error) { this.showToast(res.error, 'error'); return; }

      const sec = {
        id: res.section.id,
        row_id: rowId,
        chart_id: null,
        chart_name: null,
        element_type: 'html',
        html_content: '<p>Add your text here…</p>',
        html_preview: 'Add your text here…',
        show_title: false,
        gs_x: 0, gs_y: 0,
        gs_w: 12, gs_h: 2,
      };

      const row = this.getRow(rowId);
      if (row) row.sections.push(sec);

      this._addWidgetToGrid(rowId, sec);
      this.showToast('Text block added');
    },

    /* ── Add title block to row ─────────────────────────────────── */
    async addTitleToRow(rowId) {
      const pageId = await this._ensurePage();
      if (!pageId) return;

      const titleHtml = '<h2 style="font-size:1.4rem;font-weight:700;color:#1e293b;margin:0;line-height:1.3">Section Title</h2>';

      const url = this._sectionsUrl();
      const res = await this._post(url, {
        action: 'add_html', row_id: rowId,
        html_content: titleHtml,
        gs_w: 12, gs_h: 1,
      });
      if (res.error) { this.showToast(res.error, 'error'); return; }

      const sec = {
        id: res.section.id,
        row_id: rowId,
        chart_id: null,
        chart_name: null,
        element_type: 'html',
        html_content: titleHtml,
        html_preview: 'Section Title',
        show_title: false,
        gs_x: 0, gs_y: 0,
        gs_w: 12, gs_h: 1,
      };

      const row = this.getRow(rowId);
      if (row) row.sections.push(sec);

      this._addWidgetToGrid(rowId, sec);
      // Open editor immediately so user can edit the title
      this.$nextTick(() => this.openEditorModal(sec.id));
      this.showToast('Title block added');
    },

    /* ── Remove section ─────────────────────────────────────────── */
    async removeSection(rowId, sectionId) {
      const url = _SECTION_URL_TPL.replace('SECTIONID', sectionId);
      await fetch(url, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': _CSRF },
      });

      const grid = this.grids[rowId];
      if (grid) {
        const el = document.querySelector('#row-grid-' + rowId + ' [gs-id="' + sectionId + '"]');
        // removeDOM must be true here: unlike removeRow() (where Alpine's
        // x-for re-render removes the row container), section DOM nodes are
        // created manually via grid.addWidget() in _addWidgetToGrid() and
        // are not tracked by any Alpine template — GridStack must remove
        // the element itself or it stays visible after deletion.
        if (el) grid.removeWidget(el, true);
      }

      const row = this.getRow(rowId);
      if (row) row.sections = row.sections.filter(s => s.id !== sectionId);
      if (this.selectedSection === sectionId) this.selectedSection = null;
    },

    /* ── Section drag-to-reorder ────────────────────────────────── */
    startSectionDrag(event, secId, rowId) {
      this.dragSectionId = secId;
      this.dragSourceRowId = rowId;
      // setData() is required by browsers to activate the drag
      event.dataTransfer.setData('text/plain', String(secId));
      event.dataTransfer.effectAllowed = 'move';
    },

    sectionDragOver(event, secId) {
      event.preventDefault();
      this.dragOverSectionId = secId;
    },

    dropSection(event, rowId, targetSecId) {
      event.preventDefault();
      if (!this.dragSectionId || this.dragSectionId === targetSecId) {
        this.endSectionDrag(); return;
      }
      const row = this.getRow(rowId);
      if (!row) { this.endSectionDrag(); return; }

      const fromIdx = row.sections.findIndex(s => s.id === this.dragSectionId);
      const toIdx   = row.sections.findIndex(s => s.id === targetSecId);
      if (fromIdx < 0 || toIdx < 0) { this.endSectionDrag(); return; }

      const moved = row.sections.splice(fromIdx, 1)[0];
      row.sections.splice(toIdx, 0, moved);
      this._saveSectionOrder(rowId, row.sections);
      this.endSectionDrag();
    },

    endSectionDrag() {
      this.dragSectionId = null;
      this.dragOverSectionId = null;
      this.dragSourceRowId = null;
    },

    async _saveSectionOrder(rowId, sections) {
      const url = this._sectionsUrl();
      const rows = [{
        row_id: rowId,
        sections: sections.map((s, i) => ({ section_id: s.id, position: i })),
      }];
      await this._post(url, { action: 'reorder', rows });
    },

    /* ── Row drag-to-reorder ───────────────────────────────────── */
    startRowDrag(event, rowId) {
      this.dragRowId = rowId;
      event.dataTransfer.setData('text/plain', String(rowId));
      event.dataTransfer.effectAllowed = 'move';
    },

    rowDragOver(event, rowId) {
      event.preventDefault();
      this.dragOverRowId = rowId;
    },

    async dropRow(event, targetRowId) {
      event.preventDefault();
      if (!this.dragRowId || this.dragRowId === targetRowId) {
        this.endRowDrag(); return;
      }
      const fromIdx = this.rows.findIndex(r => r.id === this.dragRowId);
      const toIdx   = this.rows.findIndex(r => r.id === targetRowId);
      if (fromIdx < 0 || toIdx < 0) { this.endRowDrag(); return; }

      const moved = this.rows.splice(fromIdx, 1)[0];
      this.rows.splice(toIdx, 0, moved);

      // Save the updated row positions immediately
      this.saving = true;
      const url = this._sectionsUrl();
      const rowsPayload = this.rows.map((row, ri) => {
        row.position = ri;
        const grid = this.grids[row.id];
        let sectionLayout = [];
        if (grid) {
          const items = grid.save(false) || [];
          sectionLayout = items.map(item => ({
            section_id: parseInt(item.id, 10),
            gs_x: item.x ?? 0, gs_y: item.y ?? 0,
            gs_w: item.w ?? 6, gs_h: item.h ?? 4,
          })).filter(i => i.section_id);
          sectionLayout.sort((a, b) => a.gs_y - b.gs_y || a.gs_x - b.gs_x);
          sectionLayout.forEach((item, idx) => item.position = idx);
        }
        return {
          row_id: row.id,
          position: ri,
          sections: sectionLayout,
        };
      });
      await this._post(url, { action: 'reorder', rows: rowsPayload });
      this.saving = false;
      this.endRowDrag();
      this.showToast('Row order updated');
    },

    endRowDrag() {
      this.dragRowId = null;
      this.dragOverRowId = null;
    },

    /* ── Section helpers ────────────────────────────────────────── */
    getSection(sectionId) {
      for (const row of this.rows) {
        const sec = row.sections.find(s => s.id === sectionId);
        if (sec) return sec;
      }
      return null;
    },

    _findRowForSection(sectionId) {
      return this.rows.find(r => r.sections.some(s => s.id === sectionId));
    },

    async updateSectionProp(sectionId, prop, value) {
      const sec = this.getSection(sectionId);
      if (sec) {
        sec[prop] = value;
        if (prop === 'show_title') {
          const row = this._findRowForSection(sectionId);
          if (row) {
            this._rerenderSection(row.id, sec);
            this.$nextTick(() => {
              const el = document.getElementById('row-grid-' + row.id);
              if (window.htmx && el) htmx.process(el);
            });
          }
        }
      }
      const url = _SECTION_URL_TPL.replace('SECTIONID', sectionId);
      await this._post(url, { [prop]: value });
    },

    async updateHtmlContent(sectionId, content) {
      const sec = this.getSection(sectionId);
      if (!sec) return;
      sec.html_content = content;
      sec.html_preview = content.replace(/<[^>]*>/g, '').slice(0, 30);
      const row = this._findRowForSection(sectionId);
      if (row) this._rerenderSection(row.id, sec);
      const url = _SECTION_URL_TPL.replace('SECTIONID', sectionId);
      await this._post(url, { html_content: content });
    },

    setSectionPadding(sectionId, px) {
      const sec = this.getSection(sectionId);
      if (!sec) return;
      sec.padding_px = px;
      const { inner } = _parseHtmlWrapper(sec.html_content);
      sec.html_content = _applyHtmlWrapper(inner, px, sec.center_content || false);
      sec.html_preview = sec.html_content.replace(/<[^>]*>/g, '').slice(0, 30);
      const row = this._findRowForSection(sectionId);
      if (row) this._rerenderSection(row.id, sec);
      const url = _SECTION_URL_TPL.replace('SECTIONID', sectionId);
      this._post(url, { html_content: sec.html_content });
    },

    setCenterContent(sectionId, center) {
      const sec = this.getSection(sectionId);
      if (!sec) return;
      sec.center_content = center;
      const { inner } = _parseHtmlWrapper(sec.html_content);
      sec.html_content = _applyHtmlWrapper(inner, sec.padding_px || 0, center);
      sec.html_preview = sec.html_content.replace(/<[^>]*>/g, '').slice(0, 30);
      const row = this._findRowForSection(sectionId);
      if (row) this._rerenderSection(row.id, sec);
      const url = _SECTION_URL_TPL.replace('SECTIONID', sectionId);
      this._post(url, { html_content: sec.html_content });
    },

    async updateSectionHeight(sectionId, h) {
      const sec = this.getSection(sectionId);
      if (!sec) return;
      const row = this._findRowForSection(sectionId);
      const grid = row ? this.grids[row.id] : null;
      const widget = row ? document.querySelector('#row-grid-' + row.id + ' [gs-id="' + sectionId + '"]') : null;
      if (h === 0) {
        // 0 = auto-fit to content; _fitHtmlWidget will persist the result
        if (grid && widget) this._fitHtmlWidget(grid, widget);
      } else {
        sec.gs_h = h;
        if (widget) { widget.removeAttribute('gs-size-to-content'); }
        if (grid && widget) grid.update(widget, { h });
        await this._post(_SECTION_URL_TPL.replace('SECTIONID', sectionId), { gs_h: h });
      }
    },

    async updateChartContainerSize(sectionId, key, val) {
      const sec = this.getSection(sectionId);
      if (!sec || !sec.chart) return;
      const numVal = val === '' || val === null ? null : parseInt(val, 10);
      if (!sec.chart.config) sec.chart.config = {};
      sec.chart.config[key] = numVal;
      await this._post(_SECTION_URL_TPL.replace('SECTIONID', sectionId), { [key]: numVal });
      // Update the cached section chart name to trigger reactivity
      sec.chart_name = sec.chart_name || sec.chart.name;
    },

    /* ── Rich text editor (modal) ───────────────────────────────── */
    richCmd(cmd, value) {
      // @mousedown.prevent on toolbar buttons keeps editor focus/selection intact.
      // formatBlock requires angle-bracket tag names across browsers.
      if (cmd === 'formatBlock' && value && !value.startsWith('<')) value = '<' + value + '>';
      document.execCommand(cmd, false, value !== undefined ? value : null);
    },

    saveEditorSel() {
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0) this._savedSel = sel.getRangeAt(0).cloneRange();
    },

    restoreSelAndRichCmd(cmd, value) {
      if (this._savedSel) {
        const editor = document.getElementById('pb-rich-editor');
        if (editor) editor.focus();
        const sel = window.getSelection();
        if (sel) { sel.removeAllRanges(); sel.addRange(this._savedSel); }
        this._savedSel = null;
      }
      this.richCmd(cmd, value);
    },

    initRichEditor(sectionId) {
      this.$nextTick(() => {
        const editor = document.getElementById('pb-rich-editor');
        if (!editor) return;
        const sec = this.getSection(sectionId);
        // Strip any layout wrapper so editor shows clean inner content
        const { inner } = _parseHtmlWrapper(sec?.html_content || '<p></p>');
        editor.innerHTML = inner || '<p></p>';
        editor.focus();
      });
    },

    syncRichEditor(sectionId) {
      // On blur: just save inner content without re-applying wrapper (modal close/save does that)
      const editor = document.getElementById('pb-rich-editor');
      if (!editor) return;
      const sec = this.getSection(sectionId);
      if (sec) {
        const content = _applyHtmlWrapper(editor.innerHTML, sec.padding_px || 0, sec.center_content || false);
        this.updateHtmlContent(sectionId, content);
      }
    },

    switchEditorMode(mode, sectionId) {
      const richEl = document.getElementById('pb-rich-editor');
      const codeEl = document.getElementById('pb-code-editor');
      const sec = this.getSection(sectionId);

      if (mode === 'code') {
        // Flush visual → code (show inner HTML without wrapper in code view)
        if (richEl && sec) {
          const { inner } = _parseHtmlWrapper(sec.html_content);
          this.$nextTick(() => { if (codeEl) codeEl.value = inner || richEl.innerHTML; });
        }
      } else {
        // Flush code → visual
        if (codeEl && sec) {
          const inner = codeEl.value;
          sec.html_content = _applyHtmlWrapper(inner, sec.padding_px || 0, sec.center_content || false);
          sec.html_preview = inner.replace(/<[^>]*>/g, '').slice(0, 30);
        }
        this.$nextTick(() => {
          if (richEl && sec) {
            const { inner } = _parseHtmlWrapper(sec.html_content);
            richEl.innerHTML = inner || '<p></p>';
            richEl.focus();
          }
        });
      }
      this.htmlEditorMode = mode;
    },

    /* ── Editor modal ───────────────────────────────────────────── */
    openEditorModal(sectionId) {
      this.editorSectionId = sectionId;
      this.htmlEditorMode = 'visual';
      this.editorModalOpen = true;
      this.$nextTick(() => this.initRichEditor(sectionId));
    },

    closeEditorModal() {
      if (this.editorSectionId) {
        const sec = this.getSection(this.editorSectionId);
        const editor = document.getElementById('pb-rich-editor');
        if (editor && this.htmlEditorMode === 'visual') {
          const content = _applyHtmlWrapper(editor.innerHTML, sec?.padding_px || 0, sec?.center_content || false);
          this.updateHtmlContent(this.editorSectionId, content);
        }
        const codeEl = document.getElementById('pb-code-editor');
        if (codeEl && this.htmlEditorMode === 'code') {
          const content = _applyHtmlWrapper(codeEl.value, sec?.padding_px || 0, sec?.center_content || false);
          this.updateHtmlContent(this.editorSectionId, content);
        }
      }
      this.editorModalOpen = false;
      this.editorSectionId = null;
    },

    saveEditorModal(sectionId) {
      const editor = document.getElementById('pb-rich-editor');
      const codeEl = document.getElementById('pb-code-editor');
      const sec = this.getSection(sectionId);
      const rawContent = this.htmlEditorMode === 'visual'
        ? (editor ? editor.innerHTML : '')
        : (codeEl ? codeEl.value : '');
      const content = _applyHtmlWrapper(rawContent, sec?.padding_px || 0, sec?.center_content || false);
      if (sectionId) this.updateHtmlContent(sectionId, content);
      this.editorModalOpen = false;
      this.editorSectionId = null;
      this.showToast('Content saved');
    },

    insertLink() {
      const url = prompt('Enter URL:');
      if (url) {
        const editor = document.getElementById('pb-rich-editor');
        if (editor) editor.focus();
        document.execCommand('createLink', false, url);
      }
    },

    _rerenderSection(rowId, sec) {
      const contentEl = document.querySelector('#row-grid-' + rowId + ' [gs-id="' + sec.id + '"] .grid-stack-item-content');
      if (contentEl) contentEl.innerHTML = this._buildSectionHtml(sec);
      // Auto-fit height for HTML sections after content changes
      if (sec.element_type === 'html') {
        const grid = this.grids[rowId];
        const widget = document.querySelector('#row-grid-' + rowId + ' [gs-id="' + sec.id + '"]');
        if (grid && widget) {
          this.$nextTick(() => this._fitHtmlWidget(grid, widget));
        }
      }
    },

    /* ── Chart picker ───────────────────────────────────────────── */
    async loadChartList() {
      const body = document.getElementById('chart-picker-body');
      if (!body) return;
      const q = this.pickerSearch.trim();
      const url = q ? _CHART_LIST_URL + '?q=' + encodeURIComponent(q) : _CHART_LIST_URL;
      try {
        const res = await fetch(url, { credentials: 'same-origin' });
        body.innerHTML = await res.text();
        if (window.Alpine && typeof window.Alpine.initTree === 'function') {
          window.Alpine.initTree(body);
        }
      } catch (e) {
        body.innerHTML = '<p class="text-xs text-red-500 text-center py-4">Failed to load charts</p>';
      }
    },

    /* ── Filters ────────────────────────────────────────────────── */
    async loadFilterColumns() {
      try {
        const res = await fetch(_FILTER_COLS_URL, { credentials: 'same-origin' });
        const data = await res.json();
        this.filterCategories = data.categories || [];
      } catch (e) {
        console.warn('Failed to load filter columns', e);
      }
    },

    async loadFilterOptions(colKey) {
      if (this.filterOptionsMap[colKey]) return; // already loaded
      try {
        const r = await fetch('/dataviz/api/columns/filter-options/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _CSRF },
          body: JSON.stringify({ columns: [colKey] }),
        });
        const data = await r.json();
        if (data.filter_options && data.filter_options[colKey]) {
          this.filterOptionsMap[colKey] = data.filter_options[colKey];
        }
      } catch (e) {
        console.warn('Failed to load filter options for ' + colKey, e);
      }
    },

    isFilterSelected(colKey) {
      return this.filters.some(f => f.source_col === colKey);
    },

    toggleFilter(col) {
      const idx = this.filters.findIndex(f => f.source_col === col.key);
      const isHidden = this.filterModalMode === 'page';
      if (idx >= 0) {
        if (this.filters[idx].hidden === isHidden) {
          this.filters.splice(idx, 1);
        } else {
          this.filters[idx].hidden = isHidden;
          this.loadFilterOptions(col.key);
        }
      } else {
        this.filters.push({
          id: 'filter_' + col.key,
          label: col.label,
          source_col: col.key,
          source_table: 'current_student',
          widget: 'select',
          default_val: '',
          hidden: isHidden,
        });
        this.loadFilterOptions(col.key);
      }
      this.autoSave();
    },

    removeFilter(idx) {
      if (idx >= 0 && idx < this.filters.length) {
        this.filters.splice(idx, 1);
        this.autoSave();
      }
    },

    /* ── Toast ──────────────────────────────────────────────────── */
    showToast(msg, type) {
      this.toast = msg;
      this.toastType = type || 'success';
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

/* ── Layout wrapper helpers ──────────────────────────────────────── */
function _parseHtmlWrapper(html) {
  if (!html) return { inner: '', padding_px: 0, center_content: false };
  if (!html.includes('data-pb-p') && !html.includes('data-pb-center')) {
    return { inner: html, padding_px: 0, center_content: false };
  }
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const w = doc.body.firstElementChild;
    if (w && w.tagName === 'DIV' && (w.hasAttribute('data-pb-p') || w.hasAttribute('data-pb-center'))) {
      return {
        inner: w.innerHTML,
        padding_px: parseInt(w.getAttribute('data-pb-p') || '0') || 0,
        center_content: w.getAttribute('data-pb-center') === '1',
      };
    }
  } catch (e) {}
  return { inner: html, padding_px: 0, center_content: false };
}

function _applyHtmlWrapper(inner, padding_px, center_content) {
  if (!padding_px && !center_content) return inner;
  let attrs = '';
  let style = 'box-sizing:border-box;';
  if (padding_px) { attrs += ` data-pb-p="${padding_px}"`; style += `padding:${padding_px}px;`; }
  if (center_content) { attrs += ' data-pb-center="1"'; style += 'display:flex;align-items:center;justify-content:center;min-height:100%;'; }
  return `<div${attrs} style="${style}">${inner}</div>`;
}

/* ── Helper to map a section from JSON ──────────────────────────── */
function _mapSection(s) {
  const html = s.html_content || '';
  const { padding_px, center_content } = _parseHtmlWrapper(html);
  return {
    id: s.id,
    row_id: s.row_id,
    chart_id: s.chart ? s.chart.id : null,
    chart_name: s.chart ? s.chart.name : null,
    element_type: s.element_type || 'chart',
    html_content: html,
    html_preview: html.replace(/<[^>]*>/g, '').slice(0, 30),
    show_title: s.show_title !== false,
    padding_px,
    center_content,
    gs_x: s.gs_x ?? 0, gs_y: s.gs_y ?? 0,
    gs_w: s.gs_w ?? 6, gs_h: s.gs_h ?? 4,
  };
}
