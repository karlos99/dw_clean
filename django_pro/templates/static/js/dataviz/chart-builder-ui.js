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
      if (this.guidedMode) this.guidedStep = 1;
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
        if (cfg.bar_category_gap != null) this.barCategoryGap  = cfg.bar_category_gap;
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

      const dataProps = ['chartType','barLayout','barOrientation','barRadial','barPolar','lineLayout','lineFill','xCols','xCol','yCol','seriesCols','seriesCol','groupCol','comparePlCols','aggFunc','aggDecimals',
                         'sortCol','sortDir','categoryOrder','limit','palette','showPercent','showPercentSymbol','percentDecimals','stackOrder',
                         'stRowCol','stColCol','stRowCols','stColCols','stAggFunc','stValueCol','stAggDecimals','explorerSelected',
                         'acIncludeCats','acIncludePl','acColFilters',
                         'compareGap','compareCatFontSize','compareCatRotation','showCategories','showAxisTitles',
                         'tooltipShowPct','tooltipShowCount','tooltipValuePct',
                         'parliamentDotScale','parliamentShape','parliamentRows','parliamentRowsAuto','parliamentShowSS'];
      const styleProps = ['chartTitle','titleFontSize','showLegend','showLabels',
                          'legendPos','legendItemGap','legendFontSize','legendIconSize','legendOffset',
                          'labelPosition','labelFontSize','background','opacity',
                          'barBorderRadius','showPoints','smooth',
                          'showZoom','showToolbox','showGrid','showAxisLabels','enableAnimation','showTooltip',
                          'xLabelRotation','xLabelFontSize','xLabelMaxLength','forceShowAllLabels','barCategoryGap','yAxisMin','yAxisMax',
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
        if (this.chartType === 'bar' && this.barRadial) {
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
        } else if (this.chartType === 'assessment_compare') {
          opt.xAxis = [
            { axisLabel: { show: catLblShow, interval: 0, fontSize: +this.xLabelFontSize || 10 } },
            { axisLabel: { show: catLblShow, interval: 0 } },
          ];
          opt.yAxis = [{ splitLine: { show: !!this.showGrid }, axisLabel: { show: valLblShow } }];
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

      if (this._editorInstance) {
        this._editorInstance.destroy();
        this._editorInstance = null;
      }
      this.$nextTick(() => this._attachEditor());
    },
  };
}
