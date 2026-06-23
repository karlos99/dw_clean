/**
 * chart-editor.js — Interactive layout editor overlay for ECharts.
 *
 * Appends a transparent overlay DIRECTLY to the .chart-container element
 * (which ECharts already makes position:relative).  No DOM wrapping.
 *
 * Design: stable DOM structure — draw() runs once to build handles/badges,
 * update() repositions them in-place.  This avoids destroying elements
 * mid-drag (which would silently swallow drag events).
 *
 * Usage:
 *   const ed = ChartEditor.attach(chartEl, onUpdateFn);
 *   ed.destroy();
 */
(function (global) {
  'use strict';

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  var SNAP_THRESHOLD = 10; // px — how close before snapping to center
  // Returns snapped value and whether it snapped
  function snapC(v, center) {
    if (Math.abs(v - center) <= SNAP_THRESHOLD) return { v: center, snapped: true };
    return { v: v, snapped: false };
  }

  function toPx(v, total) {
    if (v == null || v === '' || v === 'auto') return null;
    var s = String(v);
    if (s.endsWith('%'))  return parseFloat(s) / 100 * total;
    var kw = { left:0, right:total, center:total/2, top:0, bottom:total, middle:total/2 };
    if (kw[s] !== undefined) return kw[s];
    var n = parseFloat(s);
    return isNaN(n) ? null : n;
  }

  function el(tag, css, html) {
    var d = document.createElement(tag || 'div');
    if (css)  d.style.cssText = css;
    if (html) d.innerHTML = html;
    return d;
  }

  function svgIcon(paths) {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none"'
         + ' stroke="white" stroke-width="2.5" stroke-linecap="round"'
         + ' stroke-linejoin="round">' + paths + '</svg>';
  }

  // Drag: calls cb(dx, dy) with deltas each mousemove, calls onEnd() on mouseup.
  function onDrag(target, cb, onEnd) {
    target.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      var ox = e.clientX, oy = e.clientY;
      function move(ev) {
        ev.preventDefault();
        var dx = ev.clientX - ox, dy = ev.clientY - oy;
        ox = ev.clientX; oy = ev.clientY;
        cb(dx, dy);
      }
      function up() {
        document.removeEventListener('mousemove', move, true);
        document.removeEventListener('mouseup',   up,   true);
        if (onEnd) onEnd();
      }
      document.addEventListener('mousemove', move, true);
      document.addEventListener('mouseup',   up,   true);
    });
  }

  function slider(min, max, step, val, color, onChange) {
    var s = document.createElement('input');
    s.type = 'range'; s.min = min; s.max = max; s.step = step; s.value = val;
    s.style.cssText = 'width:88px;cursor:pointer;accent-color:' + color;
    s.addEventListener('input', function () { onChange(Number(this.value)); });
    return s;
  }

  function sliderRow(label, color, sliderEl, valEl) {
    var row = el('div', 'display:flex;align-items:center;gap:5px');
    var lbl = el('span', 'font-size:9px;font-weight:700;color:' + color + ';letter-spacing:.06em;min-width:56px');
    lbl.textContent = label;
    row.appendChild(lbl);
    row.appendChild(sliderEl);
    row.appendChild(valEl);
    return row;
  }

  function valBadge(text, color) {
    var s = el('span', 'font-size:10px;font-weight:700;color:' + color + ';min-width:26px;text-align:right');
    s.textContent = text;
    return s;
  }

  // ── main attach ─────────────────────────────────────────────────────────────

  function attach(chartEl, onUpdate) {
    var chart = chartEl._echartsInstance;
    if (!chart) {
      console.warn('[ChartEditor] no _echartsInstance on', chartEl);
      return null;
    }

    // Remove any previous overlay
    var prev = chartEl.querySelector('.cheditor-ov');
    if (prev) prev.remove();

    // ECharts already sets position:relative; ensure it
    chartEl.style.position = 'relative';

    // Mutable container dimensions — updated when amber resize handles are dragged
    var dims = {
      w: chartEl.offsetWidth  || 600,
      h: chartEl.offsetHeight || 460,
    };

    // Read current chart option
    var opt  = chart.getOption();
    var grid = (opt.grid   || [{}])[0] || {};
    var ttl  = (opt.title  || [{}])[0] || {};
    var lgd  = (opt.legend || [{}])[0] || {};

    // State
    var st = {
      gl: toPx(grid.left,   dims.w) || 55,
      gr: toPx(grid.right,  dims.w) || 20,
      gt: toPx(grid.top,    dims.h) || 36,
      gb: toPx(grid.bottom, dims.h) || 80,
      tx: 0, ty: 0,
      lx: 0, ly: 0,
      titleFontSize: (ttl.textStyle && ttl.textStyle.fontSize) ? +ttl.textStyle.fontSize : 16,
      lgGap:  lgd.itemGap  != null ? +lgd.itemGap  : 10,
      lgText: (lgd.textStyle && lgd.textStyle.fontSize) ? +lgd.textStyle.fontSize : 11,
      lgIcon: lgd.itemWidth != null ? +lgd.itemWidth : 10,
      barGap:    null,
      labelDist: null,
      // snap flags — true when using ECharts named center/middle
      txCenter: ttl.left === 'center',
      tyMiddle: ttl.top === 'middle',
      lxCenter: lgd.left === 'center',
      lyMiddle: lgd.top === 'middle',
    };

    var ttlLeft = toPx(ttl.left, dims.w), ttlTop = toPx(ttl.top, dims.h);
    st.tx = ttlLeft != null ? (st.txCenter ? ttlLeft - 28 : ttlLeft) : dims.w / 2 - 28;
    st.ty = ttlTop  != null ? (st.tyMiddle ? ttlTop - 10 : ttlTop) : 8;

    var ll = toPx(lgd.left, dims.w), lr = toPx(lgd.right, dims.w);
    var lt = toPx(lgd.top,  dims.h), lb = toPx(lgd.bottom, dims.h);
    st.lx = ll != null ? (st.lxCenter ? ll - 40 : ll) : (lr != null ? dims.w - lr - 80 : dims.w/2 - 40);
    st.ly = lt != null ? (st.lyMiddle ? lt - 12 : lt) : (lb != null ? dims.h - lb - 24 : dims.h - 30);
    st.lx = clamp(st.lx, 0, dims.w - 100);
    st.ly = clamp(st.ly, 0, dims.h - 28);

    var series  = opt.series || [];
    var hasBar  = series.some(function(s){ return s && s.type === 'bar'; });
    var lgdVis  = lgd.show !== false;

    if (hasBar) {
      var fb = series.find(function(s){ return s && s.type === 'bar'; });
      st.barGap = fb && fb.barCategoryGap != null ? parseInt(fb.barCategoryGap) : 20;
    }
    var hasLbls = series.some(function(s){ return s && s.label && s.label.show !== false; });
    if (hasLbls) {
      var fl = series.find(function(s){ return s && s.label; });
      st.labelDist = fl && fl.label.distance != null ? +fl.label.distance : 5;
    }

    // ── apply helpers ──────────────────────────────────────────────────────────

    function applyGrid() {
      chart.setOption({ grid: [{
        left:   Math.round(st.gl), right:  Math.round(st.gr),
        top:    Math.round(st.gt), bottom: Math.round(st.gb),
      }] });
      updateGridVisuals();
      fire();
    }
    function applyTitle() {
      chart.setOption({ title: [{
        left: st.txCenter ? 'center' : Math.round(st.tx),
        top:  st.tyMiddle ? 'middle' : Math.round(st.ty),
        textStyle: { fontSize: st.titleFontSize }
      }] });
      fire();
    }
    function applyLegendPos() {
      chart.setOption({ legend: [{
        left: st.lxCenter ? 'center' : Math.round(st.lx),
        top:  st.lyMiddle ? 'middle' : Math.round(st.ly),
      }] });
      fire();
    }
    function applyLegendStyle() {
      chart.setOption({ legend: [{ itemGap: st.lgGap, itemWidth: st.lgIcon, itemHeight: st.lgIcon, textStyle: { fontSize: st.lgText } }] });
      fire();
    }
    function applyBarGap() {
      var upd = series.map(function(s){ return (s && s.type==='bar') ? { barCategoryGap: st.barGap+'%' } : {}; });
      chart.setOption({ series: upd });
      fire();
    }
    function applyLabelDist() {
      var upd = series.map(function(s){ return (s && s.label) ? { label: { distance: st.labelDist } } : {}; });
      chart.setOption({ series: upd });
      fire();
    }
    function applyContainer() {
      chartEl.style.width  = dims.w + 'px';
      chartEl.style.height = dims.h + 'px';
      chart.resize();
      updateGridVisuals();
      fire();
    }
    function fire() {
      if (!onUpdate) return;
      onUpdate({
        grid_left:        Math.round(st.gl),
        grid_right:       Math.round(st.gr),
        grid_top:         Math.round(st.gt),
        grid_bottom:      Math.round(st.gb),
        title_left:       st.txCenter ? 'center' : Math.round(st.tx),
        title_top:        st.tyMiddle ? 'middle' : Math.round(st.ty),
        title_font_size:  st.titleFontSize,
        legend_left:      st.lxCenter ? 'center' : Math.round(st.lx),
        legend_top:       st.lyMiddle ? 'middle' : Math.round(st.ly),
        legend_item_gap:  st.lgGap,
        legend_font_size: st.lgText,
        legend_icon_size: st.lgIcon,
        bar_category_gap: st.barGap,
        label_distance:   st.labelDist,
        container_width:  dims.w,
        container_height: dims.h,
      });
    }

    // ── build overlay (runs once) ──────────────────────────────────────────────

    var ov = el('div', 'position:absolute;left:0;top:0;right:0;bottom:0;z-index:100;pointer-events:none;overflow:visible;');
    ov.className = 'cheditor-ov';
    chartEl.appendChild(ov);

    // ── Snap guide lines (center H and V) ─────────────────────────────────────
    var SNAP_COLOR = 'rgba(251,146,60,0.85)'; // orange
    var snapV = el('div',
      'position:absolute;top:0;bottom:0;width:1px;pointer-events:none;display:none;z-index:130;' +
      'background:' + SNAP_COLOR + ';box-shadow:0 0 4px rgba(251,146,60,.5);');
    var snapH = el('div',
      'position:absolute;left:0;right:0;height:1px;pointer-events:none;display:none;z-index:130;' +
      'background:' + SNAP_COLOR + ';box-shadow:0 0 4px rgba(251,146,60,.5);');
    // Snap labels
    var snapVLbl = el('div',
      'position:absolute;font-size:8px;font-weight:700;letter-spacing:.06em;' +
      'color:#fff;background:rgba(251,146,60,.9);padding:1px 4px;border-radius:3px;' +
      'pointer-events:none;white-space:nowrap;z-index:131;');
    snapVLbl.textContent = 'CENTER H';
    var snapHLbl = el('div',
      'position:absolute;font-size:8px;font-weight:700;letter-spacing:.06em;' +
      'color:#fff;background:rgba(251,146,60,.9);padding:1px 4px;border-radius:3px;' +
      'pointer-events:none;white-space:nowrap;z-index:131;');
    snapHLbl.textContent = 'CENTER V';
    ov.appendChild(snapV); ov.appendChild(snapH);
    ov.appendChild(snapVLbl); ov.appendChild(snapHLbl);

    function showSnapGuides(snapX, snapY) {
      var cx = dims.w / 2, cy = dims.h / 2;
      snapV.style.display    = snapX ? 'block' : 'none';
      snapV.style.left       = cx + 'px';
      snapVLbl.style.display = snapX ? 'block' : 'none';
      snapVLbl.style.left    = (cx + 4) + 'px';
      snapVLbl.style.top     = '4px';

      snapH.style.display    = snapY ? 'block' : 'none';
      snapH.style.top        = cy + 'px';
      snapHLbl.style.display = snapY ? 'block' : 'none';
      snapHLbl.style.left    = '4px';
      snapHLbl.style.top     = (cy + 4) + 'px';
    }
    function hideSnapGuides() { showSnapGuides(false, false); }

    // Shaded margin panels (4 sides)
    var SH = 'rgba(99,102,241,0.09)';
    var shade = {
      L: el('div', 'position:absolute;background:' + SH + ';pointer-events:none;'),
      R: el('div', 'position:absolute;background:' + SH + ';pointer-events:none;'),
      T: el('div', 'position:absolute;background:' + SH + ';pointer-events:none;'),
      B: el('div', 'position:absolute;background:' + SH + ';pointer-events:none;'),
    };
    ['L','R','T','B'].forEach(function(k){ ov.appendChild(shade[k]); });

    // Grid border
    var gridBorder = el('div',
      'position:absolute;pointer-events:none;border:2px dashed rgba(99,102,241,.5);' +
      'border-radius:3px;box-sizing:border-box;');
    ov.appendChild(gridBorder);

    // Margin labels
    function mkLbl() {
      var d = el('div',
        'position:absolute;font-size:9px;font-family:monospace;font-weight:700;' +
        'color:rgba(99,102,241,.95);background:rgba(255,255,255,.88);' +
        'padding:1px 4px;border-radius:3px;pointer-events:none;transform:translate(-50%,-50%);');
      ov.appendChild(d);
      return d;
    }
    var mL = mkLbl(), mR = mkLbl(), mT = mkLbl(), mB = mkLbl();

    // ── grid handles (edge arrows — indigo, control internal margins) ──────────

    function mkHandle(cursor, iconPaths, color) {
      var bg = color || 'rgba(99,102,241,0.9)';
      var bgHov = color ? color.replace('0.9', '1').replace('0.85', '1') : 'rgba(79,70,229,1)';
      var h = el('div',
        'position:absolute;width:22px;height:22px;border-radius:5px;' +
        'background:' + bg + ';display:flex;align-items:center;' +
        'justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,.25);' +
        'pointer-events:all;cursor:' + cursor + ';z-index:110;',
        svgIcon(iconPaths));
      h.addEventListener('mouseenter', function(){ h.style.background = bgHov; });
      h.addEventListener('mouseleave', function(){ h.style.background = bg; });
      ov.appendChild(h);
      return h;
    }
    var HLR = '<path d="M7 8l-5 4 5 4M17 8l5 4-5 4"/>';
    var HTB = '<path d="M8 7l4-5 4 5M8 17l4 5 4-5"/>';
    var hL = mkHandle('ew-resize', HLR);
    var hR = mkHandle('ew-resize', HLR);
    var hT = mkHandle('ns-resize', HTB);
    var hB = mkHandle('ns-resize', HTB);

    onDrag(hL, function(dx){ st.gl = clamp(st.gl+dx, 8, dims.w/2-20); applyGrid(); });
    onDrag(hR, function(dx){ st.gr = clamp(st.gr-dx, 8, dims.w/2-20); applyGrid(); });
    onDrag(hT, function(_,dy){ st.gt = clamp(st.gt+dy, 8, dims.h/2-20); applyGrid(); });
    onDrag(hB, function(_,dy){ st.gb = clamp(st.gb-dy, 8, dims.h/2-20); applyGrid(); });

    // ── PLOT AREA badge ────────────────────────────────────────────────────────

    var plotBadge = el('div',
      'position:absolute;display:inline-flex;align-items:center;gap:4px;' +
      'padding:4px 8px;border-radius:6px;font-size:9px;font-weight:700;' +
      'letter-spacing:.07em;color:#fff;pointer-events:all;cursor:move;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.2);user-select:none;z-index:110;' +
      'background:rgba(49,46,129,.85);',
      svgIcon('<path d="M5 9l-3 3 3 3M19 9l3 3-3 3M9 5l3-3 3 3M9 19l3 3 3-3"/>') + '<span>PLOT AREA</span>');
    ov.appendChild(plotBadge);
    onDrag(plotBadge, function(dx, dy) {
      // Compute new margins after drag
      var newGl = clamp(st.gl + dx, 8, dims.w/2-20);
      var newGr = clamp(st.gr - dx, 8, dims.w/2-20);
      var newGt = clamp(st.gt + dy, 8, dims.h/2-20);
      var newGb = clamp(st.gb - dy, 8, dims.h/2-20);
      // Snap: grid center snaps to canvas center
      var gridCx = newGl + (dims.w - newGl - newGr) / 2;
      var gridCy = newGt + (dims.h - newGt - newGb) / 2;
      var snapX = Math.abs(gridCx - dims.w/2) <= SNAP_THRESHOLD;
      var snapY = Math.abs(gridCy - dims.h/2) <= SNAP_THRESHOLD;
      if (snapX) { var half = Math.round((newGl + newGr) / 2); newGl = half; newGr = half; }
      if (snapY) { var halfV = Math.round((newGt + newGb) / 2); newGt = halfV; newGb = halfV; }
      st.gl = newGl; st.gr = newGr; st.gt = newGt; st.gb = newGb;
      showSnapGuides(snapX, snapY);
      applyGrid();
    }, hideSnapGuides);

    // ── TITLE badge ────────────────────────────────────────────────────────────

    var titleBadge = el('div',
      'position:absolute;display:inline-flex;align-items:center;gap:4px;' +
      'padding:4px 8px;border-radius:6px;font-size:9px;font-weight:700;' +
      'letter-spacing:.07em;color:#fff;pointer-events:all;cursor:move;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.2);user-select:none;z-index:110;' +
      'background:rgba(6,78,59,.85);',
      svgIcon('<path d="M4 6h16M4 12h10M4 18h7"/>') + '<span>TITLE</span>');
    ov.appendChild(titleBadge);
    onDrag(titleBadge, function(dx, dy) {
      var bw = titleBadge.offsetWidth  || 56;
      var bh = titleBadge.offsetHeight || 20;
      var rawX = clamp(st.tx + dx, 0, dims.w - bw);
      var rawY = clamp(st.ty + dy, 0, dims.h - bh);
      var sx = snapC(rawX + bw / 2, dims.w / 2);
      var sy = snapC(rawY + bh / 2, dims.h / 2);
      st.txCenter = sx.snapped;
      st.tyMiddle = sy.snapped;
      st.tx = sx.snapped ? (dims.w / 2 - bw / 2) : rawX;
      st.ty = sy.snapped ? (dims.h / 2 - bh / 2) : rawY;
      titleBadge.style.left = st.tx + 'px';
      titleBadge.style.top  = st.ty + 'px';
      showSnapGuides(sx.snapped, sy.snapped);
      applyTitle();
    }, hideSnapGuides);

    // ── LEGEND badge ───────────────────────────────────────────────────────────

    var legendBadge = null;
    if (lgdVis) {
      legendBadge = el('div',
        'position:absolute;display:inline-flex;align-items:center;gap:4px;' +
        'padding:4px 8px;border-radius:6px;font-size:9px;font-weight:700;' +
        'letter-spacing:.07em;color:#fff;pointer-events:all;cursor:move;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.2);user-select:none;z-index:110;' +
        'background:rgba(88,28,135,.85);',
        svgIcon('<rect x="3" y="5" width="3" height="3" rx=".5"/><line x1="8" y1="6.5" x2="21" y2="6.5"/>'
              + '<rect x="3" y="11" width="3" height="3" rx=".5"/><line x1="8" y1="12.5" x2="21" y2="12.5"/>'
              + '<rect x="3" y="17" width="3" height="3" rx=".5"/><line x1="8" y1="18.5" x2="21" y2="18.5"/>') + '<span>LEGEND</span>');
      ov.appendChild(legendBadge);
      onDrag(legendBadge, function(dx, dy) {
        var bw = legendBadge.offsetWidth  || 80;
        var bh = legendBadge.offsetHeight || 24;
        // badge placed at (st.lx, st.ly); visual center is (st.lx + bw/2, st.ly + bh/2)
        var rawX = clamp(st.lx + dx, 0, dims.w - bw);
        var rawY = clamp(st.ly + dy, 0, dims.h - bh);
        var sx = snapC(rawX + bw / 2, dims.w / 2);
        var sy = snapC(rawY + bh / 2, dims.h / 2);
        st.lxCenter = sx.snapped;
        st.lyMiddle = sy.snapped;
        st.lx = sx.snapped ? (dims.w / 2 - bw / 2) : rawX;
        st.ly = sy.snapped ? (dims.h / 2 - bh / 2) : rawY;
        legendBadge.style.left = st.lx + 'px';
        legendBadge.style.top  = st.ly + 'px';
        showSnapGuides(sx.snapped, sy.snapped);
        applyLegendPos();
      }, hideSnapGuides);
    }

    // ── Container resize handles (amber — control outer dimensions) ────────────

    var AMBER_BG  = 'rgba(180,83,9,0.9)';
    var AMBER_HOV = 'rgba(146,64,14,1)';

    function mkAmberHandle(cursor, iconPaths) {
      var h = el('div',
        'position:absolute;width:22px;height:22px;border-radius:5px;' +
        'background:' + AMBER_BG + ';display:flex;align-items:center;' +
        'justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,.3);' +
        'pointer-events:all;cursor:' + cursor + ';z-index:115;',
        svgIcon(iconPaths));
      h.addEventListener('mouseenter', function(){ h.style.background = AMBER_HOV; });
      h.addEventListener('mouseleave', function(){ h.style.background = AMBER_BG; });
      ov.appendChild(h);
      return h;
    }

    // Right-center: resize width
    var HRIGHT = '<path d="M9 18l6-6-6-6"/>';
    var cRight  = mkAmberHandle('e-resize',  HRIGHT);

    // Bottom-center: resize height
    var HDOWN  = '<path d="M6 9l6 6 6-6"/>';
    var cBottom = mkAmberHandle('s-resize',  HDOWN);

    // Bottom-right corner: resize both
    var HBR    = '<path d="M15 3h6v6M9 15l12-12M3 21l6-6M3 15v6h6"/>';
    var cCorner = mkAmberHandle('se-resize', HBR);

    // SIZE badge (amber, shows current dimensions, positioned bottom-left of container border)
    var sizeBadge = el('div',
      'position:absolute;display:inline-flex;align-items:center;gap:3px;' +
      'padding:3px 7px;border-radius:5px;font-size:9px;font-weight:700;font-family:monospace;' +
      'letter-spacing:.05em;color:#fff;pointer-events:none;' +
      'box-shadow:0 2px 6px rgba(0,0,0,.2);user-select:none;z-index:115;' +
      'background:rgba(180,83,9,0.85);');
    ov.appendChild(sizeBadge);

    function updateSizeBadge() {
      sizeBadge.textContent = dims.w + ' × ' + dims.h;
    }
    updateSizeBadge();

    onDrag(cRight, function(dx) {
      dims.w = clamp(dims.w + dx, 200, 1400);
      updateSizeBadge();
      applyContainer();
    });
    onDrag(cBottom, function(_, dy) {
      dims.h = clamp(dims.h + dy, 150, 1000);
      updateSizeBadge();
      applyContainer();
    });
    onDrag(cCorner, function(dx, dy) {
      dims.w = clamp(dims.w + dx, 200, 1400);
      dims.h = clamp(dims.h + dy, 150, 1000);
      updateSizeBadge();
      applyContainer();
    });

    // Container border (amber dashed outline around entire chart area)
    var containerBorder = el('div',
      'position:absolute;left:0;top:0;right:0;bottom:0;pointer-events:none;' +
      'border:2px dashed rgba(217,119,6,.45);border-radius:4px;box-sizing:border-box;z-index:105;');
    ov.appendChild(containerBorder);

    // ── top-right toolbar ──────────────────────────────────────────────────────

    var tb = el('div',
      'position:absolute;top:6px;right:6px;display:flex;gap:4px;' +
      'pointer-events:all;z-index:120;');
    function toolBtn(html, tip, onClick) {
      var b = el('button',
        'display:inline-flex;align-items:center;gap:3px;font-size:9px;font-weight:700;' +
        'letter-spacing:.05em;color:#e0e7ff;background:rgba(30,27,75,.85);border:none;' +
        'border-radius:5px;padding:4px 8px;cursor:pointer;white-space:nowrap;', html);
      b.title = tip;
      b.addEventListener('mouseenter', function(){ b.style.background='rgba(79,70,229,.9)'; });
      b.addEventListener('mouseleave', function(){ b.style.background='rgba(30,27,75,.85)'; });
      b.addEventListener('click', function(e){ e.stopPropagation(); onClick(); });
      return b;
    }
    tb.appendChild(toolBtn(
      svgIcon('<line x1="12" y1="3" x2="12" y2="21"/><path d="M5 8l7-5 7 5"/>') + ' Center H',
      'Center plot horizontally',
      function() {
        var pw = dims.w - st.gl - st.gr;
        var half = Math.round((dims.w - pw) / 2);
        st.gl = Math.max(8, half); st.gr = Math.max(8, half);
        applyGrid();
      }
    ));
    tb.appendChild(toolBtn(
      svgIcon('<line x1="3" y1="12" x2="21" y2="12"/><path d="M8 5l-5 7 5 7"/>') + ' Center V',
      'Center plot vertically',
      function() {
        var ph = dims.h - st.gt - st.gb;
        var half = Math.round((dims.h - ph) / 2);
        st.gt = Math.max(8, half); st.gb = Math.max(8, half);
        applyGrid();
      }
    ));
    tb.appendChild(toolBtn(
      svgIcon('<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 .49-6.5L1 10"/>') + ' Reset',
      'Reset to defaults',
      function() {
        st.gl=55; st.gr=20; st.gt=36; st.gb=80;
        st.tx = dims.w/2 - 28; st.ty = 8;
        st.txCenter = true; st.tyMiddle = false;
        applyGrid(); applyTitle();
      }
    ));
    ov.appendChild(tb);

    // ── bottom sliders strip ───────────────────────────────────────────────────

    var strip = el('div',
      'position:absolute;bottom:-48px;left:50%;transform:translateX(-50%);' +
      'display:flex;align-items:center;gap:10px;pointer-events:all;' +
      'background:rgba(255,255,255,.97);border:1px solid rgba(99,102,241,.2);' +
      'border-radius:10px;padding:5px 12px;box-shadow:0 2px 10px rgba(0,0,0,.12);' +
      'white-space:nowrap;z-index:120;');

    // Title Size slider
    var tsV = valBadge(st.titleFontSize+'px', '#064e3b');
    var tsS = slider(8, 48, 1, st.titleFontSize, '#059669', function(v){
      st.titleFontSize = v; tsV.textContent = v+'px'; applyTitle();
    });
    strip.appendChild(sliderRow('TITLE SIZE', '#064e3b', tsS, tsV));
    strip.appendChild(el('div', 'width:1px;height:22px;background:rgba(0,0,0,.1);flex-shrink:0'));

    if (hasBar && st.barGap !== null) {
      var bgV = valBadge(st.barGap+'%', '#4f46e5');
      var bgS = slider(0, 80, 5, st.barGap, '#6366f1', function(v){
        st.barGap = v; bgV.textContent = v+'%'; applyBarGap();
      });
      strip.appendChild(sliderRow('BAR GAP', '#4f46e5', bgS, bgV));
    }

    if (st.labelDist !== null) {
      var ldV = valBadge(st.labelDist+'px', '#0369a1');
      var ldS = slider(0, 30, 1, st.labelDist, '#0284c7', function(v){
        st.labelDist = v; ldV.textContent = v+'px'; applyLabelDist();
      });
      strip.appendChild(sliderRow('LABEL GAP', '#0369a1', ldS, ldV));
    }

    if (lgdVis) {
      if (hasBar || st.labelDist !== null) {
        strip.appendChild(el('div', 'width:1px;height:22px;background:rgba(0,0,0,.1);flex-shrink:0'));
      }
      var igV = valBadge(st.lgGap+'px', '#6d28d9');
      var igS = slider(2, 40, 2, st.lgGap, '#7c3aed', function(v){
        st.lgGap = v; igV.textContent = v+'px'; applyLegendStyle();
      });
      strip.appendChild(sliderRow('LGD GAP', '#6d28d9', igS, igV));

      var ftV = valBadge(st.lgText+'px', '#6d28d9');
      var ftS = slider(8, 16, 1, st.lgText, '#7c3aed', function(v){
        st.lgText = v; ftV.textContent = v+'px'; applyLegendStyle();
      });
      strip.appendChild(sliderRow('LGD TEXT', '#6d28d9', ftS, ftV));

      var icV = valBadge(st.lgIcon+'px', '#6d28d9');
      var icS = slider(6, 20, 1, st.lgIcon, '#7c3aed', function(v){
        st.lgIcon = v; icV.textContent = v+'px'; applyLegendStyle();
      });
      strip.appendChild(sliderRow('LGD ICON', '#6d28d9', icS, icV));
    }

    ov.appendChild(strip);

    // ── updateGridVisuals: reposition margin shading, border, labels, handles ──
    // Also repositions amber container resize handles relative to current dims.

    function updateGridVisuals() {
      var lx = Math.round(st.gl);
      var ty = Math.round(st.gt);
      var pw = Math.max(40, dims.w - st.gl - st.gr);
      var ph = Math.max(40, dims.h - st.gt - st.gb);
      var rx = lx + pw;
      var by = ty + ph;

      // Shade panels
      shade.L.style.cssText += ';left:0;top:0;width:' + lx + 'px;height:100%;';
      shade.R.style.cssText = 'position:absolute;background:' + SH + ';pointer-events:none;' +
        'left:' + rx + 'px;top:0;right:0;height:100%;';
      shade.T.style.cssText = 'position:absolute;background:' + SH + ';pointer-events:none;' +
        'left:' + lx + 'px;top:0;width:' + pw + 'px;height:' + ty + 'px;';
      shade.B.style.cssText = 'position:absolute;background:' + SH + ';pointer-events:none;' +
        'left:' + lx + 'px;top:' + by + 'px;width:' + pw + 'px;bottom:0;';

      // Grid border
      gridBorder.style.left   = lx + 'px';
      gridBorder.style.top    = ty + 'px';
      gridBorder.style.width  = pw + 'px';
      gridBorder.style.height = ph + 'px';

      // Margin labels
      function setLbl(d, text, x, y) {
        d.textContent = text + 'px';
        d.style.left = x + 'px'; d.style.top = y + 'px';
      }
      setLbl(mL, Math.round(st.gl), lx/2,          ty+ph/2);
      setLbl(mR, Math.round(st.gr), rx+st.gr/2,    ty+ph/2);
      setLbl(mT, Math.round(st.gt), lx+pw/2,       ty/2);
      setLbl(mB, Math.round(st.gb), lx+pw/2,       by+st.gb/2);

      // Indigo edge handles (grid margins)
      var hs = 11;
      hL.style.left = (lx-hs) + 'px'; hL.style.top = (ty+ph/2-hs) + 'px';
      hR.style.left = (rx-hs) + 'px'; hR.style.top = (ty+ph/2-hs) + 'px';
      hT.style.left = (lx+pw/2-hs) + 'px'; hT.style.top = (ty-hs) + 'px';
      hB.style.left = (lx+pw/2-hs) + 'px'; hB.style.top = (by-hs) + 'px';

      // PLOT AREA badge (centered on grid)
      plotBadge.style.left = (lx + pw/2 - 42) + 'px';
      plotBadge.style.top  = (ty + ph/2 - 12) + 'px';

      // Amber container resize handles
      cRight.style.left  = (dims.w - hs - 3) + 'px';
      cRight.style.top   = (dims.h/2 - hs)   + 'px';
      cBottom.style.left = (dims.w/2 - hs)   + 'px';
      cBottom.style.top  = (dims.h - hs - 3) + 'px';
      cCorner.style.left = (dims.w - hs - 3) + 'px';
      cCorner.style.top  = (dims.h - hs - 3) + 'px';

      // SIZE badge: bottom-left area, just inside the amber border
      sizeBadge.style.left   = '6px';
      sizeBadge.style.bottom = '6px';
      sizeBadge.style.top    = 'auto';
    }

    // Initial placement
    updateGridVisuals();

    titleBadge.style.left = st.tx + 'px';
    titleBadge.style.top  = st.ty + 'px';

    if (legendBadge) {
      legendBadge.style.left = st.lx + 'px';
      legendBadge.style.top  = st.ly + 'px';
    }

    return {
      destroy: function() { ov.remove(); }
    };
  }

  global.ChartEditor = { attach: attach };

}(window));
