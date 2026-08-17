/* chart-builder-explorer.js — Alpine component methods: column picker (explorer), year mode, drill-through */

function chartBuilderExplorerMethods() {
  function getCsrf() {
    return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  }

  return {
    /* ─── Explorer structure loading ────────────────────────── */
    async loadExplorerStructure() {
      this.explorerLoading = true;
      try {
        const r = await fetch('/dataviz/api/columns/structures/',
                              { headers: { 'X-CSRFToken': getCsrf() } });
        const data = await r.json();
        const rawAliasMap = data?.year_alias_map || {};
        const aliasMap = {};
        for (const [yr, alias] of Object.entries(rawAliasMap)) {
          aliasMap[yr.replaceAll('-', '_')] = alias;
        }
        this.dvYearAliasMap = aliasMap;
        this.explorerStructure = {
          categories: data?.categories || {},
          definitions: data?.definitions || {},
          test_structure: data?.test_structure || {},
          mark_structure: data?.mark_structure || {},
          assessment_structure: data?.assessment_structure || {},
          mark_assessment_structure: data?.mark_assessment_structure || {},
          year_labels: data?.year_labels || {},
        };
      } catch (e) {
        console.error('Explorer structure:', e);
        this.explorerStructure = { categories: {}, definitions: {}, test_structure: {}, mark_structure: {}, assessment_structure: {}, mark_assessment_structure: {}, year_labels: {} };
      }
      this.explorerLoading = false;
      if (this.explorerSelected.length > 0) this._syncExplorerCols();
    },

    toggleExplorerCol(col) {
      if (this._getExplorerSelectedKeySet().has(col)) {
        const specific = this._dvNormaliseKey(col);
        const alias = this._dvColKeyForMode(specific, 'relative');
        const idx = this.explorerSelected.findIndex(k => k === col || k === specific || k === alias);
        if (idx >= 0) this.explorerSelected.splice(idx, 1);
      } else {
        this.explorerSelected.push(this._dvColKey(col));
      }
      this.explorerSelected = [...this.explorerSelected];
      this._syncExplorerCols();
    },

    _dvColKey(col) {
      return this._dvColKeyForMode(col, this.dvYearMode);
    },
    _dvColKeyForMode(col, mode) {
      if (mode !== 'relative') return col;
      const prefix = col.startsWith('test_') ? 'test_' : col.startsWith('mark_') ? 'mark_' : null;
      if (!prefix) return col;
      const rest = col.slice(prefix.length);
      const parts = rest.split('_');
      if (parts.length < 3) return col;
      const yearUnder = parts.slice(0, 2).join('_');
      const alias = this.dvYearAliasMap[yearUnder];
      if (!alias) return col;
      return prefix + alias + '_' + parts.slice(2).join('_');
    },
    _dvNormaliseKey(col) {
      const prefix = col.startsWith('test_') ? 'test_' : col.startsWith('mark_') ? 'mark_' : null;
      if (!prefix) return col;
      const rest = col.slice(prefix.length);
      for (const [yearUnder, alias] of Object.entries(this.dvYearAliasMap)) {
        if (rest.startsWith(alias + '_')) {
          return prefix + yearUnder + '_' + rest.slice(alias.length + 1);
        }
      }
      return col;
    },
    _dvIsColSelected(col) {
      return this._getExplorerSelectedKeySet().has(col);
    },
    _convertConfigCol(col, mode) {
      if (!col) return col;
      const norm = this._dvNormaliseKey(col);
      return mode === 'relative' ? this._dvColKeyForMode(norm, 'relative') : norm;
    },
    _cleanConfigColumns() {
      if (this.sourceType !== 'columns') return;
      const set = this._getExplorerSelectedKeySet();
      const isSelected = (col) => {
        if (!col) return false;
        if (!col.startsWith('test_') && !col.startsWith('mark_')) return true;
        return set.has(col);
      };
      this.xCols = this.xCols.filter(isSelected);
      if (this.xCol && !isSelected(this.xCol)) this.xCol = '';
      if (this.yCol && !isSelected(this.yCol)) this.yCol = '';
      this.seriesCols = this.seriesCols.filter(isSelected);
      if (this.seriesCol && !isSelected(this.seriesCol)) this.seriesCol = '';
      if (this.groupCol && !isSelected(this.groupCol)) this.groupCol = '';
      this.comparePlCols = this.comparePlCols.filter(isSelected);
      if (this.pgBeforeCol && !isSelected(this.pgBeforeCol)) this.pgBeforeCol = '';
      if (this.pgAfterCol && !isSelected(this.pgAfterCol)) this.pgAfterCol = '';
      if (this.stRowCol && !isSelected(this.stRowCol)) this.stRowCol = '';
      if (this.stColCol && !isSelected(this.stColCol)) this.stColCol = '';
      this.stRowCols = this.stRowCols.filter(isSelected);
      this.stColCols = this.stColCols.filter(isSelected);
      if (this.stValueCol && !isSelected(this.stValueCol)) this.stValueCol = '';
      if (this.sortCol && !isSelected(this.sortCol)) this.sortCol = '';
    },

    setDvYearMode(mode) {
      if (mode === this.dvYearMode) return;
      this.explorerSelected = [...new Set(this.explorerSelected.map(key =>
        mode === 'relative' ? this._dvColKeyForMode(this._dvNormaliseKey(key), 'relative')
                            : this._dvNormaliseKey(key)
      ))];
      this.xCols = this.xCols.map(c => this._convertConfigCol(c, mode));
      if (this.xCol) this.xCol = this._convertConfigCol(this.xCol, mode);
      if (this.yCol) this.yCol = this._convertConfigCol(this.yCol, mode);
      this.seriesCols = this.seriesCols.map(c => this._convertConfigCol(c, mode));
      if (this.seriesCol) this.seriesCol = this._convertConfigCol(this.seriesCol, mode);
      if (this.groupCol) this.groupCol = this._convertConfigCol(this.groupCol, mode);
      this.comparePlCols = this.comparePlCols.map(c => this._convertConfigCol(c, mode));
      if (this.pgBeforeCol) this.pgBeforeCol = this._convertConfigCol(this.pgBeforeCol, mode);
      if (this.pgAfterCol) this.pgAfterCol = this._convertConfigCol(this.pgAfterCol, mode);
      if (this.stRowCol) this.stRowCol = this._convertConfigCol(this.stRowCol, mode);
      if (this.stColCol) this.stColCol = this._convertConfigCol(this.stColCol, mode);
      this.stRowCols = this.stRowCols.map(c => this._convertConfigCol(c, mode));
      this.stColCols = this.stColCols.map(c => this._convertConfigCol(c, mode));
      if (this.sortCol) this.sortCol = this._convertConfigCol(this.sortCol, mode);

      if (this.chartFilters.school_year) {
        const yearVals = Array.isArray(this.chartFilters.school_year)
          ? this.chartFilters.school_year
          : [this.chartFilters.school_year];
        this.chartFilters.school_year = yearVals.map(val => {
          if (mode === 'relative') {
            const key_us = val.replace('-', '_');
            return this.dvYearAliasMap[key_us] || val;
          } else {
            for (const [yearUs, alias] of Object.entries(this.dvYearAliasMap)) {
              if (alias === val) return yearUs.replace('_', '-');
            }
            return val;
          }
        });
      }
      this.dvYearMode = mode;
      localStorage.setItem('dwYearMode', mode);
      this._syncExplorerCols();
      this._schedulePreview(true);
    },

    /* ─── Drill-through methods ─────────────────────────────── */
    drillColLabel(col) {
      const clean = col.split('.').pop();
      return this.explorerStructure?.definitions?.[clean] ||
             this.explorerStructure?.definitions?.[col] ||
             this.filterLabels[clean] ||
             this.filterLabels[col] ||
             col;
    },
    toggleDrillCol(col) {
      const cleanCol = col.split('.').pop();
      if (this.drillSelected.length === 0 && this.queryColumns.length > 0) {
        this.drillSelected = this.queryColumns.map(c => c.split('.').pop());
      }
      const idx = this.drillSelected.indexOf(cleanCol);
      if (idx >= 0) {
        this.drillSelected.splice(idx, 1);
      } else {
        this.drillSelected.push(cleanCol);
      }
      this.drillSelected = [...this.drillSelected];
    },
    drillMoveCol(fromIdx, toIdx) {
      if (this.drillSelected.length === 0 && this.queryColumns.length > 0) {
        this.drillSelected = this.queryColumns.map(c => c.split('.').pop());
      }
      if (toIdx < 0 || toIdx >= this.drillSelected.length) return;
      const arr = [...this.drillSelected];
      const [item] = arr.splice(fromIdx, 1);
      arr.splice(toIdx, 0, item);
      this.drillSelected = arr;
    },

    _getExplorerSelectedKeySet() {
      if (this._explorerKeySetVersion !== this._explorerKeySetCachedAt) {
        const set = new Set();
        for (const key of this.explorerSelected) {
          set.add(key);
          const norm = this._dvNormaliseKey(key);
          if (norm !== key) set.add(norm);
          const aliasForm = this._dvColKeyForMode(norm, 'relative');
          if (aliasForm !== norm && aliasForm !== key) set.add(aliasForm);
        }
        this._explorerSelectedKeySet = set;
        this._explorerKeySetCachedAt = this._explorerKeySetVersion;
      }
      return this._explorerSelectedKeySet;
    },

    _syncExplorerCols() {
      this._explorerKeySetVersion = (this._explorerKeySetVersion || 0) + 1;
      this.explorerCols = this.explorerSelected.map(c => c);
      this._cleanConfigColumns();
      clearTimeout(this._explorerSyncTimer);
      this._explorerSyncTimer = setTimeout(() => {
        this._loadExplorerFilterOptions();
        this.fetchRecommendations();
      }, 350);
    },

    async _loadExplorerFilterOptions() {
      if (this.explorerSelected.length === 0) {
        this.filterOptions = {}; this.filterLabels = {};
        this._recomputeFilterCols();
        return;
      }
      // Cancel any in-flight filter options request
      if (this._filterOptionsAbortCtrl) this._filterOptionsAbortCtrl.abort();
      this._filterOptionsAbortCtrl = new AbortController();
      this.filtersLoading = true;
      try {
        const activeFilters = this.cleanSourceFilters();
        const body = { columns: this.explorerSelected };
        if (Object.keys(activeFilters).length > 0) body.filters = activeFilters;
        const r = await fetch('/dataviz/api/columns/filter-options/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify(body),
          signal: this._filterOptionsAbortCtrl.signal,
        });
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
      } catch (e) {
        if (e.name !== 'AbortError') { console.error('Explorer filters:', e); this.filterOptions = {}; this.filterLabels = {}; }
        this._recomputeFilterCols();
      }
      this.filtersLoading = false;
    },

    explorerCatSelectedCount(catKey) {
      const cat = this.explorerStructure?.categories?.[catKey];
      if (!cat) return '';
      const n = (cat.columns || []).filter(c => this.explorerSelected.includes(c)).length;
      return n > 0 ? `${n}/${cat.columns.length}` : '';
    },
    explorerFilteredCatCols(catKey) {
      const cat = this.explorerStructure?.categories?.[catKey];
      if (!cat) return [];
      const cols = cat.columns || [];
      if (!this.explorerSearch) return cols;
      const q = this.explorerSearch.toLowerCase();
      const defs = this.explorerStructure?.definitions || {};
      return cols.filter(c => (defs[c] || c).toLowerCase().includes(q));
    },
    drillCatSelectedCount(catKey) {
      const cat = this.explorerStructure?.categories?.[catKey];
      if (!cat) return '';
      const list = this.drillPreviewList;
      const n = (cat.columns || []).filter(c => list.includes(c)).length;
      return n > 0 ? `${n}/${cat.columns.length}` : '';
    },
    drillFilteredCatCols(catKey) {
      const cat = this.explorerStructure?.categories?.[catKey];
      if (!cat) return [];
      const cols = cat.columns || [];
      if (!this.drillSearch) return cols;
      const q = this.drillSearch.toLowerCase();
      const defs = this.explorerStructure?.definitions || {};
      return cols.filter(c => (defs[c] || c).toLowerCase().includes(q));
    },
  };
}
