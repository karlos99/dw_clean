(function () {
    function makeMessageOption(message, isError) {
        return {
            animation: false,
            title: {
                text: String(message || ''),
                left: 'center',
                top: 'middle',
                textStyle: {
                    color: isError ? '#dc2626' : '#64748b',
                    fontSize: 14,
                    fontWeight: 500,
                },
            },
            xAxis: { show: false },
            yAxis: { show: false },
            series: [],
        };
    }

    function create(containerEl) {
        if (!containerEl || typeof echarts === 'undefined') {
            return null;
        }

        const chart = echarts.init(containerEl, null, { renderer: 'canvas' });

        return {
            render(option) {
                chart.clear();
                chart.setOption(option || {}, true);
            },
            showMessage(message, isError) {
                chart.clear();
                chart.setOption(makeMessageOption(message, !!isError), true);
            },
            resize() {
                chart.resize();
            },
            dispose() {
                chart.dispose();
            },
        };
    }

    window.DVChartRenderer = { create };
})();
