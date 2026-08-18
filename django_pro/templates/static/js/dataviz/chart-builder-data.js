/* chart-builder-data.js — Alpine component methods: column loading, filter options, SQL, query preview */

function chartBuilderDataMethods() {
  function getCsrf() {
    return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  }

  return {
    /* ─── Column loading ─────────────────────────────────── */
    async loadColumns() {
      if (!this.savedQueryId) { this.columns = []; this.filterOptions = {}; this.filterLabels = {}; return; }
      this.columnsLoading = true;
      const ctrl = new AbortController();
      try {
        const r = await fetch(`/dataviz/api/query/${this.savedQueryId}/columns/`,
                              { headers: { 'X-CSRFToken': getCsrf() }, signal: ctrl.signal });
        const d = await r.json();
        this.columns = d.columns || [];
      } catch (e) {
        if (e.name !== 'AbortError') console.error('Column load:', e);
        this.columns = [];
      }
      this.columnsLoading = false;
      // Load filter options in parallel (non-blocking)
      this.loadFilterOptions();
      this.fetchRecommendations();
    },

    async loadDatasetColumns() {
      if (!this.datasetSourceId) { this.columns = []; this.filterOptions = {}; this.filterLabels = {}; return; }
      this.columnsLoading = true;
      try {
        const r = await fetch(`/dataviz/api/datasets/${this.datasetSourceId}/`,
                               { headers: { 'X-CSRFToken': getCsrf() } });
        const ds = await r.json();
        const cols = ds.columns || [];
        const mappings = ds.column_mappings || {};
        this.datasetColumnMappings = mappings;
        this.columns = cols.map(c => mappings[c] || c.split('.').slice(-1)[0]);
        if (ds.school_filter_mode) this.schoolFilterMode = ds.school_filter_mode;
        if (ds.year_context) {
          this.yearContext = ds.year_context;
          this.chartFilters = { ...this.chartFilters, school_year: ds.year_context };
        }
        const dsFilters = ds.filters || [];
        if (dsFilters.length > 0) {
          dsFilters.forEach(f => {
            if (f.column && f.value) {
              const alias = mappings[f.column] || f.column.split('.').slice(-1)[0];
              this.chartFilters[alias] = Array.isArray(f.value) ? f.value : [f.value];
            }
          });
        }
      } catch (e) { console.error('Dataset load:', e); this.columns = []; }
      this.columnsLoading = false;
      this._loadDatasetFilterOptions();
      this.fetchRecommendations();
    },

    async _loadDatasetFilterOptions() {
      if (!this.datasetSourceId) { this.filterOptions = {}; this.filterLabels = {}; return; }
      this.filtersLoading = true;
      try {
        const params = new URLSearchParams();
        const activeFilters = this.cleanSourceFilters();
        if (Object.keys(activeFilters).length > 0) {
          params.set('current_filters', JSON.stringify(activeFilters));
        }
        const r = await fetch(`/dataviz/api/datasets/${this.datasetSourceId}/filter-options/?${params}`,
                               { headers: { 'X-CSRFToken': getCsrf() } });
        const d = await r.json();
        this.filterOptions = d.filter_options || {};
        const labels = d.filter_labels || {};
        this.yearAliasLabels = labels.__school_year_alias_labels__ || {};
        delete labels.__school_year_alias_labels__;
        this.filterLabels = labels;
        Object.keys(this.filterOptions).forEach(col => {
          this.filterOptions[col] = this.normalizeRangeOptionBounds(col, this.filterOptions[col]);
        });
        this._preserveSelectedFilterValues();
        this._recomputeFilterCols();
      } catch (e) { console.error('Dataset filter options:', e); this.filterOptions = {}; this.filterLabels = {}; this._recomputeFilterCols(); }
      this.filtersLoading = false;
    },

    async loadFilterOptions() {
      if (!this.savedQueryId) { this.filterOptions = {}; this.filterLabels = {}; return; }
      this.filtersLoading = true;
      try {
        const params = new URLSearchParams();
        const activeFilters = this.cleanSourceFilters();
        if (Object.keys(activeFilters).length > 0) {
          params.set('current_filters', JSON.stringify(activeFilters));
        }
        const r = await fetch(`/dataviz/api/query/${this.savedQueryId}/filter-options/?${params}`,
                              { headers: { 'X-CSRFToken': getCsrf() } });
        const d = await r.json();
        this.filterOptions = d.filter_options || {};
        const labels = d.filter_labels || {};
        this.yearAliasLabels = labels.__school_year_alias_labels__ || {};
        delete labels.__school_year_alias_labels__;
        this.testedSchoolNames = labels.__tested_school_labels__ || {};
        delete labels.__tested_school_labels__;
        this.filterLabels = labels;
        Object.keys(this.filterOptions).forEach(col => {
          this.filterOptions[col] = this.normalizeRangeOptionBounds(col, this.filterOptions[col]);
        });
        this._preserveSelectedFilterValues();
        this._recomputeFilterCols();
      } catch (e) { console.error('Filter options:', e); this.filterOptions = {}; this.filterLabels = {}; this._recomputeFilterCols(); }
      this.filtersLoading = false;
      if (this.chartType === 'progress_growth' && this.pgBeforeCol && this.pgAfterCol) {
        this._pgAutoScale();
      }
    },

    /* Keep any currently-selected filter values in the options list even when
       the data no longer contains them (e.g. a relative year that resolves to
       a year with no rows yet). Without this the checked chip disappears after
       a data rollover to a new current year. */
    _preserveSelectedFilterValues() {
      if (!this.chartFilters || typeof this.chartFilters !== 'object') return;
      let changed = false;
      const opts = { ...this.filterOptions };
      Object.entries(this.chartFilters).forEach(([col, vals]) => {
        if (!Array.isArray(vals) || !vals.length) return;
        const existing = Array.isArray(opts[col]) ? opts[col] : [];
        const merged = existing.slice();
        vals.forEach(v => { if (!merged.includes(v)) merged.push(v); });
        if (merged.length !== existing.length) {
          opts[col] = merged;
          changed = true;
        }
      });
      if (changed) this.filterOptions = opts;
    },

    async runSql() {
      if (!this.rawSql.trim()) return;
      this.columnsLoading = true;
      try {
        const r = await fetch('/dataviz/api/query/sql/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify({ sql: this.rawSql }),
        });
        const d = await r.json();
        if (d.error) { this._flash(d.error, true); this.rawSqlColumns = []; }
        else { this.rawSqlColumns = d.columns || []; }
      } catch (e) { console.error('SQL:', e); this.rawSqlColumns = []; }
      this.columnsLoading = false;
    },

    async previewQuery() {
      if (!this.savedQueryId) return;
      this.queryPreviewLoading = true;
      this.queryPreviewData = null;
      try {
        const r = await fetch(`/dataviz/api/query/${this.savedQueryId}/preview/`,
                              { headers: { 'X-CSRFToken': getCsrf() } });
        const data = await r.json();
        if (data.success) {
          this.queryPreviewData = { columns: data.columns, rows: data.rows };
        } else {
          this._flash(data.error || 'Preview failed', true);
        }
      } catch (e) {
        this._flash('Preview error: ' + e.message, true);
      }
      this.queryPreviewLoading = false;
    },

    /* ─── Colour mapping presets ─────────────────────────── */
    async loadColorMapPresets() {
      try {
        const r = await fetch('/dataviz/api/colormap-presets/list/', { headers: { 'X-CSRFToken': getCsrf() } });
        const d = await r.json();
        this.colorMapPresets = d.presets || [];
      } catch (e) { console.error('Colour preset list:', e); this.colorMapPresets = []; }
    },

    async loadColorMapPreset(id) {
      if (!id) return;
      try {
        const r = await fetch(`/dataviz/api/colormap-presets/${id}/`, { headers: { 'X-CSRFToken': getCsrf() } });
        const d = await r.json();
        if (!d.preset) { this._flash('Preset not found', true); return; }
        this.colorMap = { ...d.preset.color_map };
        this.colorMapPresetId = d.preset.id;
        this.colorMapPresetName = d.preset.name;
        this._schedulePreview();
        this._flash(`Loaded "${d.preset.name}"`);
      } catch (e) { this._flash('Load preset error: ' + e.message, true); }
    },

    async saveColorMapPresetAsNew(name) {
      const trimmed = (name || '').trim();
      if (!trimmed) return;
      try {
        const r = await fetch('/dataviz/api/colormap-presets/save/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify({ name: trimmed, color_map: this.colorMap }),
        });
        const d = await r.json();
        if (!d.success) { this._flash(d.error || 'Save failed', true); return; }
        this.colorMapPresets.push(d.preset);
        this.colorMapPresets.sort((a, b) => a.name.localeCompare(b.name));
        this.colorMapPresetId = d.preset.id;
        this.colorMapPresetName = d.preset.name;
        this._flash(`Saved "${d.preset.name}"`);
      } catch (e) { this._flash('Save preset error: ' + e.message, true); }
    },

    async updateColorMapPreset() {
      if (!this.colorMapPresetId) return;
      try {
        const r = await fetch(`/dataviz/api/colormap-presets/${this.colorMapPresetId}/`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify({ color_map: this.colorMap }),
        });
        const d = await r.json();
        if (!d.success) { this._flash(d.error || 'Update failed', true); return; }
        const idx = this.colorMapPresets.findIndex(p => p.id === d.preset.id);
        if (idx !== -1) this.colorMapPresets[idx] = d.preset;
        this._flash(`Updated "${d.preset.name}"`);
      } catch (e) { this._flash('Update preset error: ' + e.message, true); }
    },

    async deleteColorMapPreset(id) {
      if (!id) return;
      try {
        await fetch(`/dataviz/api/colormap-presets/${id}/`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCsrf() },
        });
        this.colorMapPresets = this.colorMapPresets.filter(p => p.id !== id);
        if (this.colorMapPresetId === id) {
          this.colorMapPresetId = null;
          this.colorMapPresetName = '';
        }
        this._flash('Preset deleted');
      } catch (e) { this._flash('Delete preset error: ' + e.message, true); }
    },
  };
}
