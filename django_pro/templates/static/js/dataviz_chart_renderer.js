(function () {
    function create(containerEl) {
        if (!containerEl || typeof Highcharts === 'undefined') {
            return null;
        }

        let chart = null;
        let lastOption = null;

        function disposeChart() {
            if (chart && typeof chart.destroy === 'function') {
                chart.destroy();
            }
            chart = null;
        }

        function getLegendConfig(legend) {
            if (!legend || legend.show === false) {
                return { enabled: false };
            }
            const out = { enabled: true, layout: 'horizontal', align: 'center', verticalAlign: 'bottom' };
            if (legend.top !== undefined) {
                out.verticalAlign = 'top';
            } else if (legend.left !== undefined && legend.orient === 'vertical') {
                out.layout = 'vertical';
                out.align = 'left';
                out.verticalAlign = 'middle';
            } else if (legend.right !== undefined && legend.orient === 'vertical') {
                out.layout = 'vertical';
                out.align = 'right';
                out.verticalAlign = 'middle';
            }
            return out;
        }

        function getChartType(option) {
            const series = option.series || [];
            const firstType = series[0] ? series[0].type : '';
            if (firstType === 'bar') {
                const hasArea = series.some((s) => !!s.areaStyle);
                return hasArea ? 'area' : 'column';
            }
            if (firstType === 'column') {
                return 'column';
            }
            if (firstType === 'line') {
                const hasArea = series.some((s) => !!s.areaStyle);
                return hasArea ? 'area' : 'line';
            }
            if (firstType === 'spline') {
                const hasArea = series.some((s) => !!s.areaStyle);
                return hasArea ? 'areaspline' : 'spline';
            }
            if (firstType === 'areaspline') {
                return 'areaspline';
            }
            if (firstType === 'pie') {
                return 'pie';
            }
            if (firstType === 'scatter') {
                const isBubble = series.some((s) => !!s.bubble);
                return isBubble ? 'bubble' : 'scatter';
            }
            if (firstType === 'heatmap') {
                return 'heatmap';
            }
            if (firstType === 'treemap') {
                return 'treemap';
            }
            if (firstType === 'funnel') {
                return 'funnel';
            }
            if (firstType === 'pyramid') {
                return 'pyramid';
            }
            if (firstType === 'boxplot') {
                return 'boxplot';
            }
            if (firstType === 'waterfall') {
                return 'waterfall';
            }
            if (firstType === 'radar') {
                return 'line';
            }
            if (firstType === 'gauge') {
                return 'gauge';
            }
            if (firstType === 'solidgauge') {
                return 'solidgauge';
            }
            return 'column';
        }

        function mapSeries(option) {
            const series = option.series || [];
            const highType = getChartType(option);
            const dv = option.dv || {};

            if (highType === 'heatmap') {
                return series.map((s) => ({
                    type: 'heatmap',
                    name: s.name || 'Heatmap',
                    data: (s.data || []).map((d) => [Number(d[0]) || 0, Number(d[1]) || 0, Number(d[2]) || 0]),
                    dataLabels: { enabled: !!(s.label && s.label.show) },
                }));
            }

            if (highType === 'bubble') {
                return series.map((s) => ({
                    type: 'bubble',
                    name: s.name || 'Series',
                    data: (s.data || []).map((d) => ({
                        x: Number(d[0]) || 0,
                        y: Number(d[1]) || 0,
                        z: Number(d[2]) || Number(d[3]) || 1,
                    })),
                    marker: {
                        lineWidth: 1,
                        lineColor: '#ffffff',
                    },
                }));
            }

            if (highType === 'scatter') {
                return series.map((s) => ({
                    type: 'scatter',
                    name: s.name || 'Series',
                    data: (s.data || []).map((d) => [Number(d[0]) || 0, Number(d[1]) || 0]),
                    marker: {
                        radius: Number(s.symbolSize) || 4,
                    },
                }));
            }

            if (highType === 'pie') {
                return series.map((s) => ({
                    type: 'pie',
                    name: s.name || 'Distribution',
                    innerSize: Array.isArray(s.radius) ? '55%' : '0%',
                    data: (s.data || []).map((d) => ({ name: String(d.name), y: Number(d.value) || 0 })),
                    dataLabels: {
                        enabled: !!(s.label && s.label.show),
                    },
                }));
            }

            if (highType === 'treemap') {
                return series.map((s) => ({
                    type: 'treemap',
                    name: s.name || 'Treemap',
                    layoutAlgorithm: 'squarified',
                    data: (s.data || []).map((d) => ({ name: String(d.name), value: Number(d.value) || 0 })),
                }));
            }

            if (highType === 'funnel') {
                return series.map((s) => ({
                    type: 'funnel',
                    name: s.name || 'Funnel',
                    data: (s.data || []).map((d) => [String(d.name), Number(d.value) || 0]),
                    dataLabels: { enabled: true },
                }));
            }

            if (highType === 'pyramid') {
                return series.map((s) => ({
                    type: 'pyramid',
                    name: s.name || 'Pyramid',
                    data: (s.data || []).map((d) => [String(d.name), Number(d.value) || 0]),
                    dataLabels: { enabled: true },
                }));
            }

            if (highType === 'boxplot') {
                return series.map((s) => ({
                    type: 'boxplot',
                    name: s.name || 'Box plot',
                    data: (s.data || []).map((d) => [Number(d[0]) || 0, Number(d[1]) || 0, Number(d[2]) || 0, Number(d[3]) || 0, Number(d[4]) || 0]),
                }));
            }

            if (highType === 'waterfall') {
                return series.map((s) => ({
                    type: 'waterfall',
                    name: s.name || 'Waterfall',
                    data: (s.data || []).map((v) => Number(v) || 0),
                    dataLabels: { enabled: !!(s.label && s.label.show) },
                }));
            }

            if (highType === 'gauge') {
                return series.map((s) => ({
                    type: 'gauge',
                    name: s.name || 'Gauge',
                    data: (s.data || []).map((d) => Number(d.value) || 0),
                }));
            }

            if (highType === 'solidgauge') {
                return series.map((s) => ({
                    type: 'solidgauge',
                    name: s.name || 'Gauge',
                    data: (s.data || []).map((d) => Number(d.value) || 0),
                    dataLabels: {
                        format: '<div style="text-align:center"><span style="font-size:18px">{y}</span></div>',
                    },
                }));
            }

            if (option.radar && option.radar.indicator && option.radar.indicator.length) {
                const names = option.radar.indicator.map((d) => String(d.name || ''));
                return series.map((s) => ({
                    type: 'line',
                    name: s.name || 'Radar',
                    data: (((s.data || [])[0] || {}).value || []).map((v) => Number(v) || 0),
                    pointPlacement: 'on',
                    xAxis: 0,
                    yAxis: 0,
                    custom: { radarCategories: names },
                }));
            }

            const isStacked = series.some((s) => !!s.stack);
            const stackMode = dv.stacking_mode === 'percent' ? 'percent' : 'normal';
            return series.map((s) => ({
                type: highType,
                name: s.name || 'Series',
                data: (s.data || []).map((v) => Number(v) || 0),
                stacking: isStacked ? stackMode : undefined,
                marker: {
                    enabled: !!s.showSymbol,
                    radius: Math.max(2, Number(s.symbolSize) || 4),
                },
                dataLabels: {
                    enabled: !!(s.label && s.label.show),
                },
            }));
        }

        function toHighchartsConfig(option) {
            const highType = getChartType(option);
            const dv = option.dv || {};
            const isPolarRadar = !!(option.radar && option.radar.indicator && option.radar.indicator.length);
            const stackMode = dv.stacking_mode === 'percent' ? 'percent' : 'normal';
            const hasStackedSeries = (option.series || []).some((s) => !!s.stack);

            const config = {
                chart: {
                    type: highType,
                    backgroundColor: option.backgroundColor || '#ffffff',
                    polar: isPolarRadar || !!dv.polar,
                    inverted: !!dv.inverted,
                    animation: !!dv.animate,
                    zooming: {
                        type: dv.zoom_pan ? 'xy' : undefined,
                        mouseWheel: { enabled: !!dv.zoom_pan },
                    },
                    panning: {
                        enabled: !!dv.zoom_pan,
                        type: 'xy',
                    },
                    panKey: 'shift',
                },
                title: {
                    text: option.title && option.title.show ? option.title.text : null,
                    style: {
                        color: (option.textStyle && option.textStyle.color) || '#334155',
                    },
                },
                credits: { enabled: false },
                exporting: { enabled: true },
                colors: option.color || undefined,
                legend: getLegendConfig(option.legend),
                tooltip: {
                    shared: !!dv.tooltip_shared && highType !== 'pie' && highType !== 'treemap' && highType !== 'funnel' && highType !== 'gauge',
                    outside: true,
                },
                plotOptions: {
                    series: {
                        animation: !!dv.animate,
                        turboThreshold: 0,
                        states: {
                            inactive: {
                                opacity: 0.35,
                            },
                        },
                        opacity: Number(dv.opacity || 0.9),
                        lineWidth: Math.max(1, Number(dv.line_width) || 2),
                        borderRadius: Math.max(0, Number(dv.border_radius) || 0),
                    },
                    area: {
                        stacking: hasStackedSeries ? stackMode : undefined,
                        lineWidth: Math.max(1, Number(dv.line_width) || 2),
                    },
                    column: {
                        stacking: hasStackedSeries ? stackMode : undefined,
                        borderRadius: Math.max(0, Number(dv.border_radius) || 0),
                    },
                    pie: {
                        borderRadius: Math.max(0, Number(dv.border_radius) || 0),
                    },
                    funnel: {
                        borderRadius: Math.max(0, Number(dv.border_radius) || 0),
                    },
                },
                series: mapSeries(option),
            };

            if (highType === 'heatmap') {
                config.xAxis = {
                    categories: (option.xAxis && option.xAxis.data) || [],
                    title: { text: option.xAxis && option.xAxis.name ? option.xAxis.name : null },
                };
                config.yAxis = {
                    categories: (option.yAxis && option.yAxis.data) || [],
                    title: { text: option.yAxis && option.yAxis.name ? option.yAxis.name : null },
                    reversed: true,
                };
                config.colorAxis = {
                    min: (option.visualMap && option.visualMap.min) || 0,
                    max: (option.visualMap && option.visualMap.max) || null,
                };
            } else if (highType === 'scatter' || highType === 'bubble') {
                config.xAxis = {
                    title: { text: option.xAxis && option.xAxis.name ? option.xAxis.name : null },
                    gridLineWidth: 1,
                };
                config.yAxis = {
                    title: { text: option.yAxis && option.yAxis.name ? option.yAxis.name : null },
                };
            } else if (highType === 'gauge') {
                config.yAxis = {
                    min: 0,
                    max: (((option.series || [])[0] || {}).max) || null,
                    title: { text: (((option.series || [])[0] || {}).data || [])[0] ? (((option.series || [])[0] || {}).data || [])[0].name : null },
                };
            } else if (highType === 'solidgauge') {
                config.chart.type = 'solidgauge';
                config.pane = {
                    center: ['50%', '85%'],
                    size: '100%',
                    startAngle: -90,
                    endAngle: 90,
                    background: {
                        backgroundColor: '#EEE',
                        innerRadius: '60%',
                        outerRadius: '100%',
                        shape: 'arc',
                    },
                };
                config.yAxis = {
                    min: 0,
                    max: (((option.series || [])[0] || {}).max) || 100,
                    stops: [[0.1, '#55BF3B'], [0.5, '#DDDF0D'], [0.9, '#DF5353']],
                    lineWidth: 0,
                    tickWidth: 0,
                    minorTickInterval: null,
                    title: { text: (((option.series || [])[0] || {}).data || [])[0] ? (((option.series || [])[0] || {}).data || [])[0].name : null, y: -70 },
                };
            } else if (isPolarRadar) {
                const indicators = option.radar.indicator || [];
                const maxVal = Math.max(1, ...indicators.map((d) => Number(d.max) || 1));
                config.xAxis = {
                    categories: indicators.map((d) => String(d.name || '')),
                    tickmarkPlacement: 'on',
                    lineWidth: 0,
                };
                config.yAxis = {
                    gridLineInterpolation: 'polygon',
                    lineWidth: 0,
                    min: 0,
                    max: maxVal,
                };
            } else {
                const categories = option.xAxis && option.xAxis.type === 'category' ? (option.xAxis.data || []) : null;
                config.xAxis = {
                    categories: categories,
                    title: { text: option.xAxis && option.xAxis.name ? option.xAxis.name : null },
                };
                config.yAxis = {
                    title: { text: option.yAxis && option.yAxis.name ? option.yAxis.name : null },
                };
            }

            return config;
        }

        function renderMessage(message, isError) {
            disposeChart();
            containerEl.innerHTML = `<div class="h-full w-full flex items-center justify-center text-sm ${isError ? 'text-red-600' : 'text-slate-500'}">${String(message || '')}</div>`;
        }

        return {
            render(option) {
                lastOption = option || {};
                try {
                    disposeChart();
                    containerEl.innerHTML = '';
                    const config = toHighchartsConfig(lastOption);
                    chart = Highcharts.chart(containerEl, config);
                } catch (error) {
                    renderMessage(String((error && error.message) || error || 'Unable to render chart'), true);
                }
            },
            showMessage(message, isError) {
                renderMessage(message, !!isError);
            },
            resize() {
                if (chart && typeof chart.reflow === 'function') {
                    chart.reflow();
                }
            },
            dispose() {
                disposeChart();
                containerEl.innerHTML = '';
                lastOption = null;
            },
        };
    }

    window.DVChartRenderer = { create };
})();
