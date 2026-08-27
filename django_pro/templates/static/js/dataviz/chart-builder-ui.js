/* chart-builder-ui.js — Alpine component methods: column helpers, drag & drop, style live-apply, editor, init, scheduling */

function chartBuilderUIMethods() {
  return {
    _autoTimer: null,
    _styleApplyTimer: null,
    _editorTimer: null,
    _editorFiring: false,
    _lastPreviewTime: 0,

    /* ─── Column assignment helpers ─────────────────────────── */
    assignedTo(col) {
      if (this.xCols.includes(col)) return 'X';
      if (col === this.yCol) return 'Y';
      if (this.seriesCols.includes(col)) return 'S';
      return '';
    },
    togglePlCol(col) {
      const idx = this.comparePlCols.indexOf(col);
      if (idx >= 0) {
        this.comparePlCols.splice(idx, 1);
      } else {
        this.comparePlCols.push(col);
      }
      this.comparePlCols = [...this.comparePlCols];
      this._schedulePreview(true);
    },
    toggleStRowCol(col) {
      const idx = this.stRowCols.indexOf(col);
      if (idx >= 0) {
        this.stRowCols.splice(idx, 1);
      } else {
        this.stRowCols.push(col);
      }
      this.stRowCols = [...this.stRowCols];
      this._schedulePreview(true);
    },
    toggleStColCol(col) {
      const idx = this.stColCols.indexOf(col);
      if (idx >= 0) {
        this.stColCols.splice(idx, 1);
      } else {
        this.stColCols.push(col);
      }
      this.stColCols = [...this.stColCols];
      this._schedulePreview(true);
    },
    toggleDynRowCol(col) {
      const idx = this.dynRowCols.indexOf(col);
      if (idx >= 0) {
        this.dynRowCols.splice(idx, 1);
      } else {
        this.dynRowCols.push(col);
      }
      this.dynRowCols = [...this.dynRowCols];
      this._schedulePreview(true);
    },
    toggleDynColCol(col) {
      const idx = this.dynColCols.indexOf(col);
      if (idx >= 0) {
        this.dynColCols.splice(idx, 1);
      } else {
        this.dynColCols.push(col);
      }
      this.dynColCols = [...this.dynColCols];
      this._schedulePreview(true);
    },
    addDynCalculation() {
      this._dynCalcSeq = (this._dynCalcSeq || 0) + 1;
      this.dynCalculations.push({
        id: 'c' + this._dynCalcSeq,
        calc_type: 'base',
        value_col: '',
        agg_func: 'count',
        label: '',
        decimals: null,
        visible: true,
        filter: null,
        filters: [],
        op: 'divide',
        ref_a: '',
        ref_b: '',
        as_percent: false
      });
      this.dynCalculations = [...this.dynCalculations];
      this._schedulePreview(true);
    },
    removeDynCalculation(idx) {
      const removedId = this.dynCalculations[idx]?.id;
      this.dynCalculations.splice(idx, 1);
      // Drop derived columns that referenced the removed column — a dangling
      // reference would silently resolve to zero, more confusing than clearing
      // the reference so the user re-picks.
      this.dynCalculations.forEach(c => {
        if (c.calc_type === 'derived') {
          if (c.ref_a === removedId) c.ref_a = '';
          if (c.ref_b === removedId) c.ref_b = '';
        }
      });
      this.dynCalculations = [...this.dynCalculations];
      this._schedulePreview(true);
    },
    moveDynCalculation(idx, direction) {
      const next = idx + direction;
      if (next < 0 || next >= this.dynCalculations.length) return;
      const items = [...this.dynCalculations];
      [items[idx], items[next]] = [items[next], items[idx]];
      this.dynCalculations = items;
      this._schedulePreview(true);
    },
    dynCalcLabel(calc) {
      if (!calc) return '';
      const lbl = (calc.label || '').trim();
      if (lbl) return lbl;
      if (calc.calc_type === 'derived') {
        return 'Calc ' + String(calc.id || '').toUpperCase();
      }
      const fn = calc.agg_func || 'count';
      const vc = calc.value_col || '';
      if (!vc) return fn.charAt(0).toUpperCase() + fn.slice(1);
      return fn.charAt(0).toUpperCase() + fn.slice(1) + ' of ' + this.colLabel(vc);
    },
    dynCalcRefOptions(idx) {
      // Calculation columns a derived column at *idx* can reference — every
      // other calculation column (base or derived), in display order.
      return this.dynCalculations
        .map((c, i) => ({ c, i }))
        .filter(o => o.i !== idx && this.dynCalcLabel(o.c))
        .map(o => ({ id: o.c.id, label: this.dynCalcLabel(o.c) }));
    },
    toggleDynCalcFilter(calc) {
      const filters = this.dynCalcFilters(calc);
      if (filters.length) calc.filters = [];
      else calc.filters = [{ col: calc.value_col || '', op: 'lt', val: '' }];
      this.dynCalculations = [...this.dynCalculations];
      this._schedulePreview(true);
    },
    dynCalcFilters(calc) {
      if (!Array.isArray(calc.filters)) {
        calc.filters = calc.filter && typeof calc.filter === 'object' ? [calc.filter] : [];
      }
      return calc.filters;
    },
    addDynCalcFilter(calc) {
      this.dynCalcFilters(calc).push({ col: calc.value_col || '', op: 'lt', val: '', logic: 'and' });
      this.dynCalculations = [...this.dynCalculations];
      this._schedulePreview(true);
    },
    removeDynCalcFilter(calc, filterIdx) {
      this.dynCalcFilters(calc).splice(filterIdx, 1);
      this.dynCalculations = [...this.dynCalculations];
      this._schedulePreview(true);
    },
    dynFilterValueOptions(col) {
      // Distinct values for the calc filter column, used to prepopulate the
      // "equals / not equals" dropdown.  Lazy-fetched when not already loaded.
      if (!col) return [];
      if (!this.filterOptions[col]) this._loadCalcFilterOptions(col);
      return this.filterOptions[col] || [];
    },
    _loadCalcFilterOptions(col) {
      if (this._calcFilterLoading && this._calcFilterLoading[col]) return;
      this._calcFilterLoading = { ...(this._calcFilterLoading || {}), [col]: true };
      const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
      fetch('/dataviz/api/columns/filter-options/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ columns: [col], filters: this.cleanSourceFilters ? this.cleanSourceFilters() : {} })
      })
        .then(r => r.json())
        .then(d => {
          if (d.filter_options) {
            this.filterOptions = { ...this.filterOptions, ...d.filter_options };
          }
        })
        .catch(e => console.error('Fetch calc filter options:', e))
        .finally(() => {
          const l = { ...(this._calcFilterLoading || {}) };
          delete l[col];
          this._calcFilterLoading = l;
        });
    },
    addDsLine(lineType) {
      this._dsLineSeq = (this._dsLineSeq || 0) + 1;
      const id = 'l' + this._dsLineSeq;
      if (lineType === 'calc') {
        const baseLines = this.dsLines.filter(l => l.line_type !== 'calc');
        this.dsLines.push({
          id, line_type: 'calc', label: '',
          op: 'subtract', as_percent: false,
          line_a: baseLines[0]?.id || '',
          line_b: baseLines[1]?.id || '',
        });
      } else if (lineType === 'active_range') {
        this.dsLines.push({ id, line_type: 'active_range', label: '', enter_col: '', leave_col: '' });
      } else {
        this.dsLines.push({ id, line_type: 'base', label: '', agg_func: 'count', value_col: '' });
      }
      this.dsLines = [...this.dsLines];
      this._schedulePreview(true);
    },
    removeDsLine(idx) {
      const removedId = this.dsLines[idx]?.id;
      this.dsLines.splice(idx, 1);
      // Drop calc lines that referenced the removed line — a dangling
      // reference would silently resolve to a zero-value line, which is
      // more confusing than just clearing the calc so the user re-picks.
      this.dsLines.forEach(l => {
        if (l.line_type === 'calc') {
          if (l.line_a === removedId) l.line_a = '';
          if (l.line_b === removedId) l.line_b = '';
        }
      });
      this.dsLines = [...this.dsLines];
      this._schedulePreview(true);
    },
    plColLabel(col) {
      return this.colLabel(col);
    },
    colLabel(col) {
      if (!col) return '';
      if (this.explorerStructure?.definitions?.[col]) {
        return this.explorerStructure.definitions[col];
      }
      const norm = this._dvNormaliseKey(col);
      if (this.explorerStructure?.definitions?.[norm]) {
        let label = this.explorerStructure.definitions[norm];
        const prefix = col.startsWith('test_') ? 'test_' : col.startsWith('mark_') ? 'mark_' : null;
        if (prefix) {
          const rest = col.slice(prefix.length);
          for (const [yearUnder, alias] of Object.entries(this.dvYearAliasMap)) {
            if (rest.startsWith(alias + '_')) {
              const yearDash = yearUnder.replace('_', '-');
              const yearLabel = this.explorerStructure?.year_labels?.[yearDash] || yearDash;
              return `${label} (${yearLabel})`;
            }
          }
        }
        return label;
      }
      if (this.filterLabels[col]) return this.filterLabels[col];
      const cleanCol = col.split('.').pop();
      if (this.filterLabels[cleanCol]) return this.filterLabels[cleanCol];
      let lbl = col.replace(/^(test_|mark_)/, '').replace(/_/g, ' ');
      return lbl.charAt(0).toUpperCase() + lbl.slice(1);
    },

    setSource(type) {
      this.sourceType = type;
      this.columns = []; this.rawSqlColumns = []; this.explorerCols = []; this.colSearch = '';
      this.filterOptions = {}; this.filterLabels = {}; this.openFilter = '';
      this.filterColsList = [];
      this.stRowCols = []; this.stColCols = [];
      const yearCtx = this.yearContext;
      const schoolMode = this.schoolFilterMode;
      const customYears = this.chartFilters._custom_years;
      this.chartFilters = {};
      if (yearCtx) {
        this.chartFilters.school_year = yearCtx;
      } else if (customYears && customYears.length > 0) {
        this.chartFilters._custom_years = [...customYears];
        this.chartFilters.school_year = [...customYears];
      }
      this.schoolFilterMode = schoolMode;
      if (type === 'columns' && !Object.keys(this.explorerStructure?.definitions || {}).length) this.loadExplorerStructure();
      if (type === 'dataset' && this.datasetSourceId) this.loadDatasetColumns();
    },

    /* ─── Drag & Drop ────────────────────────────────────────── */
    onDragStart(ev, col) {
      this.draggedCol = col;
      ev.dataTransfer.setData('text/plain', col);
      ev.dataTransfer.effectAllowed = 'copy';
      requestAnimationFrame(() => ev.target.classList.add('dragging'));
    },
    onDragEnd() {
      this.draggedCol = '';
      this.dragTarget = '';
      document.querySelectorAll('.col-chip.dragging').forEach(el => el.classList.remove('dragging'));
    },
    doDrop(target) {
      if (this.draggedCol) this[target] = this.draggedCol;
      this.draggedCol = '';
      this.dragTarget = '';
    },
    doDropMulti(arrName) {
      if (this.draggedCol && !this[arrName].includes(this.draggedCol)) {
        this[arrName].push(this.draggedCol);
        if (arrName === 'xCols') this._syncXCols();
        if (arrName === 'seriesCols') this._syncSeriesCols();
      }
      this.draggedCol = '';
      this.dragTarget = '';
    },
    _syncXCols() {
      this.xCols = [...this.xCols];
      this._schedulePreview(true);
    },
    _syncSeriesCols() {
      this.seriesCols = [...this.seriesCols];
      this._schedulePreview(true);
    },

    /* ─── Interactive Layout Editor ─────────────────────────── */
    toggleEditMode() {
      this.editMode = !this.editMode;
      if (this.editMode) {
        if (this.previewHtml) {
          this.$nextTick(() => this._attachEditor());
        } else if (this.canPreview) {
          this.doPreview();
        }
      } else {
        this.showStylePanel = false;
        if (this._editorInstance) { this._editorInstance.destroy(); this._editorInstance = null; }
      }
    },

    _attachEditor() {
      if (this._editorInstance) { this._editorInstance.destroy(); this._editorInstance = null; }
      const c = this.$refs.previewContainer;
      if (!c) return;
      const chartEl = c.querySelector('.chart-container');
      if (!chartEl || !chartEl._echartsInstance) return;
      this._editorInstance = ChartEditor.attach(chartEl, (cfg) => {
        this._editorFiring = true;
        if (cfg.grid_left        != null) this.gridLeft        = cfg.grid_left;
        if (cfg.grid_right       != null) this.gridRight       = cfg.grid_right;
        if (cfg.grid_top         != null) this.gridTop         = cfg.grid_top;
        if (cfg.grid_bottom      != null) this.gridBottom      = cfg.grid_bottom;
        if (cfg.title_left       != null) this.titleLeft       = cfg.title_left;
        if (cfg.title_top        != null) this.titleTop        = cfg.title_top;
        if (cfg.title_font_size  != null) this.titleFontSize   = cfg.title_font_size;
        if (cfg.bar_category_gap != null) { this.barCategoryGap  = cfg.bar_category_gap; this.groupGap = cfg.bar_category_gap; }
        if (cfg.label_distance   != null) this.labelDistance   = cfg.label_distance;
        if (cfg.legend_left      != null) this.legendLeft      = cfg.legend_left;
        if (cfg.legend_top       != null) this.legendTop       = cfg.legend_top;
        if (cfg.legend_item_gap  != null) this.legendItemGap   = cfg.legend_item_gap;
        if (cfg.legend_font_size != null) this.legendFontSize  = cfg.legend_font_size;
        if (cfg.legend_icon_size  != null) this.legendIconSize  = cfg.legend_icon_size;
        if (cfg.container_width  != null) this.containerWidth  = cfg.container_width;
        if (cfg.container_height != null) this.containerHeight = cfg.container_height;
        this._editorFiring = false;
      });
    },

    /* ─── Scheduling & Live Style Update ────────────────────── */
    init() {
      if (Array.isArray(this.chartFilters.school_year)) {
        this.chartFilters = { ...this.chartFilters, _custom_years: [...this.chartFilters.school_year] };
      }
      if (this.savedQueryId) this.loadColumns();
      if (this.sourceType === 'dataset' && this.datasetSourceId) this.loadDatasetColumns();
      if (this.sourceType === 'columns') this.loadExplorerStructure();
      this._recomputeFilterCols();

      const dataProps = ['chartType','barLayout','barOrientation','barRadial','barPolar','lineLayout','lineFill','xCols','xCol','yCol','seriesCols','seriesCol','groupCol','groupTotal','groupTotalLabel','comparePlCols','aggFunc','aggDecimals',
                         'sortCol','sortDir','categoryOrder','limit','palette','showPercent','showPercentSymbol','percentDecimals','stackOrder',
                         'stRowCol','stColCol','stRowCols','stColCols','stAggFunc','stValueCol','stAggDecimals','explorerSelected',
                         'acIncludeCats','acIncludePl','acColFilters',
                         'compareGap','showCategories','showAxisTitles',
                         'tooltipShowPct','tooltipShowCount','tooltipValuePct',
                         'parliamentDotScale','parliamentShape','parliamentRows','parliamentRowsAuto','parliamentShowSS','groupGapPct',
                         'centerGroupLabels'];
      const styleProps = ['chartTitle','titleFontSize','showLegend','showLabels',
                          'legendPos','legendItemGap','legendFontSize','legendIconSize','legendOffset',
                          'labelPosition','labelFontSize','background','opacity',
                          'barBorderRadius','showPoints','smooth',
                          'showZoom','showToolbox','showGrid','showAxisLabels','enableAnimation','showTooltip',
                          'xLabelRotation','xLabelFontSize','xLabelMaxLength','forceShowAllLabels','barCategoryGap','yAxisMin','yAxisMax',
                          'compareCatFontSize','compareCatRotation','groupGap',
                          'colorMap'];
      dataProps.forEach(prop => this.$watch(prop, () => this._scheduleDataPreview()));
      styleProps.forEach(prop => this.$watch(prop, () => this._scheduleStylePreview()));
      this.$watch('explorerSelected', () => { this._explorerKeySetVersion = (this._explorerKeySetVersion || 0) + 1; });
      this._explorerKeySetVersion = 0;
    },

    _schedulePreview(forData) {
      // forData=true → full server round-trip; false/undefined → try fast style path first
      if (forData || !this.previewHtml) {
        this._scheduleDataPreview();
      } else {
        this._scheduleStylePreview();
      }
    },
    _scheduleStylePreview() {
      if (this._editorFiring) return;
      if (this.previewHtml) {
        clearTimeout(this._styleApplyTimer);
        this._styleApplyTimer = setTimeout(() => this._applyStyleLive(), 80);
        return;
      }
      clearTimeout(this._autoTimer);
      this._autoTimer = setTimeout(() => {
        if (this.canPreview && !this.previewLoading) this.doPreview();
      }, 400);
    },
    _scheduleDataPreview() {
      if (this._editorFiring) return;
      clearTimeout(this._autoTimer);
      this._autoTimer = setTimeout(() => {
        if (this.canPreview && !this.previewLoading) this.doPreview();
      }, 250);
    },

    _applyStyleLive() {
      const c = this.$refs.previewContainer;
      if (!c) return;
      const chartEl = c.querySelector('.chart-container');
      if (!chartEl || !chartEl._echartsInstance) return;
      const chart = chartEl._echartsInstance;
      const opt = {};

      const bgMap = { white: '#ffffff', light: '#f8fafc', dark: '#1e293b' };
      opt.backgroundColor = bgMap[this.background] || '#ffffff';

      opt.title = [{
        text: this.chartTitle || '',
        textStyle: { fontSize: +this.titleFontSize || 16 },
        ...(this.titleLeft != null ? { left: this.titleLeft } : {}),
        ...(this.titleTop  != null ? { top:  this.titleTop  } : {}),
      }];

      if (this.showLegend) {
        const lgOpt = {
          show: true,
          itemGap:    +this.legendItemGap || 10,
          itemWidth:  +this.legendIconSize || 10,
          itemHeight: +this.legendIconSize || 10,
          textStyle:  { fontSize: +this.legendFontSize || 11 },
        };
        if (this.legendLeft != null && this.legendTop != null) {
          lgOpt.left = this.legendLeft;
          lgOpt.top  = this.legendTop;
        } else {
          const posMap = { top: 'top', bottom: 'bottom', left: 'left', right: 'right' };
          if (posMap[this.legendPos]) {
            lgOpt.orient = ['left','right'].includes(this.legendPos) ? 'vertical' : 'horizontal';
            lgOpt[this.legendPos] = +this.legendOffset || 0;
            if (['left','right'].includes(this.legendPos)) { lgOpt.top = 'middle'; lgOpt.bottom = undefined; }
            else { lgOpt.left = 'center'; lgOpt.right = undefined; }
          }
        }
        opt.legend = [lgOpt];
      } else {
        opt.legend = [{ show: false }];
      }

      opt.tooltip  = [{ show: !!this.showTooltip }];
      opt.animation = !!this.enableAnimation;
      opt.toolbox = [{ show: !!this.showToolbox, feature: { saveAsImage: {}, dataView: {}, restore: {} } }];

      const curOpt = chart.getOption();
      if (curOpt.dataZoom && curOpt.dataZoom.length) {
        opt.dataZoom = curOpt.dataZoom.map(dz => ({ ...dz, show: !!this.showZoom }));
      }

      const noAxis = ['pie','donut','funnel','treemap','radar','gauge'].includes(this.chartType);
      if (!noAxis) {
        const catLblShow = !!this.showCategories;
        const valLblShow = !!this.showAxisLabels;
        const catInterval = this.forceShowAllLabels ? 0 : 'auto';
        const hasGroupBands = !!(chart._groupLabelBands && chart._groupLabelBands.length);
        if (hasGroupBands) {
          chart._groupLabelBands.forEach(b => {
            b.show = catLblShow;
            b.fontSize = +this.compareCatFontSize || 12;
            b.rotate = +this.compareCatRotation || 0;
          });
        }
        if (Array.isArray(curOpt.xAxis) && curOpt.xAxis.length > 1) {
          let offset = this.chartType === 'assessment_compare' ? 22 : 30;
          if (this.xLabelRotation > 0) {
            const rad = (this.xLabelRotation * Math.PI) / 180;
            const baseFontSize = this.chartType === 'assessment_compare' ? 10 : 11;
            const textWidth = 12 * (this.xLabelFontSize || baseFontSize) * 0.6;
            const heightContrib = textWidth * Math.sin(rad) + (this.xLabelFontSize || baseFontSize) * Math.cos(rad);
            offset = Math.max(offset, Math.round(heightContrib + 12));
          }
          if (hasGroupBands) {
            chart._groupLabelBands.forEach(b => { b.offset = offset; });
          }
          opt.xAxis = [
            { axisLabel: { show: catLblShow, interval: 0, rotate: +this.xLabelRotation || 0, fontSize: +this.xLabelFontSize || (this.chartType === 'assessment_compare' ? 10 : 11) } },
            { offset: offset, axisLabel: { show: hasGroupBands ? false : catLblShow, interval: 0, rotate: +this.compareCatRotation || 0, fontSize: +this.compareCatFontSize || 12 } },
          ];
          opt.yAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow } }];
        } else if (Array.isArray(curOpt.yAxis) && curOpt.yAxis.length > 1) {
          let offset = 60;
          if (this.xLabelRotation > 0) {
            const rad = (this.xLabelRotation * Math.PI) / 180;
            const textWidth = 12 * (this.xLabelFontSize || 11) * 0.6;
            const heightContrib = textWidth * Math.sin(rad) + (this.xLabelFontSize || 11) * Math.cos(rad);
            offset = Math.max(offset, Math.round(heightContrib + 12));
          }
          if (hasGroupBands) {
            chart._groupLabelBands.forEach(b => { b.offset = offset; });
          }
          opt.yAxis = [
            { axisLabel: { show: catLblShow, rotate: +this.xLabelRotation || 0, fontSize: +this.xLabelFontSize || 11 } },
            { offset: offset, axisLabel: { show: hasGroupBands ? false : catLblShow, interval: 0, rotate: +this.compareCatRotation || 0, fontSize: +this.compareCatFontSize || 12 } },
          ];
          opt.xAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow },
            min: this.yAxisMin !== '' && this.yAxisMin != null ? +this.yAxisMin : undefined,
            max: this.yAxisMax !== '' && this.yAxisMax != null ? +this.yAxisMax : undefined }];
        } else if (this.chartType === 'bar' && this.barRadial) {
          opt.angleAxis = [{ axisLabel: { show: catLblShow, rotate: +this.xLabelRotation || 0, fontSize: +this.xLabelFontSize || 11, interval: catInterval } }];
          opt.radiusAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow },
            min: this.yAxisMin !== '' && this.yAxisMin != null ? +this.yAxisMin : undefined,
            max: this.yAxisMax !== '' && this.yAxisMax != null ? +this.yAxisMax : undefined }];
        } else if (this.chartType === 'bar' && this.barPolar) {
          opt.radiusAxis = [{ axisLabel: { show: catLblShow, rotate: +this.xLabelRotation || 0, fontSize: +this.xLabelFontSize || 11, interval: catInterval } }];
          opt.angleAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow },
            min: this.yAxisMin !== '' && this.yAxisMin != null ? +this.yAxisMin : undefined,
            max: this.yAxisMax !== '' && this.yAxisMax != null ? +this.yAxisMax : undefined }];
        } else if (this.chartType === 'bar' && this.barOrientation === 'horizontal') {
          opt.yAxis = [{ axisLabel: { show: catLblShow, rotate: +this.xLabelRotation || 0, fontSize: +this.xLabelFontSize || 11, interval: catInterval } }];
          opt.xAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow },
            min: this.yAxisMin !== '' && this.yAxisMin != null ? +this.yAxisMin : undefined,
            max: this.yAxisMax !== '' && this.yAxisMax != null ? +this.yAxisMax : undefined }];
        } else {
          opt.xAxis = [{ axisLabel: { show: catLblShow, rotate: +this.xLabelRotation || 0, fontSize: +this.xLabelFontSize || 11, interval: catInterval } }];
          opt.yAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow },
            min: this.yAxisMin !== '' && this.yAxisMin != null ? +this.yAxisMin : undefined,
            max: this.yAxisMax !== '' && this.yAxisMax != null ? +this.yAxisMax : undefined }];
        }
      }

      const series = curOpt.series || [];
      const updSeries = series.map(s => {
        if (!s) return {};
        const u = {};
        if (s.type === 'bar') {
          u.barCategoryGap = (this.barCategoryGap || 20) + '%';
          u.itemStyle = { borderRadius: +this.barBorderRadius || 0, opacity: this.opacity != null ? +this.opacity : 1 };
          u.label = { show: !!this.showLabels, position: this.labelPosition || 'top', fontSize: +this.labelFontSize || 10 };
        }
        if (s.type === 'line') {
          u.smooth = !!this.smooth;
          u.showSymbol = !!this.showPoints;
          u.itemStyle = { opacity: this.opacity != null ? +this.opacity : 1 };
          if (this.showLabels) u.label = { show: true, position: this.labelPosition || 'top', fontSize: +this.labelFontSize || 10 };
          else u.label = { show: false };
        }
        if (s.type === 'pie') {
          u.itemStyle = { opacity: this.opacity != null ? +this.opacity : 1 };
          u.label = { show: !!this.showLabels, fontSize: +this.labelFontSize || 10 };
        }
        return u;
      });
      if (updSeries.length) opt.series = updSeries;

      chart.setOption(opt, { replaceMerge: ['dataZoom'] });
      if (typeof window.applyCenteredGroupLabels === 'function') {
        window.applyCenteredGroupLabels(chart);
      }

      if (this._editorInstance) {
        this._editorInstance.destroy();
        this._editorInstance = null;
      }
      this.$nextTick(() => this._attachEditor());
    },
  };
}
