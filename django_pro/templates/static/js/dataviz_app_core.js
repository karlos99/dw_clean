(function () {
    const utils = window.DVUtils || {};
    const {
        isBlankValue,
        parseJSON,
        toNumber,
        inferColumnType,
        normalizeLabel,
        escapeHtml,
        formatValueForColumn,
        fillSelect,
        readJsonResponse,
    } = utils;

    document.addEventListener('DOMContentLoaded', function () {
        const appData = document.getElementById('dataviz-app-data');
        const appRoot = document.getElementById('dataviz-app');
        if (!appData || !appRoot || typeof Highcharts === 'undefined' || !window.DVChartRenderer || !readJsonResponse) {
            return;
        }

        const loadStudentsUrl = appData.dataset.loadStudentsUrl;
        const renderChartUrl = appData.dataset.renderChartUrl;
        const csrfToken = appData.dataset.csrfToken || '';
        const defaultColumns = parseJSON(appData.dataset.defaultColumns || '[]', []);
        const initialDefinitions = parseJSON(appData.dataset.columnDefinitions || '{}', {});

        const savedQueryEl = document.getElementById('dv-saved-query');
        const searchEl = document.getElementById('dv-search');
        // Multi-select filter wrappers (buttons + dropdown panels)
        const schoolYearBtn = document.getElementById('dv-school-year-btn');
        const schoolYearDd  = document.getElementById('dv-school-year-dd');
        const gradeBtn      = document.getElementById('dv-grade-btn');
        const gradeDd       = document.getElementById('dv-grade-dd');
        const statusBtn     = document.getElementById('dv-status-btn');
        const statusDd      = document.getElementById('dv-status-dd');
        const loadBtn = document.getElementById('dv-load-data');
        const loadStatus = document.getElementById('dv-load-status');
        const useDefaultBtn = document.getElementById('dv-use-default-columns');

        const chartControlsWrap = document.getElementById('dv-chart-controls');
        const emptyState = document.getElementById('dv-empty-state');
        const chartWrap = document.getElementById('dv-chart-wrap');
        const chartEl = document.getElementById('dv-chart');
        const chartRenderer = window.DVChartRenderer.create(chartEl);
        const chartTypeEl = document.getElementById('dv-chart-type');
        const chartTypeHelpEl = document.getElementById('dv-chart-type-help');
        const xColEl = document.getElementById('dv-x-col');
        const yColEl = document.getElementById('dv-y-col');
        const stackColEl = document.getElementById('dv-stack-col');
        const sizeColEl = document.getElementById('dv-size-col');
        const aggEl = document.getElementById('dv-agg');
        const chartSourceEl = document.getElementById('dv-chart-source');
        const multiXWrapEl = null;
        const multiXTagsEl = null;
        const groupSeriesWrapEl = null;
        const groupSeriesTagsEl = null;
        const seriesHelpEl = null;
        const renderBtn = document.getElementById('dv-render-chart');
        const recommendationsEl = document.getElementById('dv-recommendations');
        const datasetSummaryEl = document.getElementById('dv-dataset-summary');
        const chartCompatEl = document.getElementById('dv-chart-compat');
        const seriesCapabilityEl = null;
        const seriesPinColEl = null;
        const seriesPinChipsEl = null;
        const seriesPinSelectAllEl = null;
        const seriesPinClearAllEl = null;
        const seriesPinStatusEl = null;
        const previewHead = document.getElementById('dv-preview-head');
        const previewBody = document.getElementById('dv-preview-body');
        const filterBarEl = document.getElementById('dv-filter-bar');
        const filterColsEl = document.getElementById('dv-filter-cols');
        const filterSearchEl = document.getElementById('dv-filter-search');
        const filterClearEl = document.getElementById('dv-filter-clear');
        const filterStatusEl = document.getElementById('dv-filter-status');
        const previewPageStatusEl = document.getElementById('dv-preview-page-status');
        const previewPrevBtn = document.getElementById('dv-preview-prev');
        const previewNextBtn = document.getElementById('dv-preview-next');

        const xWrap = document.getElementById('dv-wrap-x');
        const yWrap = document.getElementById('dv-wrap-y');
        const stackWrap = document.getElementById('dv-wrap-stack');
        const sizeWrap = document.getElementById('dv-wrap-size');
        const groupByChipsEl   = document.getElementById('dv-group-by-chips');
        const groupByHintEl    = document.getElementById('dv-group-by-hint');
        const groupByPreviewEl = document.getElementById('dv-group-by-preview');

        const tabDataBtn = document.getElementById('dv-tab-data');
        const tabSummaryBtn = document.getElementById('dv-tab-summary');
        const panelDataEl = document.getElementById('dv-panel-data');
        const panelSummaryEl = document.getElementById('dv-panel-summary');
        const groupByTagsEl = document.getElementById('dv-group-by-tags');
        const groupTableWrapEl = document.getElementById('dv-group-table-wrap');
        const groupMetricModeEl = document.getElementById('dv-group-metric-mode');
        const groupViewModeEl = document.getElementById('dv-group-view-mode');
        const groupApplyViewBtn = document.getElementById('dv-group-apply-view');
        const groupBaseLabelEl = document.getElementById('dv-group-base-label');
        const groupFilterBadgeEl = document.getElementById('dv-group-filter-badge');
        const groupFilterLabelEl = document.getElementById('dv-group-filter-label');
        const groupFilterClearEl = document.getElementById('dv-group-filter-clear');

        const optTitleEl = document.getElementById('dv-opt-title');
        const optPaletteEl = document.getElementById('dv-opt-palette');
        const optBgEl = document.getElementById('dv-opt-bg');
        const optLegendEl = document.getElementById('dv-opt-legend');
        const optLabelsEl = document.getElementById('dv-opt-labels');
        const optSmoothEl = document.getElementById('dv-opt-smooth');
        const optMarkersEl = document.getElementById('dv-opt-markers');
        const optBinsEl = document.getElementById('dv-opt-bins');
        const optCurveEl = document.getElementById('dv-opt-curve');
        const optPointSizeEl = document.getElementById('dv-opt-point-size');
        const optOpacityEl = document.getElementById('dv-opt-opacity');
        const optZoomEl = document.getElementById('dv-opt-zoom');
        const optLineWidthEl = document.getElementById('dv-opt-line-width');
        const optRadiusEl = document.getElementById('dv-opt-radius');
        const optStackModeEl = document.getElementById('dv-opt-stack-mode');
        const optTooltipSharedEl = document.getElementById('dv-opt-tooltip-shared');
        const optAnimateEl = document.getElementById('dv-opt-animate');
        const optInvertedEl = document.getElementById('dv-opt-inverted');
        const optPolarEl = document.getElementById('dv-opt-polar');

        const styleState = {
            title: '',
            palette: 'default',
            background: 'white',
            legendPos: 'bottom',
            dataLabels: false,
            smooth: false,
            showPoints: false,
            bins: 12,
            curve: 'linear',
            pointSize: 6,
            opacity: 0.9,
            zoomPan: true,
            lineWidth: 2,
            borderRadius: 6,
            stackMode: 'normal',
            tooltipShared: true,
            animate: true,
            inverted: false,
            polar: false,
        };

        const state = {
            rows: [],
            columns: [],
            columnDefinitions: { ...initialDefinitions },
            columnTypes: {},
            recommendations: [],
            filterState: { search: '', columns: {} },
            filteredRows: [],
            groupState: { groupKeys: [] },
            groupSeriesCols: [],
            chartXCols: [],
            chartGroupByCols: [],  // extra group-level dimensions (combined with X as composite key)
            groupReport: { metricMode: 'all', viewMode: 'table' },
            previewPage: 1,
            sourceType: 'query',   // 'query' | 'sql'
            sourceSql: '',         // raw SQL when sourceType === 'sql'
        };

