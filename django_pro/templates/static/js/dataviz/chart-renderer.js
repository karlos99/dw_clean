/**
 * chart-renderer.js — ECharts 6 initialiser.
 * Attaches to every .chart-container element, respects data-drill-enabled.
 */
(function () {
  'use strict';

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
      if (cw) container.style.width  = cw + 'px';
      if (ch) container.style.height = ch + 'px';
      // If the parent chart-section has auto height, ensure the container fills it
      var chartSection = container.closest('.chart-section');
      if (chartSection) {
        if (!ch) {
          // No explicit height → fill the available space
          container.style.height = '100%';
        }
      }
    }

    let option;
    try {
      option = (typeof raw === 'string') ? JSON.parse(raw) : raw;
    } catch (e) {
      container.textContent = 'Invalid chart option';
      return;
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
      _renderSummaryTable(container, option);
      return null;
    }

    const chart = echarts.init(container, null, { renderer: 'canvas' });

    // Extract custom drill metadata before setOption (ECharts drops unknown keys)
    if (option._drillMeta) {
      chart._drillMeta = option._drillMeta;
      delete option._drillMeta;
    }
    if (option && option.__category_names__) {
      chart._categoryNames = option.__category_names__;
    }

    chart.setOption(option);

    if (option && option.__is_parliament__) {
      adjustParliamentAspectRatio(chart, container, option);
    }

    // Expose instance for external tools (e.g. chart-editor.js)
    container._echartsInstance = chart;

    // Auto-resize when container dimensions change
    const ro = new ResizeObserver(() => {
      if (option && option.__is_parliament__) {
        adjustParliamentAspectRatio(chart, container, option);
      }
      chart.resize();
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
        if (fullCat) {
          xVal = fullCat;
        }
      }

      // assessment_compare stores _drillMeta on the chart instance
      const meta = chart._drillMeta;
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
      if (meta && meta.pgDisplay === 'matrix') {
        if (!Array.isArray(params.value) || params.value[2] === 0) return;
        afterVal  = (meta.xLabels || [])[params.value[0]] || '';
        beforeVal = (meta.yLabels || [])[params.value[1]] || '';
        xVal = '';  // not used for matrix drill
      }

      let url = `/dataviz/drill/${chartId}/?x_val=${encodeURIComponent(xVal)}&series_val=${encodeURIComponent(seriesVal)}`;
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

  function _renderSummaryTable(container, option) {
    const colHeaders = option.col_headers || [];
    const colHeaderGroups = option.col_header_groups || [];
    const colHeaderCols  = option.col_header_cols || [];
    const rows       = option.rows        || [];
    const totalRow   = option.total_row   || [];
    const rowLabel   = option.row_label   || option.row_col || 'Row';
    const rowLabels  = option.row_labels  || [rowLabel];
    const numRowLbls = option.num_row_labels || rowLabels.length || 1;
    const showRowTotals = option.st_show_row_totals !== false;  // default true
    const showColTotals = option.st_show_col_totals !== false;  // default true

    const chartId    = container.dataset.chartId;
    const drillEnabled = container.dataset.drillEnabled === '1';
    const source     = container.dataset.drillSource     || '';
    const sourceId   = container.dataset.drillSourceId   || '';
    const sourceName = container.dataset.drillSourceName || '';
    const canDrill   = drillEnabled && chartId && chartId !== 'preview';

    var SEPS = ' \u25b8 ';

    let html = '<div class="overflow-auto w-full h-full min-h-[200px]"><table class="min-w-full text-xs border-collapse">';

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
        html += '<th class="bg-slate-100 border border-slate-200 px-2 py-1 text-center font-semibold text-slate-500 whitespace-nowrap" colspan="' + grp.headers.length + '">'
             + _esc(String(grp.label)) + '</th>';
      }
      if (showRowTotals) {
        html += '<th class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-700 whitespace-nowrap" rowspan="2">Total</th>';
      }
      html += '</tr><tr>';
      // Value header row
      for (var i = 0; i < colHeaders.length; i++) {
        html += '<th class="bg-slate-100 border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-600 whitespace-nowrap">'
             + _esc(String(colHeaders[i])) + '</th>';
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
        html += '<th class="bg-slate-100 border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-600 whitespace-nowrap">' + _esc(String(colHeaders[i])) + '</th>';
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
        var cv  = colHeaders[c];
        var cc  = colHeaderCols[c] || '';
        if (canDrill && cnt > 0) {
          var rvKey = rowVals.join(SEPS);
          var cvKey = cc ? (cc + SEPS + cv) : cv;
          html += '<td class="border border-slate-200 px-2 py-1 text-center font-medium text-sky-700 cursor-pointer hover:bg-sky-100" data-rv="' + _esc(rvKey) + '" data-cv="' + _esc(cvKey) + '">' + cnt + '</td>';
        } else {
          html += '<td class="border border-slate-200 px-2 py-1 text-center text-slate-500">' + (cnt > 0 ? cnt : '<span class="text-slate-300">\u2014</span>') + '</td>';
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
      for (var t = numRowLbls; t < totalRow.length - 1; t++) {
        html += '<td class="border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-700">' + totalRow[t] + '</td>';
      }
      html += '<td class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-800">' + totalRow[totalRow.length - 1] + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table></div>';

    container.innerHTML = html;

    // Attach drill-through click handlers on drillable cells
    if (canDrill) {
      container.querySelectorAll('td[data-rv]').forEach(function (td) {
        td.addEventListener('click', function () {
          var rv = td.dataset.rv;
          var cv = td.dataset.cv;
          var url = '/dataviz/drill/' + chartId + '/?st_row_val=' + encodeURIComponent(rv) + '&st_col_val=' + encodeURIComponent(cv);
          if (source)     url += '&source='      + encodeURIComponent(source);
          if (sourceId)   url += '&source_id='   + encodeURIComponent(sourceId);
          if (sourceName) url += '&source_name=' + encodeURIComponent(sourceName);
          if (window._pageFilterQS) url += '&' + window._pageFilterQS;
          window.location.href = url;
        });
      });
    }
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
  window.registerDrillThrough = registerDrillThrough;
}());
