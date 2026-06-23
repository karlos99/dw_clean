/* chart-builder-preview.js — Alpine component methods: preview, save, save-and-attach, recommendations, describe, config builder, flash */

function chartBuilderPreviewMethods(urls) {
  // urls injected from the template: { preview, save, saveAndAttach, recommend, describe }
  function getCsrf() {
    return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  }

  return {
    _lastPreviewTime: 0,
    _previewAbortCtrl: null,

    /* ─── Preview ────────────────────────────────────────── */
    async doPreview() {
      if (!this.canPreview) return;
      const now = Date.now();
      if (now - this._lastPreviewTime < 400) {
        clearTimeout(this._autoTimer);
        this._autoTimer = setTimeout(() => {
          if (this.canPreview && !this.previewLoading) this.doPreview();
        }, 400 - (now - this._lastPreviewTime) + 20);
        return;
      }
      this._lastPreviewTime = now;

      // Cancel any in-flight preview request
      if (this._previewAbortCtrl) this._previewAbortCtrl.abort();
      this._previewAbortCtrl = new AbortController();

      // Don't blank previewHtml — keep the old preview visible so the user
      // never sees a flash and the fast style path stays active between fetches.
      this.previewLoading = true;
      try {
        const fd = new FormData(document.getElementById('chart-form'));
        const r = await fetch(urls.preview, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCsrf() },
          body: fd,
          signal: this._previewAbortCtrl.signal,
        });
        const html = await r.text();
        this.previewHtml = html;
        this.previewLoading = false;
        this.$nextTick(() => {
          const c = this.$refs.previewContainer;
          if (c) {
            c.innerHTML = html;
            if (this.containerWidth || this.containerHeight) {
              const cc = c.querySelector('.chart-container');
              if (cc) {
                if (this.containerWidth)  cc.style.width  = this.containerWidth  + 'px';
                if (this.containerHeight) cc.style.height = this.containerHeight + 'px';
              }
            }
            if (window.initAllCharts) window.initAllCharts();
            const el = c.querySelector('[data-series-names]');
            if (el) {
              try {
                const names = JSON.parse(el.dataset.seriesNames || '[]');
                this.stackOrderAvail = names;
                if (this.stackOrder.length !== names.length || !names.every(n => this.stackOrder.includes(n))) {
                  this.stackOrder = [...names];
                }
              } catch (_) {}
              try {
                const categories = JSON.parse(el.dataset.categoryNames || '[]');
                if (categories.length) {
                  this.categoryOrderAvail = categories;
                  if (this.categoryOrder.length !== categories.length || !categories.every(c => this.categoryOrder.includes(c))) {
                    this.categoryOrder = [...categories];
                  }
                } else {
                  this.categoryOrderAvail = [];
                }
              } catch (_) {}
              try {
                const availCats = JSON.parse(el.dataset.acAvailCats || '[]');
                const availPl   = JSON.parse(el.dataset.acAvailPl   || '[]');
                if (availCats.length) this.acAvailCats = availCats;
                if (availPl.length)   this.acAvailPl   = availPl;
              } catch (_) {}
              try {
                const queryCols = JSON.parse(el.dataset.queryColumns || '[]');
                this.queryColumns = queryCols.map(c => c.split('.').pop());
              } catch (_) {}
            }
          }
        });
        this.fetchAutoDescription();
        return;
      } catch (e) {
        if (e.name === 'AbortError') { this.previewLoading = false; return; }
        const msg = `<div class="text-red-600 text-sm p-4">Preview failed: ${e.message}</div>`;
        this.previewHtml = msg;
        if (this.$refs.previewContainer) this.$refs.previewContainer.innerHTML = msg;
      }
      this.previewLoading = false;
    },

    /* ─── Save ───────────────────────────────────────────── */
    async doSave() {
      if (!this.name.trim()) { this._flash('Enter a chart name first', true); return; }
      this.saving = true; this.statusMsg = '';
      try {
        const fd = new FormData(document.getElementById('chart-form'));
        const r = await fetch(urls.save, {
          method: 'POST', headers: { 'X-CSRFToken': getCsrf() }, body: fd,
        });
        if (r.ok) {
          this._flash('Saved!', false);
          const text = await r.text();
          try {
            const data = JSON.parse(text);
            if (data.chart?.id && !this.chartId) {
              this.chartId = data.chart.id;
              history.replaceState(null, '', `/dataviz/charts/${data.chart.id}/edit/`);
            }
          } catch (_) {
            const m = text.match(/charts\/(\d+)\/edit/);
            if (m && !this.chartId) {
              this.chartId = parseInt(m[1]);
              history.replaceState(null, '', `/dataviz/charts/${m[1]}/edit/`);
            }
          }
        } else {
          this._flash(await r.text() || 'Save failed', true);
        }
      } catch (e) { this._flash('Error: ' + e.message, true); }
      this.saving = false;
    },

    /* ─── Save & Add to Page ─────────────────────────────── */
    async doSaveAndAttach() {
      if (!this.name.trim()) { this._flash('Enter a chart name first', true); return; }
      if (!this.targetPageId) { this._flash('Select a target page', true); return; }
      this.saving = true;
      try {
        const fd = new FormData(document.getElementById('chart-form'));
        const payload = Object.fromEntries(fd.entries());
        payload.target_page_id = this.targetPageId;
        payload.chart_type = this.chartType;
        payload.config = this._buildConfig();
        payload.school_filter_mode = this.schoolFilterMode;
        payload.source_filters = this.cleanSourceFilters();
        payload.source_type = this.sourceType;
        if (this.sourceType === 'columns') payload.selected_columns = this.explorerSelected;
        if (this.chartId) payload.id = this.chartId;
        const r = await fetch(urls.saveAndAttach, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (data.success) {
          this._flash('Chart saved and added to page!', false);
          this.saveAndAttachOpen = false;
          this.targetPageId = '';
          if (data.chart?.id) {
            this.chartId = data.chart.id;
            if (!window.location.href.includes('/edit/')) {
              history.replaceState(null, '', `/dataviz/charts/${data.chart.id}/edit/`);
            }
          }
        } else {
          this._flash(data.error || 'Save failed', true);
        }
      } catch (e) {
        this._flash('Error: ' + e.message, true);
      }
      this.saving = false;
    },

    /* ─── Recommendations & Auto Description ─────────────── */
    async fetchRecommendations() {
      if (!this.hasData) return;
      this.recommendationsLoading = true;
      try {
        const r = await fetch(urls.recommend, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify({ columns: this.activeCols, column_definitions: {} }),
        });
        const data = await r.json();
        if (data.success) this.recommendedCharts = data.recommendations || [];
      } catch (_) {}
      this.recommendationsLoading = false;
    },

    async fetchAutoDescription() {
      if (!this.chartType) return;
      // Throttle: only re-fetch description every 30 seconds
      const now = Date.now();
      if (this._lastDescFetch && now - this._lastDescFetch < 30000) return;
      this._lastDescFetch = now;
      try {
        const cfg = this._buildConfig();
        const r = await fetch(urls.describe, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
          body: JSON.stringify({ chart_config: cfg, column_definitions: {} }),
        });
        const data = await r.json();
        if (data.success) this.autoDescription = data.description;
      } catch (_) {}
    },

    /* ─── Config builder ─────────────────────────────────── */
    _buildConfig() {
      const xColStr = this.xCols.join(',');
      const seriesColStr = this.seriesCols.join(',');
      return {
        chart_type: this.chartType,
        x_col: xColStr,
        y_col: this.yCol,
        series_col: seriesColStr,
        stack_col: seriesColStr,
        group_col: this.groupCol,
        compare_pl_cols: this.comparePlCols,
        ac_include_cats: this.acIncludeCats,
        ac_include_pl: this.acIncludePl,
        ac_col_filters: this.acColFilters,
        agg: this.aggFunc,
        agg_decimals: this.aggDecimals,
        pg_before_col: this.pgBeforeCol,
        pg_after_col: this.pgAfterCol,
        pl_scale: this.pgScaleParsed,
        pg_display: this.pgDisplay,
        st_row_col: this.stRowCol,
        st_col_col: this.stColCol,
        st_row_cols: this.stRowCols,
        st_col_cols: this.stColCols,
        st_agg_func: this.stAggFunc,
        st_value_col: this.stValueCol,
        st_agg_decimals: this.stAggDecimals,
        sort_col: this.sortCol,
        sort_dir: this.sortDir,
        limit: this.limit,
        palette: this.palette,
        legend_pos: this.showLegend ? this.legendPos : 'hidden',
        legend_item_gap: this.legendItemGap,
        legend_font_size: this.legendFontSize,
        legend_icon_size: this.legendIconSize,
        legend_offset: this.legendOffset,
        legend_left: this.legendLeft,
        legend_top: this.legendTop,
        show_legend: this.showLegend,
        data_labels: this.showLabels,
        label_position: this.labelPosition,
        label_font_size: this.labelFontSize,
        background: this.background,
        opacity: this.opacity,
        bar_border_radius: this.barBorderRadius,
        show_points: this.showPoints,
        smooth: this.smooth,
        color_map: this.colorMap,
        show_zoom: this.showZoom,
        show_toolbox: this.showToolbox,
        show_grid: this.showGrid,
        show_axis_labels: this.showAxisLabels,
        show_categories: this.showCategories,
        show_axis_titles: this.showAxisTitles,
        animation: this.enableAnimation,
        show_tooltip: this.showTooltip,
        tooltip_show_pct: this.tooltipShowPct,
        tooltip_show_count: this.tooltipShowCount,
        tooltip_value_pct: this.tooltipValuePct,
        show_percent: this.showPercent,
        show_percent_symbol: this.showPercentSymbol,
        percent_decimals: this.percentDecimals,
        category_order: this.categoryOrder,
        bar_layout: this.barLayout,
        bar_orientation: this.barOrientation,
        bar_radial: this.barRadial,
        bar_polar: this.barPolar,
        line_layout: this.lineLayout,
        line_fill: this.lineFill,
        compare_gap: this.compareGap,
        compare_cat_font_size: this.compareCatFontSize,
        compare_cat_rotation: this.compareCatRotation,
        x_label_rotation: this.xLabelRotation,
        x_label_font_size: this.xLabelFontSize,
        x_label_max_length: this.xLabelMaxLength,
        force_show_all_labels: this.forceShowAllLabels,
        bar_category_gap: this.barCategoryGap,
        y_axis_min: this.yAxisMin,
        y_axis_max: this.yAxisMax,
        grid_left: this.gridLeft,
        grid_right: this.gridRight,
        grid_top: this.gridTop,
        grid_bottom: this.gridBottom,
        title_left: this.titleLeft,
        title_top: this.titleTop,
        title_font_size: this.titleFontSize,
        label_distance: this.labelDistance,
        container_width: this.containerWidth,
        container_height: this.containerHeight,
        chart_title: this.chartTitle,
        stack_order: this.stackOrder,
        parliament_dot_scale: this.parliamentDotScale,
        parliament_shape: this.parliamentShape,
        parliament_rows: this.parliamentRowsAuto ? null : this.parliamentRows,
        parliament_show_ss: this.parliamentShowSS,
        school_filter_mode: this.schoolFilterMode,
        source_filters: this.cleanSourceFilters(),
      };
    },

    _flash(msg, isError) {
      this.statusMsg = msg; this.statusError = isError;
      setTimeout(() => { this.statusMsg = ''; }, 4000);
    },
  };
}
