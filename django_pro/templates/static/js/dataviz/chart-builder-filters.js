/* chart-builder-filters.js — Alpine component methods: data filters, year context, custom years, PG scale, query summary */

function chartBuilderFilterMethods() {
  return {
    /* ─── Filter helpers ─────────────────────────────────── */
    toggleFilter(col) { this.openFilter = this.openFilter === col ? '' : col; },

    isDateColumn(col) {
      if (!col) return false;
      const name = String(col).split('.').pop().toLowerCase();
      if (name === 'school_year' || name === 'year') return false;
      return name.endsWith('_date') || name.endsWith('_at') || name === 'date' || name.includes('date_of');
    },

    filterBadgeCount(col) {
      const v = this.chartFilters[col];
      if (Array.isArray(v)) return v.length;
      if (v && typeof v === 'object') return (v.gte || v.lte) ? 1 : 0;
      return 0;
    },

    setDateRangeFilter(col, which, val) {
      const existing = this.chartFilters[col];
      const cur = { ...(existing && typeof existing === 'object' && !Array.isArray(existing) ? existing : {}) };
      if (val) cur[which] = val; else delete cur[which];
      if (!Object.keys(cur).length) {
        const f = { ...this.chartFilters };
        delete f[col];
        this.chartFilters = f;
      } else {
        this.chartFilters = { ...this.chartFilters, [col]: cur };
      }
      this._schedulePreview(true);
    },

    clearDateRangeFilter(col) {
      const f = { ...this.chartFilters };
      delete f[col];
      this.chartFilters = f;
      this._schedulePreview(true);
    },

    filteredFilterValues(col) {
      let vals = this.filterOptions[col] || [];
      if (col === 'school_year' && this.sourceType !== 'dataset') {
        if (this.dvYearMode === 'relative') {
          vals = vals.filter(v => (v || '').startsWith('__sy_'));
        } else {
          vals = vals.filter(v => !(v || '').startsWith('__sy_'));
        }
      }
      const q = (this.filterSearch[col] || '').toLowerCase().trim();
      return q ? vals.filter(v => (v || '').toLowerCase().includes(q)) : vals;
    },

    toggleFilterValue(col, val) {
      const cur = this.chartFilters[col] || [];
      if (cur.includes(val)) {
        this.chartFilters[col] = cur.filter(v => v !== val);
      } else {
        this.chartFilters[col] = [...cur, val];
      }
      if (this.chartFilters[col].length === 0) delete this.chartFilters[col];
      this.chartFilters = { ...this.chartFilters };
      this._refreshFilterOptions();
      this._schedulePreview(true);
    },

    removeFilterValue(col, val) {
      this.chartFilters[col] = (this.chartFilters[col] || []).filter(v => v !== val);
      if (this.chartFilters[col].length === 0) delete this.chartFilters[col];
      this.chartFilters = { ...this.chartFilters };
      this._refreshFilterOptions();
      this._schedulePreview(true);
    },

    clearAllFilters() {
      this.chartFilters = {};
      this._refreshFilterOptions();
      this._schedulePreview(true);
    },

    _refreshFilterOptions() {
      if (this.sourceType === 'query' && this.savedQueryId) this.loadFilterOptions();
      else if (this.sourceType === 'columns' && this.explorerSelected.length > 0) this._loadExplorerFilterOptions();
      else if (this.sourceType === 'dataset' && this.datasetSourceId) this._loadDatasetFilterOptions();
    },

    /* ─── Progress Growth scale helpers ─────────────────── */
    _pgAutoScale() {
      const needed = [];
      if (this.pgBeforeCol && !this.filterOptions[this.pgBeforeCol]) needed.push(this.pgBeforeCol);
      if (this.pgAfterCol  && !this.filterOptions[this.pgAfterCol])  needed.push(this.pgAfterCol);

      if (needed.length > 0) {
        const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
        fetch('/dataviz/api/columns/filter-options/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrf,
          },
          body: JSON.stringify({
            columns: needed,
            filters: this.cleanSourceFilters(),
          })
        })
        .then(r => r.json())
        .then(d => {
          if (d.filter_options) {
            this.filterOptions = { ...this.filterOptions, ...d.filter_options };
            this._pgAutoScale();
          }
        })
        .catch(e => console.error('Fetch extra filter options:', e));
        return;
      }

      const _KNOWN_PL_SCALES = [
        ['Well Below Benchmark', 'Below Benchmark', 'At Benchmark', 'Above Benchmark'],
        ['Not Met', 'Nearly Met', 'Met', 'Exceeded'],
        ['Far Below Basic', 'Below Basic', 'Basic', 'Proficient', 'Advanced'],
        ['Below Basic', 'Basic', 'Proficient', 'Advanced'],
        ['1', '2', '3', '4'],
      ];
      const _BLANK = new Set(['', '-', '—', '–', 'N/A', 'n/a', 'None', 'null', 'NULL']);
      const valsB = (this.filterOptions[this.pgBeforeCol] || []).map(v => String(v).trim()).filter(v => !_BLANK.has(v));
      const valsA = (this.filterOptions[this.pgAfterCol]  || []).map(v => String(v).trim()).filter(v => !_BLANK.has(v));
      const valSet = new Set([...valsB, ...valsA]);
      if (!valSet.size) return;
      let ordered = null;
      for (const scale of _KNOWN_PL_SCALES) {
        if ([...valSet].every(v => scale.includes(v))) {
          ordered = scale.filter(s => valSet.has(s));
          break;
        }
      }
      if (!ordered) ordered = [...valSet].sort();
      const detected = ordered.join(', ');
      if (!this.pgScaleText || this.pgScaleText === this._pgLastAutoScale) {
        this.pgScaleText = detected;
        this._pgLastAutoScale = detected;
      }
    },

    _pgScaleDrop(toIdx) {
      const from = this._pgDragIdx;
      if (from === null || from === toIdx) {
        this._pgDragIdx = null; this._pgDragOverIdx = null;
        return;
      }
      const items = [...this.pgScaleParsed];
      const [moved] = items.splice(from, 1);
      items.splice(toIdx, 0, moved);
      this.pgScaleText = items.join(', ');
      this._pgLastAutoScale = '';
      this._pgDragIdx = null; this._pgDragOverIdx = null;
      this._schedulePreview(true);
    },

    _pgScaleRemove(i) {
      const items = [...this.pgScaleParsed];
      items.splice(i, 1);
      this.pgScaleText = items.join(', ');
      this._pgLastAutoScale = '';
      this._schedulePreview(true);
    },

    /* ─── Year context ───────────────────────────────────── */
    onYearContextChange() {
      if (this.yearContext) {
        const f = { ...this.chartFilters, school_year: this.yearContext };
        delete f._custom_years;
        this.chartFilters = f;
      } else {
        const f = { ...this.chartFilters };
        delete f.school_year;
        this.chartFilters = f;
      }
      if (this.sourceType === 'query' && this.savedQueryId) {
        this.loadColumns();
      } else {
        this._refreshFilterOptions();
      }
      this._schedulePreview(true);
    },

    toggleCustomYear(year) {
      let years = this.chartFilters._custom_years || [];
      if (years.includes(year)) {
        years = years.filter(y => y !== year);
      } else {
        years = [...years, year];
      }
      if (years.length > 0) {
        this.chartFilters = { ...this.chartFilters, _custom_years: years, school_year: years };
        if (years.length > 1 && this.pickerLayout === 'year_first') {
          this.pickerLayout = 'assessment_first';
          localStorage.setItem('dwPickerLayout', 'assessment_first');
        }
      } else {
        const f = { ...this.chartFilters };
        delete f._custom_years;
        delete f.school_year;
        this.chartFilters = f;
      }
      this._refreshFilterOptions();
      this._schedulePreview(true);
    },

    /* ─── Query summary ──────────────────────────────────── */
    buildQuerySummary() {
      const parts = [];
      if (this.sourceType === 'query') {
        const sel = document.querySelector('[x-model="savedQueryId"]');
        const qName = sel?.selectedOptions?.[0]?.textContent || 'a saved query';
        parts.push(`From: ${qName}`);
      } else if (this.sourceType === 'sql') {
        parts.push('From: custom SQL query');
      } else if (this.sourceType === 'columns') {
        parts.push(`${this.explorerCols.length} columns selected`);
      } else if (this.sourceType === 'dataset') {
        const sel = document.querySelector('[x-model="datasetSourceId"]');
        const dsName = sel?.selectedOptions?.[0]?.textContent || 'a dataset';
        parts.push(`Dataset: ${dsName}`);
      }
      if (this.yearContext) {
        const label = this.aliasResolvedLabels[this.yearContext] || this.yearContext;
        parts.push(`Year: ${label}`);
      }
      parts.push(`School: ${this.schoolFilterMode === 'tested' ? 'Tested/Assessment' : 'Enrolled'}`);
      if (this.activeCols.length) {
        const plCount = this.activeCols.filter(c => c.startsWith('test_') && c.endsWith('_PL')).length;
        const ssCount = this.activeCols.filter(c => c.startsWith('test_') && c.endsWith('_SS')).length;
        if (plCount || ssCount) parts.push(`Assessments: ${ssCount} SS, ${plCount} PL columns`);
      }
      return parts.join(' | ') || 'Configure a data source to see summary';
    },

    /* ─── Clean source_filters before save ───────────────── */
    cleanSourceFilters() {
      if (!this.chartFilters || typeof this.chartFilters !== 'object') return {};
      const { _custom_years, ...clean } = this.chartFilters;
      return clean;
    },
  };
}
