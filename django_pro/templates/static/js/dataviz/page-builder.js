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
    filters:  (_PAGE_FILTERS || []).map(f => ({ ...f })),
    filtersEnabled: (_PAGE_FILTERS || []).length > 0,
    filterCategories: [],

    /* Sidebar accordion open/closed state */
    sidebarSections: { settings: true, filters: true, layout: true },

    /* Filter modal */
    filterModalOpen: false,
    filterColumnSearch: '',

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

    rows: _ROWS_JSON.map(r => ({
      id:        r.id,
      label:     r.label || '',
      position:  r.position ?? 0,
      bg_color:  r.bg_color || '',
      collapsed: r.collapsed || false,
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

      this.$nextTick(() => {
        if (this.pageId) {
          this._initAllGrids();
          this.loadChartList();
        }
      });
      this.$watch('pickerOpen', open => {
        if (open) this.loadChartList();
      });

      // Load available filter columns
      this.loadFilterColumns();
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
          disableOneColumnMode: true,
          resizable: { handles: 'se,sw,e,w' },
          float: false,
        }, el);
        this.grids[row.id] = grid;

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
      content.classList.add('grid-stack-item-content',
        'bg-white', 'border', 'border-slate-200', 'rounded-xl',
        'overflow-hidden', 'shadow-sm', 'flex', 'flex-col');
      content.innerHTML = this._buildSectionHtml(sec);
      item.appendChild(content);

      grid.addWidget(item);
      return item;
    },

    _buildSectionHtml(sec) {
      if (sec.element_type === 'html') {
        return '<div class="p-4 text-sm text-slate-700 leading-relaxed">' + (sec.html_content || '<p class="text-slate-400 italic">Empty text block — click Edit Content to add text</p>') + '</div>';
      }
      const pageSlug = encodeURIComponent(this.pageSlug);
      const chartId = sec.chart_id;
      const renderUrl = _CHART_RENDER_TPL.replace('CHARTID', chartId)
        + '?source=page&source_id=' + encodeURIComponent(this.pageId) + '&source_name=' + pageSlug;
      const titleBar = sec.show_title !== false
        ? '<div class="flex items-center justify-between px-3 py-1.5 border-b border-slate-100 bg-slate-50/50">'
        + '<span class="text-xs font-semibold text-slate-600 truncate">' + this._esc(sec.chart_name || 'Chart') + '</span>'
        + '<a href="/dataviz/charts/' + chartId + '/edit/" class="text-[10px] text-blue-500 hover:underline" title="Edit chart">edit</a>'
        + '</div>'
        : '';
      return titleBar
        + '<div class="flex-1 min-h-0" hx-get="' + renderUrl + '" hx-trigger="load" hx-swap="innerHTML">'
        + '<div class="flex items-center justify-center h-full text-slate-300 text-xs">Loading…</div>'
        + '</div>';
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
        }));
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

      const url = this._sectionsUrl();
      const reorderRes = await this._post(url, { action: 'reorder', rows: rowsPayload });

      // Sync local state
      rowsPayload.forEach(rp => {
        const row = this.rows.find(r => r.id === rp.row_id);
        if (!row) return;
        rp.sections.forEach(l => {
          const s = row.sections.find(s => s.id === l.section_id);
          if (s) { s.gs_x = l.gs_x; s.gs_y = l.gs_y; s.gs_w = l.gs_w; s.gs_h = l.gs_h; }
        });
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
        sections: [],
      };
      this.rows.push(row);
      this.activeRow = row.id;
      this.$nextTick(() => this._initRowGrid(row));
      this.showToast('Section added');
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
        gs_w: 12, gs_h: 2,
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
        gs_w: 12, gs_h: 2,
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
        if (el) grid.removeWidget(el, false);
      }

      const row = this.getRow(rowId);
      if (row) row.sections = row.sections.filter(s => s.id !== sectionId);
      if (this.selectedSection === sectionId) this.selectedSection = null;
    },

    /* ── Section drag-to-reorder ────────────────────────────────── */
    startSectionDrag(event, secId, rowId) {
      this.dragSectionId = secId;
      this.dragSourceRowId = rowId;
      event.dataTransfer.effectAllowed = 'move';
    },

    sectionDragOver(secId) {
      this.dragOverSectionId = secId;
    },

    dropSection(event, rowId, targetSecId) {
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
      // Strip any existing padding wrapper then re-apply
      let inner = sec.html_content.replace(/^<div[^>]*data-pb-p="\d+"[^>]*>([\s\S]*?)<\/div>$/, '$1');
      sec.html_content = px > 0
        ? `<div data-pb-p="${px}" style="padding:${px}px;box-sizing:border-box;">${inner}</div>`
        : inner;
      sec.html_preview = sec.html_content.replace(/<[^>]*>/g, '').slice(0, 30);
      const row = this._findRowForSection(sectionId);
      if (row) this._rerenderSection(row.id, sec);
      const url = _SECTION_URL_TPL.replace('SECTIONID', sectionId);
      this._post(url, { html_content: sec.html_content });
    },

    /* ── Rich text editor (modal) ───────────────────────────────── */
    richCmd(cmd, value) {
      // @mousedown.prevent on toolbar buttons already keeps editor focus/selection intact.
      // Do NOT call editor.focus() here – it would collapse the selection.
      document.execCommand(cmd, false, value !== undefined ? value : null);
    },

    initRichEditor(sectionId) {
      this.$nextTick(() => {
        const editor = document.getElementById('pb-rich-editor');
        if (!editor) return;
        const sec = this.getSection(sectionId);
        // Strip any padding wrapper so the editor shows clean inner content
        let html = sec?.html_content || '<p></p>';
        html = html.replace(/^<div[^>]*data-pb-p="\d+"[^>]*>([\s\S]*?)<\/div>$/, '$1');
        editor.innerHTML = html;
        editor.focus();
      });
    },

    syncRichEditor(sectionId) {
      const editor = document.getElementById('pb-rich-editor');
      if (editor) this.updateHtmlContent(sectionId, editor.innerHTML);
    },

    switchEditorMode(mode, sectionId) {
      const richEl = document.getElementById('pb-rich-editor');
      const codeEl = document.getElementById('pb-code-editor');
      const sec = this.getSection(sectionId);

      if (mode === 'code') {
        // Flush visual → code
        if (richEl && sec) {
          sec.html_content = richEl.innerHTML;
          sec.html_preview = richEl.innerHTML.replace(/<[^>]*>/g, '').slice(0, 30);
        }
        this.$nextTick(() => {
          if (codeEl && sec) codeEl.value = sec.html_content || '';
        });
      } else {
        // Flush code → visual
        if (codeEl && sec) {
          sec.html_content = codeEl.value;
          sec.html_preview = codeEl.value.replace(/<[^>]*>/g, '').slice(0, 30);
        }
        this.$nextTick(() => {
          if (richEl && sec) {
            richEl.innerHTML = sec.html_content || '<p></p>';
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
      // Flush content before closing, re-wrapping with padding if needed
      if (this.editorSectionId) {
        const sec = this.getSection(this.editorSectionId);
        const editor = document.getElementById('pb-rich-editor');
        if (editor && this.htmlEditorMode === 'visual') {
          let content = editor.innerHTML;
          if (sec?.padding_px > 0) {
            content = `<div data-pb-p="${sec.padding_px}" style="padding:${sec.padding_px}px;box-sizing:border-box;">${content}</div>`;
          }
          this.updateHtmlContent(this.editorSectionId, content);
        }
        const codeEl = document.getElementById('pb-code-editor');
        if (codeEl && this.htmlEditorMode === 'code') {
          this.updateHtmlContent(this.editorSectionId, codeEl.value);
        }
      }
      this.editorModalOpen = false;
      this.editorSectionId = null;
    },

    saveEditorModal(sectionId) {
      const editor = document.getElementById('pb-rich-editor');
      const codeEl = document.getElementById('pb-code-editor');
      let content = this.htmlEditorMode === 'visual'
        ? (editor ? editor.innerHTML : '')
        : (codeEl ? codeEl.value : '');
      // Re-apply padding wrapper when saving
      const sec = this.getSection(sectionId);
      if (sec?.padding_px > 0) {
        content = `<div data-pb-p="${sec.padding_px}" style="padding:${sec.padding_px}px;box-sizing:border-box;">${content}</div>`;
      }
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
      const el = document.querySelector('#row-grid-' + rowId + ' [gs-id="' + sec.id + '"] .grid-stack-item-content');
      if (el) el.innerHTML = this._buildSectionHtml(sec);
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

    isFilterSelected(colKey) {
      return this.filters.some(f => f.source_col === colKey);
    },

    toggleFilter(col) {
      const idx = this.filters.findIndex(f => f.source_col === col.key);
      if (idx >= 0) {
        this.filters.splice(idx, 1);
      } else {
        this.filters.push({
          id: 'filter_' + col.key,
          label: col.label,
          source_col: col.key,
          source_table: 'current_student',
          widget: 'select',
          default_val: '',
        });
      }
      this.autoSave();
    },

    removeFilter(idx) {
      this.filters.splice(idx, 1);
      this.autoSave();
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

/* ── Helper to map a section from JSON ──────────────────────────── */
function _mapSection(s) {
  const html = s.html_content || '';
  const padMatch = html.match(/data-pb-p="(\d+)"/);
  return {
    id: s.id,
    row_id: s.row_id,
    chart_id: s.chart ? s.chart.id : null,
    chart_name: s.chart ? s.chart.name : null,
    element_type: s.element_type || 'chart',
    html_content: html,
    html_preview: html.replace(/<[^>]*>/g, '').slice(0, 30),
    show_title: s.show_title !== false,
    padding_px: padMatch ? parseInt(padMatch[1]) : 0,
    gs_x: s.gs_x ?? 0, gs_y: s.gs_y ?? 0,
    gs_w: s.gs_w ?? 6, gs_h: s.gs_h ?? 4,
  };
}
