/**
 * chart-renderer.js — ECharts 6 initialiser.
 * Attaches to every .chart-container element, respects data-drill-enabled.
 */
(function () {
  'use strict';

  function initChart(container) {
    const raw = container.dataset.chartOption;
    if (!raw) return;

    let option;
    try {
      option = (typeof raw === 'string') ? JSON.parse(raw) : raw;
    } catch (e) {
      container.textContent = 'Invalid chart option';
      return;
    }

    // Convert __formatter_js__ strings into live JS functions (e.g. tooltip, axis labels)
    if (option.tooltip && option.tooltip.__formatter_js__) {
      try {
        option.tooltip.formatter = new Function('return ' + option.tooltip.__formatter_js__)();
      } catch (e) { /* ignore bad formatter */ }
      delete option.tooltip.__formatter_js__;
    }
    ['xAxis', 'yAxis'].forEach(function(axKey) {
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

    chart.setOption(option);

    // Auto-resize when container dimensions change
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(container);

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
    const rows       = option.rows        || [];
    const totalRow   = option.total_row   || [];
    const rowLabel   = option.row_label   || option.row_col || 'Row';

    const chartId    = container.dataset.chartId;
    const drillEnabled = container.dataset.drillEnabled === '1';
    const source     = container.dataset.drillSource     || '';
    const sourceId   = container.dataset.drillSourceId   || '';
    const sourceName = container.dataset.drillSourceName || '';
    const canDrill   = drillEnabled && chartId && chartId !== 'preview';

    let html = '<div class="overflow-auto w-full h-full min-h-[200px]"><table class="min-w-full text-xs border-collapse">';

    // Header row
    html += '<thead><tr>';
    html += `<th class="sticky left-0 z-10 bg-slate-100 border border-slate-200 px-2 py-1.5 text-left font-semibold text-slate-600 whitespace-nowrap">${_esc(rowLabel)}</th>`;
    for (var i = 0; i < colHeaders.length; i++) {
      html += `<th class="bg-slate-100 border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-600 whitespace-nowrap">${_esc(String(colHeaders[i]))}</th>`;
    }
    html += '<th class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-700 whitespace-nowrap">Total</th>';
    html += '</tr></thead>';

    // Body rows
    html += '<tbody>';
    for (var r = 0; r < rows.length; r++) {
      var row    = rows[r];
      var rv     = row[0];
      var cells  = row.slice(1, row.length - 1);
      var rTotal = row[row.length - 1];
      html += '<tr class="hover:bg-sky-50 transition">';
      html += `<td class="sticky left-0 bg-white border border-slate-200 px-2 py-1 font-medium text-slate-700 whitespace-nowrap">${_esc(String(rv))}</td>`;
      for (var c = 0; c < cells.length; c++) {
        var cnt = cells[c];
        var cv  = colHeaders[c];
        if (canDrill && cnt > 0) {
          html += `<td class="border border-slate-200 px-2 py-1 text-center font-medium text-sky-700 cursor-pointer hover:bg-sky-100" data-rv="${_esc(String(rv))}" data-cv="${_esc(String(cv))}">${cnt}</td>`;
        } else {
          html += `<td class="border border-slate-200 px-2 py-1 text-center text-slate-500">${cnt > 0 ? cnt : '<span class="text-slate-300">\u2014</span>'}</td>`;
        }
      }
      html += `<td class="bg-slate-50 border border-slate-200 px-2 py-1 text-center font-semibold text-slate-700">${rTotal}</td>`;
      html += '</tr>';
    }

    // Total row
    if (totalRow.length) {
      html += '<tr class="bg-slate-100">';
      html += '<td class="sticky left-0 bg-slate-100 border border-slate-200 px-2 py-1.5 font-bold text-slate-700">Total</td>';
      for (var t = 1; t < totalRow.length - 1; t++) {
        html += `<td class="border border-slate-200 px-2 py-1.5 text-center font-semibold text-slate-700">${totalRow[t]}</td>`;
      }
      html += `<td class="bg-slate-200 border border-slate-200 px-2 py-1.5 text-center font-bold text-slate-800">${totalRow[totalRow.length - 1]}</td>`;
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

  function initAll() {
    document.querySelectorAll('.chart-container').forEach(function (el) {
      if (!el._echartsInited) {
        el._echartsInited = true;
        initChart(el);
      }
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
