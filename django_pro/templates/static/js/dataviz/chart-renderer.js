/**
 * chart-renderer.js — ECharts 6 initialiser.
 * Attaches to every .chart-container element, respects data-drill-enabled.
 */
(function () {
  'use strict';

  /** Place outer multi-x group labels at the true midpoint between leaf ticks. */
  function applyCenteredGroupLabels(chart) {
    var bands = chart && chart._groupLabelBands;
    if (!bands || !bands.length) return;

    // convertToPixel({xAxisIndex}) alone returns only X (a number). That was
    // incorrectly used as Y and scattered labels across the plot. Use leaf-axis
    // ordinal → pixel for the centered X/Y along the category axis, and the grid
    // rect + secondary-axis offset for the label row outside the plot.
    var rect = null;
    try {
      var grid = chart.getModel().getComponent('grid', 0);
      if (grid && grid.coordinateSystem) rect = grid.coordinateSystem.getRect();
    } catch (e) { /* ignore */ }

    var elems = [];
    for (var i = 0; i < bands.length; i++) {
      var b = bands[i];
      if (!b || b.show === false || b.label == null || b.label === '') {
        elems.push({ id: '__dvGroupLabel_' + i, $action: 'remove', type: 'text' });
        continue;
      }
      var isY = b.axis === 'y';
      var offset = b.offset != null ? Number(b.offset) : (isY ? 60 : 30);
      var c0, c1;
      try {
        c0 = chart.convertToPixel(isY ? { yAxisIndex: 0 } : { xAxisIndex: 0 }, b.start);
        c1 = chart.convertToPixel(isY ? { yAxisIndex: 0 } : { xAxisIndex: 0 }, b.end);
      } catch (e) {
        continue;
      }
      if (c0 == null || c1 == null) continue;

      var x, y;
      if (isY) {
        var y0 = Array.isArray(c0) ? c0[1] : c0;
        var y1 = Array.isArray(c1) ? c1[1] : c1;
        y = (y0 + y1) / 2;
        x = rect ? (rect.x - offset) : ((Array.isArray(c0) ? c0[0] : 0) - offset);
      } else {
        var x0 = Array.isArray(c0) ? c0[0] : c0;
        var x1 = Array.isArray(c1) ? c1[0] : c1;
        x = (x0 + x1) / 2;
        y = rect ? (rect.y + rect.height + offset) : (chart.getHeight() - offset);
      }
      if (x == null || y == null || isNaN(x) || isNaN(y)) continue;

      var rot = Number(b.rotate) || 0;
      elems.push({
        type: 'text',
        id: '__dvGroupLabel_' + i,
        silent: true,
        z: 100,
        rotation: rot ? (-rot * Math.PI / 180) : 0,
        style: {
          text: String(b.label),
          x: x,
          y: y,
          textAlign: 'center',
          textVerticalAlign: 'middle',
          fontSize: b.fontSize || 12,
          fontWeight: 'bold',
          fill: b.color || '#475569',
        },
      });
    }
    if (!elems.length) return;
    try {
      chart.setOption({ graphic: elems }, { lazyUpdate: true });
    } catch (e) { /* ignore */ }
  }

  function adjustParliamentAspectRatio(chart, container, option) {
    if (!option || !option.__is_parliament__) return;

    const shape = option.__parliament_shape__ || 'semi-circle';

    // Parse grid padding
    let left = 40, right = 40, top = 60, bottom = 60;
    if (option.grid) {
      if (option.grid.left != null && !isNaN(option.grid.left)) left = Number(option.grid.left);
      if (option.grid.right != null && !isNaN(option.grid.right)) right = Number(option.grid.right);
      if (option.grid.top != null && !isNaN(option.grid.top)) top = Number(option.grid.top);
      if (option.grid.bottom != null && !isNaN(option.grid.bottom)) bottom = Number(option.grid.bottom);
    }

    const W = container.clientWidth - left - right;
    const H = container.clientHeight - top - bottom;
    if (W <= 0 || H <= 0) return;

    const R = W / H;

    // Define data ranges based on shape
    let default_x_min = -260, default_x_max = 260;
    let default_y_min, default_y_max;
    if (shape === 'circle') {
      default_y_min = -260;
      default_y_max = 260;
    } else if (shape === 'horseshoe') {
      default_y_min = -190;
      default_y_max = 260;
    } else {
      default_y_min = -20;
      default_y_max = 260;
    }

    const dw = default_x_max - default_x_min; // 480
    const dh = default_y_max - default_y_min; // 480, 410, or 260
    const shape_r = dw / dh;

    let x_min, x_max, y_min, y_max;

    if (R >= shape_r) {
      // Container is wider than the shape aspect ratio -> height limits width
      y_min = default_y_min;
      y_max = default_y_max;
      const dw_new = dh * R;
      x_max = dw_new / 2;
      x_min = -x_max;
    } else {
      // Container is taller than the shape aspect ratio -> width limits height
      x_min = default_x_min;
      x_max = default_x_max;
      const dh_new = dw / R;
      if (shape === 'circle') {
        y_max = dh_new / 2;
        y_min = -y_max;
      } else {
        // Anchor the bottom at default_y_min
        y_min = default_y_min;
        y_max = default_y_min + dh_new;
      }
    }

    chart.setOption({
      xAxis: {
        min: x_min,
        max: x_max
      },
      yAxis: {
        min: y_min,
        max: y_max
      }
    });
  }

  function initChart(container) {
    const raw = container.dataset.chartOption;
    if (!raw) return;

    // Apply per-chart container sizing from the page builder (chart-slot data attrs)
    var chartSlot = container.closest('.page-chart-slot');
    if (chartSlot) {
      var cw = chartSlot.dataset.containerWidth;
      var ch = chartSlot.dataset.containerHeight;
      if (cw) {
        container.style.width = cw + 'px';
        container.style.flex  = 'none';
      } else {
        container.style.width = '100%';
      }
      if (ch) {
        container.style.height    = ch + 'px';
        container.style.minHeight = '0';
        container.style.flex      = 'none';
      } else {
        // Use flex:1 to fill the slot — avoids percentage-height resolution
        // ambiguity when the parent slot is itself sized by flex (flex:1).
        container.style.flex      = '1';
        container.style.height    = '';
        container.style.minHeight = '0';
      }
      // Ensure the slot is a flex column so the chart container stretches into it.
      // Do NOT set height:100% here — the slot's flex:1 (set in CSS/HTML) already
      // fills the grid cell correctly without creating a percentage-height cycle.
      chartSlot.style.display       = 'flex';
      chartSlot.style.flexDirection = 'column';
    }

    let option;
    try {
      option = (typeof raw === 'string') ? JSON.parse(raw) : raw;
    } catch (e) {
      container.textContent = 'Invalid chart option';
      return;
    }

    // Table column layout: also on data-* so embed/page renders stay in sync
    // with saved config even when render HTML was cached before option JSON
    // included these keys.
    if (container.dataset.colHeaderWrap === '1') {
      option.col_header_wrap = true;
    }
    if (container.dataset.colHeaderWidth) {
      var parsedHdrW = Number(container.dataset.colHeaderWidth);
      if (Number.isFinite(parsedHdrW) && parsedHdrW > 0) {
        option.col_header_width = parsedHdrW;
      }
    }

    // Message option: show guidance/error text instead of a chart
    if (option && option.__is_message__) {
      var msgClass = option.__is_error__ ? 'bg-red-50 border-red-200 text-red-700' : 'bg-amber-50 border-amber-200 text-amber-800';
      var msgIcon  = option.__is_error__ ? '\u26a0\ufe0f ' : '\u2139\ufe0f ';
      var msgText  = (option.title && option.title.text) || option.markdown || option.text || '';
      container.innerHTML = '<div class="' + msgClass + ' border rounded-lg p-3 text-sm">' + msgIcon + _esc(String(msgText)) + '</div>';
      return null;
    }

    // Convert __formatter_js__ strings into live JS functions (e.g. tooltip, axis labels)
    if (option.tooltip && option.tooltip.__formatter_js__) {
      try {
        option.tooltip.formatter = new Function('return ' + option.tooltip.__formatter_js__)();
      } catch (e) { /* ignore bad formatter */ }
      delete option.tooltip.__formatter_js__;
    }
    ['xAxis', 'yAxis', 'angleAxis', 'radiusAxis'].forEach(function(axKey) {
      var axes = option[axKey];
      if (!axes) return;
      // Normalise to array so we handle both single-axis and multi-axis configs
      var list = Array.isArray(axes) ? axes : [axes];
      list.forEach(function(ax) {
        if (ax && ax.axisLabel && ax.axisLabel.__formatter_js__) {
          try {
            ax.axisLabel.formatter = new Function('return ' + ax.axisLabel.__formatter_js__)();
          } catch (e) { /* ignore */ }
          delete ax.axisLabel.__formatter_js__;
        }
      });
    });
    // Handle series label __formatter_js__ (e.g. transition matrix cell labels)
    if (Array.isArray(option.series)) {
      option.series.forEach(function(s) {
        if (s && s.label && s.label.__formatter_js__) {
          try {
            s.label.formatter = new Function('return ' + s.label.__formatter_js__)();
          } catch (e) { /* ignore */ }
          delete s.label.__formatter_js__;
        }
      });
    }

    // Summary table: render as HTML cross-tab instead of ECharts
    if (option.__type__ === 'table') {
      container._isTable = true;
      _renderSummaryTable(container, option);
      return null;
    }

    // Initialise ECharts with the container's current dimensions.
    // When the container is flex-sized (page builder / page view), the browser
    // may not have committed the final flex layout yet, so we also schedule a
    // resize after the next paint (requestAnimationFrame) to guarantee the chart
    // fills its grid cell on first render.
    const chart = echarts.init(container, null, { renderer: 'canvas' });

    // Extract custom drill metadata before setOption (ECharts drops unknown keys)
    if (option._drillMeta) {
      chart._drillMeta = option._drillMeta;
      delete option._drillMeta;
    }
    const categoryNames = _categoryNamesFromContainer(container, option);
    if (categoryNames) {
      chart._categoryNames = categoryNames;
      delete option.__category_names__;
    }
    if (option && option.__group_label_bands__) {
      chart._groupLabelBands = option.__group_label_bands__;
      delete option.__group_label_bands__;
    }

    // Large charts are already reduced by the server-side aggregation path;
    // avoid ECharts' expensive tweening when a chart still contains many
    // points. This preserves animation for ordinary charts.
    var pointCount = 0;
    if (Array.isArray(option.series)) {
      option.series.forEach(function (series) {
        if (series && Array.isArray(series.data)) pointCount += series.data.length;
      });
    }
    if (pointCount > 5000 && option.animation !== false) {
      option.animation = false;
    }
    chart.setOption(option, { lazyUpdate: true });
    applyCenteredGroupLabels(chart);

    if (option && option.__is_parliament__) {
      adjustParliamentAspectRatio(chart, container, option);
    }

    // Expose instance for external tools (e.g. chart-editor.js)
    container._echartsInstance = chart;

    // Correct size after the browser commits the flex / grid layout.
    // This is the primary fix for charts in page-builder grid cells where
    // clientHeight may be 0 or stale at the time echarts.init() runs.
    function _forceRelayout() {
      chart.resize();
      if (option && option.__is_parliament__) {
        adjustParliamentAspectRatio(chart, container, option);
      }
      applyCenteredGroupLabels(chart);
    }
    requestAnimationFrame(_forceRelayout);
    // Flex/grid layouts can commit their final dimensions a frame or two
    // after the chart is inserted, and the legend is a layout component that
    // ECharts only paints on render/resize. Without these extra passes the
    // legend could stay un-painted until an interaction (e.g. a click) forces
    // a repaint. Running resize again once layout has definitely settled makes
    // the legend appear reliably on first paint.
    requestAnimationFrame(function () {
      _forceRelayout();
      setTimeout(_forceRelayout, 60);
    });

    let resizeTimer = null;

    // Auto-resize when container dimensions change
    const ro = new ResizeObserver(() => {
      if (option && option.__is_parliament__) {
        adjustParliamentAspectRatio(chart, container, option);
      }
      chart.resize();

      // Debounce setOption to prevent lag during active resizing, but ensure legend layout updates
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        applyCenteredGroupLabels(chart);
      }, 100);
    });
    ro.observe(container);

    // Notify listeners that this chart is ready
    container.dispatchEvent(new CustomEvent('chartInited', { bubbles: true, detail: { chart } }));

    // Drill-through click handler
    const chartId = container.dataset.chartId;
    const drillEnabled = container.dataset.drillEnabled === '1';
    if (drillEnabled && chartId && chartId !== 'preview') {
      registerDrillThrough(chart, chartId, container);
    }

    return chart;
  }

  function registerDrillThrough(chart, chartId, container) {
    // Source context for breadcrumbs
    const source     = (container && container.dataset.drillSource)     || '';
    const sourceId   = (container && container.dataset.drillSourceId)   || '';
    const sourceName = (container && container.dataset.drillSourceName) || '';

    chart.on('click', function (params) {
      let xVal      = params.name      || '';
      const seriesVal = params.seriesName || '';
      let drillCol  = '';
      let beforeVal = '';
      let afterVal  = '';

      // If we have access to original category names and a valid dataIndex,
      // use the full category name (e.g. "Fall · KN") instead of just "KN".
      if (chart._categoryNames && params.dataIndex != null && params.dataIndex < chart._categoryNames.length) {
        const fullCat = chart._categoryNames[params.dataIndex];
        if (fullCat && fullCat.includes('spacer_')) {
          return;
        }
        if (fullCat) {
          xVal = fullCat;
        }
      }

      // assessment_compare stores _drillMeta on the chart instance
      const meta = chart._drillMeta;

      // date_series: calculation lines are derived (line A +/- line B) and
      // have no rows of their own to drill into — skip navigation.
      if (meta && meta.dsLines && meta.dsLines[seriesVal] && meta.dsLines[seriesVal].lineType === 'calc') {
        return;
      }

      if (meta && params.dataIndex != null) {
        if (meta.catForPos) {
          const realCat = meta.catForPos[params.dataIndex];
          if (realCat) xVal = realCat;
        }
        if (meta.colForPos) {
          drillCol = meta.colForPos[params.dataIndex] || '';
        }
      }

      // progress_growth matrix (heatmap): decode before/after PL from cell value indices.
      // params.value = [after_idx, before_idx, count].
      // Skip navigation for empty cells (count === 0).
      let seriesValForUrl = seriesVal;
      if (meta && meta.pgDisplay === 'matrix') {
        if (!Array.isArray(params.value) || params.value[2] === 0) return;
        afterVal  = (meta.xLabels || [])[params.value[0]] || '';
        beforeVal = (meta.yLabels || [])[params.value[1]] || '';
        xVal = '';  // not used for matrix drill
        seriesValForUrl = '';  // heatmap series name is not meaningful for drill
      }

      let url = `/dataviz/drill/${chartId}/?x_val=${encodeURIComponent(xVal)}&series_val=${encodeURIComponent(seriesValForUrl)}`;
      if (drillCol)   url += `&drill_col=${encodeURIComponent(drillCol)}`;
      if (beforeVal)  url += `&before_val=${encodeURIComponent(beforeVal)}`;
      if (afterVal)   url += `&after_val=${encodeURIComponent(afterVal)}`;
      if (source)     url += `&source=${encodeURIComponent(source)}`;
      if (sourceId)   url += `&source_id=${encodeURIComponent(sourceId)}`;
      if (sourceName) url += `&source_name=${encodeURIComponent(sourceName)}`;
      // Forward active page-level filters
      if (window._pageFilterQS) url += `&${window._pageFilterQS}`;
      window.location.href = url;
    });
  }

  function _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _tableHeaderLabelHtml(text, allowWrap) {
    var s = String(text);
    if (allowWrap && s.indexOf('\n') >= 0) {
      return s.split('\n').map(_esc).join('<br>');
    }
    return _esc(s);
  }

  function _csvEscape(val) {
    var s = val == null ? '' : String(val);
    if (/[",\r\n]/.test(s)) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  function _tableCellExportText(cell, percentColumns, colIndex) {
    var cellVal = cell;
    if (cell && typeof cell === 'object' && cell.val !== undefined) {
      cellVal = cell.val;
    }
    if (cellVal === null || cellVal === undefined || String(cellVal).trim() === '') {
      return '';
    }
    if (percentColumns.indexOf(colIndex) >= 0) {
      return String(cellVal) + '%';
    }
    return String(cellVal);
  }

  function _buildSummaryTableCsv(option) {
    var colHeaders = option.col_headers || [];
    var colHeaderGroups = option.col_header_groups || [];
    var rows = option.rows || [];
    var totalRow = option.total_row || [];
    var rowLabels = option.row_labels || [option.row_label || 'Row'];
    var numRowLbls = option.num_row_labels || rowLabels.length || 1;
    var showRowTotals = option.st_show_row_totals !== false;
    var showColTotals = option.st_show_col_totals !== false;
    var percentColumns = option.percent_columns || [];
    var lines = [];

    function pushRow(cells) {
      lines.push(cells.map(_csvEscape).join(','));
    }

    if (colHeaderGroups.length > 1) {
      var groupRow = [];
      for (var rl = 0; rl < numRowLbls; rl++) {
        groupRow.push(rl === 0 ? String(rowLabels[rl] || '') : '');
      }
      for (var g = 0; g < colHeaderGroups.length; g++) {
        var grp = colHeaderGroups[g];
        var label = grp && grp.label != null ? String(grp.label) : '';
        var span = (grp && grp.headers && grp.headers.length) || 1;
        groupRow.push(label);
        for (var gs = 1; gs < span; gs++) {
          groupRow.push('');
        }
      }
      if (showRowTotals) {
        groupRow.push('');
      }
      pushRow(groupRow);
      var headerRow = [];
      for (var rl2 = 0; rl2 < numRowLbls; rl2++) {
        headerRow.push('');
      }
      for (var hi = 0; hi < colHeaders.length; hi++) {
        headerRow.push(String(colHeaders[hi] || ''));
      }
      if (showRowTotals) {
        headerRow.push('Total');
      }
      pushRow(headerRow);
    } else {
      var singleHeader = [];
      for (var rl3 = 0; rl3 < numRowLbls; rl3++) {
        singleHeader.push(String(rowLabels[rl3] || ''));
      }
      for (var ci = 0; ci < colHeaders.length; ci++) {
        singleHeader.push(String(colHeaders[ci] || ''));
      }
      if (showRowTotals) {
        singleHeader.push('Total');
      }
      pushRow(singleHeader);
    }

    for (var r = 0; r < rows.length; r++) {
      var row = rows[r];
      var rowVals = row.slice(0, numRowLbls);
      var cells = showRowTotals ? row.slice(numRowLbls, row.length - 1) : row.slice(numRowLbls);
      var rTotal = showRowTotals ? row[row.length - 1] : null;
      var out = [];
      for (var rv = 0; rv < numRowLbls; rv++) {
        out.push(String(rowVals[rv] || ''));
      }
      for (var c = 0; c < cells.length; c++) {
        out.push(_tableCellExportText(cells[c], percentColumns, c));
      }
      if (showRowTotals) {
        out.push(rTotal == null ? '' : String(rTotal));
      }
      pushRow(out);
    }

    if (showColTotals && totalRow.length) {
      var totalHasGrand = option.total_row_has_grand_total !== false;
      var totalValueEnd = totalHasGrand ? totalRow.length - 1 : totalRow.length;
      var totalOut = [];
      for (var t = 0; t < totalRow.length; t++) {
        var tv = totalRow[t];
        if (t >= numRowLbls && t < totalValueEnd) {
          var totalColIdx = t - numRowLbls;
          if (percentColumns.indexOf(totalColIdx) >= 0 && tv !== '' && tv != null) {
            totalOut.push(String(tv) + '%');
          } else {
            totalOut.push(tv == null ? '' : String(tv));
          }
        } else {
          totalOut.push(tv == null ? '' : String(tv));
        }
      }
      pushRow(totalOut);
    }

    return lines.join('\r\n') + '\r\n';
  }

  function _tableExportBaseName(container, option) {
    var title = String(option.title_text || '').trim();
    if (title) {
      return title.replace(/[^\w\s-]+/g, '').trim().replace(/\s+/g, '_').slice(0, 80) || 'chart_table';
    }
    var chartId = container && container.dataset ? container.dataset.chartId : '';
    if (chartId && chartId !== 'preview') {
      return 'chart_' + chartId + '_table';
    }
    return 'chart_table';
  }

  function _downloadBlob(filename, blob) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  function _exportSummaryTableCsv(container, option) {
    var csv = _buildSummaryTableCsv(option);
    var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    _downloadBlob(_tableExportBaseName(container, option) + '.csv', blob);
  }

  function _ensureHtml2Canvas() {
    if (typeof window.html2canvas === 'function') {
      return Promise.resolve(window.html2canvas);
    }
    return new Promise(function (resolve, reject) {
      var existing = document.querySelector('script[data-dv-html2canvas]');
      if (existing) {
        existing.addEventListener('load', function () { resolve(window.html2canvas); });
        existing.addEventListener('error', reject);
        return;
      }
      var s = document.createElement('script');
      s.src = '/static/vendor/html2canvas/html2canvas.min.js';
      s.defer = true;
      s.dataset.dvHtml2canvas = '1';
      s.onload = function () { resolve(window.html2canvas); };
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  function _exportSummaryTablePng(container) {
    var root = container.querySelector('.dv-table-export-root');
    if (!root) return;
    _ensureHtml2Canvas().then(function (html2canvas) {
      return html2canvas(root, {
        backgroundColor: '#ffffff',
        scale: Math.min(2, window.devicePixelRatio || 1),
        useCORS: true,
        logging: false,
        onclone: function (_doc, clone) {
          var scroll = clone.querySelector('.overflow-auto');
          if (scroll) {
            scroll.style.overflow = 'visible';
            scroll.style.maxHeight = 'none';
            scroll.style.height = 'auto';
          }
        },
      });
    }).then(function (canvas) {
      canvas.toBlob(function (blob) {
        if (!blob) return;
        var option = container._tableExportOption || {};
        _downloadBlob(_tableExportBaseName(container, option) + '.png', blob);
      }, 'image/png');
    }).catch(function () {
      window.alert('Could not save table image. Try again or use CSV export.');
    });
  }

  function _attachTableExportToolbar(container, option) {
    if (option.show_toolbox === false) return;
    var toolbar = container.querySelector('.dv-table-toolbar');
    if (!toolbar) return;
    var csvBtn = toolbar.querySelector('[data-dv-export="csv"]');
    var pngBtn = toolbar.querySelector('[data-dv-export="png"]');
    if (csvBtn) {
      csvBtn.addEventListener('click', function (evt) {
        evt.preventDefault();
        evt.stopPropagation();
        _exportSummaryTableCsv(container, option);
      });
    }
    if (pngBtn) {
      pngBtn.addEventListener('click', function (evt) {
        evt.preventDefault();
        evt.stopPropagation();
        _exportSummaryTablePng(container);
      });
    }
  }

  function _renderSummaryTable(container, option) {
    container._tableExportOption = option;
    const colHeaders = option.col_headers || [];
    const colKeys    = option.col_header_raw || colHeaders;
    const percentColumns = option.percent_columns || [];
    const colHeaderGroups = option.col_header_groups || [];
    const colHeaderCols  = option.col_header_cols || [];
    const rows       = option.rows        || [];
    const totalRow   = option.total_row   || [];
    const rowLabel   = option.row_label   || option.row_col || 'Row';
    const rowLabels  = option.row_labels  || [rowLabel];
    const numRowLbls = option.num_row_labels || rowLabels.length || 1;
    const showRowTotals = option.st_show_row_totals !== false;  // default true
    const showColTotals = option.st_show_col_totals !== false;  // default true
    const totalHasGrand = option.total_row_has_grand_total !== false;
    const totalDrillable = option.total_row_drillable === true;

    const chartId    = container.dataset.chartId;
    const drillEnabled = container.dataset.drillEnabled === '1';
    const source     = container.dataset.drillSource     || '';
    const sourceId   = container.dataset.drillSourceId   || '';
    const sourceName = container.dataset.drillSourceName || '';
    const canDrill   = drillEnabled && chartId && chartId !== 'preview';

    var SEPS = ' \u25b8 ';

    const titleText = String(option.title_text || '').trim();
    const titleSize = Number(option.title_font_size) || 16;
    const titleShow = option.title_show !== false && !!titleText;
    const subText = String(option.subtitle_text || '').trim();
    const subShow = option.subtitle_show === true && !!subText;
    const colHeaderWrap = option.col_header_wrap === true;
    var colHeaderWidth = Number(option.col_header_width);
    if (colHeaderWrap && !Number.isFinite(colHeaderWidth)) {
      colHeaderWidth = 88;
    }
    const colHeaderWidthStyle = (colHeaderWrap && Number.isFinite(colHeaderWidth) && colHeaderWidth > 0)
      ? (' max-width:' + colHeaderWidth + 'px;width:' + colHeaderWidth + 'px;')
      : '';
    const colThWrapClass = colHeaderWrap
      ? 'whitespace-normal break-words leading-tight align-bottom'
      : 'whitespace-nowrap';
    const colThStyleAttr = colHeaderWidthStyle ? (' style="' + colHeaderWidthStyle.trim() + '"') : '';
    const tableLayoutStyle = colHeaderWidthStyle ? ' style="table-layout:fixed;width:100%"' : '';
    const colTdWidthClass = colHeaderWidthStyle ? ' max-w-0 overflow-hidden' : '';
    const showToolbox = option.show_toolbox !== false;
    const toolbarBtnClass = 'px-2 py-0.5 text-[10px] font-semibold rounded border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-900 shadow-sm';
    let html = '<div class="dv-table-export-root flex flex-col w-full h-full min-h-[200px] bg-white">';
    if (showToolbox) {
      html += '<div class="dv-table-toolbar flex justify-end gap-1 px-2 pt-1 shrink-0">'
           + '<button type="button" class="' + toolbarBtnClass + '" data-dv-export="csv" title="Download table as CSV">CSV</button>'
           + '<button type="button" class="' + toolbarBtnClass + '" data-dv-export="png" title="Save table as PNG image">PNG</button>'
           + '</div>';
    }
    html += '<div class="dv-table-title text-center font-semibold text-slate-800 px-3 pt-2 pb-1 shrink-0"'
         + (titleShow ? ' style="font-size:' + titleSize + 'px"' : ' style="display:none"') + '>'
         + _esc(titleText) + '</div>';
    html += '<p class="dv-table-subtitle text-center font-normal text-slate-500 px-4 pb-2 shrink-0"'
         + (subShow ? ' style="font-size:11px;line-height:1.45;white-space:pre-wrap"' : ' style="display:none"') + '>'
         + _esc(subText) + '</p>';
    html += '<div class="overflow-auto w-full flex-1 min-h-0"><table class="min-w-full text-xs border-collapse"' + tableLayoutStyle + '>';

    if (colHeaderWidthStyle) {
      html += '<colgroup>';
      for (var rlCol = 0; rlCol < numRowLbls; rlCol++) {
        html += '<col style="width:120px">';
      }
      for (var ci = 0; ci < colHeaders.length; ci++) {
        html += '<col style="' + colHeaderWidthStyle.trim() + '">';
      }
      if (showRowTotals) {
        html += '<col style="width:72px">';
      }
      html += '</colgroup>';
    }

    // Header rows (single row, or two rows if col_header_groups present)
    html += '<thead>';
    if (colHeaderGroups.length > 1) {
      // Group label row
      html += '<tr>';
      for (var rl = 0; rl < numRowLbls; rl++) {
        html += '<th class="sticky left-0 z-10 bg-slate-100 border border-slate-200 px-2 py-1.5 text-left font-semibold text-slate-600 whitespace-nowrap"'
             + (rl > 0 ? ' style="left:' + (rl * 120) + 'px;"' : '') + ' rowspan="2">'
             + _esc(String(rowLabels[rl] || '')) + '</th>';
      }
      for (var g = 0; g < colHeaderGroups.length; g++) {
        var grp = colHeaderGroups[g];
        html += '<th class="bg-slate-100 border border-slate-200 px-2 py-1 text-center font-semibold text-slate-500 ' + (colHeaderWrap ? colThWrapClass : 'whitespace-nowrap') + '" colspan="' + grp.headers.length + '">'
             + _tableHeaderLabelHtml(grp.label, colHeaderWrap) + '</th>';
      }
      if (showRowTotals) {
        html += '<th class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-700 whitespace-nowrap" rowspan="2">Total</th>';
      }
      html += '</tr><tr>';
      // Value header row
      for (var i = 0; i < colHeaders.length; i++) {
        html += '<th class="bg-slate-100 border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-600 ' + colThWrapClass + '"' + colThStyleAttr + '>'
             + _tableHeaderLabelHtml(colHeaders[i], colHeaderWrap) + '</th>';
      }
      html += '</tr>';
    } else {
      // Single header row (original behavior)
      html += '<tr>';
      for (var rl = 0; rl < numRowLbls; rl++) {
        html += '<th class="sticky left-0 z-10 bg-slate-100 border border-slate-200 px-2 py-1.5 text-left font-semibold text-slate-600 whitespace-nowrap"'
             + (rl > 0 ? ' style="left:' + (rl * 120) + 'px;"' : '') + '>'
             + _esc(String(rowLabels[rl] || '')) + '</th>';
      }
      for (var i = 0; i < colHeaders.length; i++) {
        html += '<th class="bg-slate-100 border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-600 ' + colThWrapClass + '"' + colThStyleAttr + '>' + _tableHeaderLabelHtml(colHeaders[i], colHeaderWrap) + '</th>';
      }
      if (showRowTotals) {
        html += '<th class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-700 whitespace-nowrap">Total</th>';
      }
      html += '</tr>';
    }
    html += '</thead>';

    // Body rows
    html += '<tbody>';
    for (var r = 0; r < rows.length; r++) {
      var row    = rows[r];
      var rowVals = row.slice(0, numRowLbls);
      var cells  = showRowTotals ? row.slice(numRowLbls, row.length - 1) : row.slice(numRowLbls);
      var rTotal = showRowTotals ? row[row.length - 1] : null;
      html += '<tr class="hover:bg-sky-50 transition">';
      for (var rl = 0; rl < numRowLbls; rl++) {
        var rv = String(rowVals[rl] || '');
        html += '<td class="sticky left-0 bg-white border border-slate-200 px-2 py-1 font-medium text-slate-700 whitespace-nowrap"'
             + (rl > 0 ? ' style="left:' + (rl * 120) + 'px;"' : '') + '>'
             + (rv || '<span class="text-slate-300">\u2014</span>') + '</td>';
      }
      for (var c = 0; c < cells.length; c++) {
        var cnt = cells[c];
        var cv  = colKeys[c];
        var cc  = colHeaderCols[c] || '';
        
        var cellVal = cnt;
        var tooltipText = '';
        if (cnt && typeof cnt === 'object' && cnt.val !== undefined) {
          cellVal = cnt.val;
          tooltipText = cnt.tooltip || '';
        }
        
        var cntNum = Number(cellVal);
        var displayCellVal = percentColumns.indexOf(c) >= 0 && cellVal !== '' && cellVal !== null && cellVal !== undefined
          ? String(cellVal) + '%' : cellVal;
        var hasDisplayValue = !(cellVal === null || cellVal === undefined || String(cellVal).trim() === '');
        var titleAttr = (option.show_tooltips !== false && tooltipText) ? ' title="' + _esc(tooltipText) + '"' : '';

        if (canDrill && Number.isFinite(cntNum) && cntNum !== 0) {
          var rvKey = rowVals.join(SEPS);
          var cvKey = cc ? (cc + SEPS + cv) : cv;
          var calcAttr = cc ? ' data-calc-id="' + _esc(cc) + '"' : '';
          html += '<td class="border border-slate-200 px-2 py-1 text-center font-medium text-sky-700 cursor-pointer hover:bg-sky-100' + colTdWidthClass + '"' + titleAttr + ' data-rv="' + _esc(rvKey) + '" data-cv="' + _esc(cvKey) + '"' + calcAttr + '>' + displayCellVal + '</td>';
        } else {
          html += '<td class="border border-slate-200 px-2 py-1 text-center text-slate-500' + colTdWidthClass + '"' + titleAttr + '>' + (hasDisplayValue ? displayCellVal : '<span class="text-slate-300">\u2014</span>') + '</td>';
        }
      }
      if (showRowTotals) {
        html += '<td class="bg-slate-50 border border-slate-200 px-2 py-1 text-center font-semibold text-slate-700">' + rTotal + '</td>';
      }
      html += '</tr>';
    }

    // Total row
    if (showColTotals && totalRow.length) {
      html += '<tr class="bg-slate-100">';
      for (var rl = 0; rl < numRowLbls; rl++) {
        html += '<td class="sticky left-0 bg-slate-100 border border-slate-200 px-2 py-1.5 font-bold text-slate-700 whitespace-nowrap"'
             + (rl > 0 ? ' style="left:' + (rl * 120) + 'px;"' : '') + '>'
             + (rl < totalRow.length ? _esc(String(totalRow[rl] || '')) : '') + '</td>';
      }
      var totalValueEnd = totalHasGrand ? totalRow.length - 1 : totalRow.length;
      for (var t = numRowLbls; t < totalValueEnd; t++) {
        var totalColIdx = t - numRowLbls;
        var totalDisplay = percentColumns.indexOf(totalColIdx) >= 0 && totalRow[t] !== '' ? String(totalRow[t]) + '%' : totalRow[t];
        var totalCv = colHeaderCols[totalColIdx] ? colHeaderCols[totalColIdx] + SEPS + colKeys[totalColIdx] : colKeys[totalColIdx];
        if (canDrill && totalDrillable && Number(totalRow[t]) !== 0) {
          var totalCalcId = colHeaderCols[totalColIdx] || '';
          var totalCalcAttr = totalCalcId ? ' data-calc-id="' + _esc(totalCalcId) + '"' : '';
          html += '<td class="border border-slate-200 px-2 py-1.5 text-center font-semibold text-sky-700 cursor-pointer hover:bg-sky-100" data-rv="" data-cv="' + _esc(totalCv) + '"' + totalCalcAttr + '>' + totalDisplay + '</td>';
        } else {
          html += '<td class="border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-700">' + totalDisplay + '</td>';
        }
      }
      if (totalHasGrand) {
        html += '<td class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-800">' + totalRow[totalRow.length - 1] + '</td>';
      }
      html += '</tr>';
    }
    html += '</tbody></table></div></div>';

    container.innerHTML = html;
    _attachTableExportToolbar(container, option);

    // Attach drill-through click handlers on drillable cells
    if (canDrill) {
      container.querySelectorAll('td[data-rv]').forEach(function (td) {
        td.addEventListener('click', function () {
          var rv = td.dataset.rv;
          var cv = td.dataset.cv;
          var url = '/dataviz/drill/' + chartId + '/?st_col_val=' + encodeURIComponent(cv);
          if (rv) url += '&st_row_val=' + encodeURIComponent(rv);
          if (td.dataset.calcId) url += '&dyn_calc_id=' + encodeURIComponent(td.dataset.calcId);
          if (source)     url += '&source='      + encodeURIComponent(source);
          if (sourceId)   url += '&source_id='   + encodeURIComponent(sourceId);
          if (sourceName) url += '&source_name=' + encodeURIComponent(sourceName);
          if (window._pageFilterQS) url += '&' + window._pageFilterQS;
          window.location.href = url;
        });
      });
    }
  }

  function _categoryNamesFromContainer(container, option) {
    if (option && option.__category_names__) {
      return option.__category_names__;
    }
    try {
      var raw = container.dataset.categoryNames;
      if (raw) {
        var parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length) return parsed;
      }
    } catch (_) {}
    return null;
  }

  function getDrillStatus(container) {
    if (!container) {
      return { canDrill: false, state: 'none', message: 'No chart loaded.' };
    }
    var chartId = container.dataset.chartId || '';
    var drillEnabled = container.dataset.drillEnabled === '1';
    var hasSavedChart = drillEnabled && chartId && chartId !== 'preview';
    if (!hasSavedChart) {
      return {
        canDrill: false,
        state: 'needs_save',
        message: 'Save the chart to test drill-through in the builder.',
      };
    }
    var drillableCells = container.querySelectorAll('td[data-rv]').length;
    if (drillableCells > 0) {
      return {
        canDrill: true,
        state: 'ready',
        message: 'Drill ready — click a table cell (' + drillableCells + ' clickable). Save after edits.',
      };
    }
    if (container._echartsInstance) {
      return {
        canDrill: true,
        state: 'ready',
        message: 'Drill ready — click a bar, point, or slice. Save after edits so drill matches preview.',
      };
    }
    return {
      canDrill: true,
      state: 'ready',
      message: 'Drill ready. Save after config changes so drill matches preview.',
    };
  }

  var _lazyObserver = null;

  function _getLazyObserver() {
    if (!_lazyObserver) {
      _lazyObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var el = entry.target;
          _lazyObserver.unobserve(el);
          if (!el._echartsInited) {
            el._echartsInited = true;
            initChart(el);
          }
        });
      }, { rootMargin: '300px 0px' });
    }
    return _lazyObserver;
  }

  function initAll() {
    var observer = _getLazyObserver();
    document.querySelectorAll('.chart-container').forEach(function (el) {
      if (el._echartsInited || el._echartsObserved) return;
      el._echartsObserved = true;
      observer.observe(el);
    });
  }

  function disposeChartsIn(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('.chart-container').forEach(function (el) {
      if (el._echartsInstance) {
        try { el._echartsInstance.dispose(); } catch (e) { /* ignore */ }
        el._echartsInstance = null;
      }
      el._echartsInited = false;
      el._echartsObserved = false;
    });
  }

  // Run on initial load and after HTMX swaps
  function onReady() {
    initAll();
    document.body.addEventListener('htmx:afterSwap', function () { initAll(); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady);
  } else if (document.body) {
    onReady();
  } else {
    // Head script: body doesn't exist yet, wait for DOMContentLoaded
    document.addEventListener('DOMContentLoaded', onReady);
  }

  // Expose globally for manual calls
  window.initChart        = initChart;
  window.initAllCharts    = initAll;
  window.disposeChartsIn  = disposeChartsIn;
  window.registerDrillThrough = registerDrillThrough;
  window.applyCenteredGroupLabels = applyCenteredGroupLabels;
  window.getChartDrillStatus = getDrillStatus;
}());
