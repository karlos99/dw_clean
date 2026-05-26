/**
 * Student Tests Data Exploration - Vanilla JS Controller
 */
document.addEventListener('DOMContentLoaded', () => {
    const appDataEl = document.getElementById('app-data');
    if (!appDataEl) {
        console.error('app-data element not found');
        return;
    }

    const loadStudentsUrl = appDataEl.dataset.loadStudentsUrl;
    if (!loadStudentsUrl) {
        console.error('loadStudentsUrl not found in app-data');
        return;
    }

    const columnStructuresUrl = appDataEl.dataset.columnStructuresUrl;
    const columnStatisticsUrl = appDataEl.dataset.columnStatisticsUrl;

    let selectedColumns = [];
    let columnDefinitions = {};
    let yearAliasMap = {}; // {'2024_2025': '__sy_this__', ...} — underscore-format keys
    let yearMode = localStorage.getItem('dwYearMode') || 'specific';
    let page = 1;
    let sortBy = '';
    let sortDir = 'asc';
    let filters = {};
    let lastPayload = null;
    let columnWidths = {};
    let draggedColumn = null;
    let resizeState = null;
    let pastedIdList = null; // array when in list-search mode, null otherwise

    const stateStorageKey = 'dataexp:student_list_state';
    let stateRestored = false;

    // Row selection for saving student lists
    const selectedStudentSsids = new Set();

    const studentSummaryUrlTemplate = appDataEl.dataset.studentSummaryUrlTemplate || '';
    const studentSummaryPartialUrlTemplate = appDataEl.dataset.studentSummaryPartialUrlTemplate || '';
    const studentSummaryWarmUrl = appDataEl.dataset.studentSummaryWarmUrl || '';
    const buildStudentSummaryUrl = (ssid) => {
        if (!studentSummaryUrlTemplate) return '';
        if (!ssid) return '';
        return studentSummaryUrlTemplate.replace('SSID_PLACEHOLDER', encodeURIComponent(String(ssid)));
    };
    const buildStudentSummaryPartialUrl = (ssid) => {
        if (!studentSummaryPartialUrlTemplate) return '';
        if (!ssid) return '';
        return studentSummaryPartialUrlTemplate.replace('SSID_PLACEHOLDER', encodeURIComponent(String(ssid)));
    };

    const summaryOverlayEl = document.getElementById('student-summary-overlay');
    const summaryOverlayBodyEl = document.getElementById('student-summary-overlay-body');

    const warmedSsids = new Set();

    const warmStudentSummaryCache = async (ssids) => {
        if (!studentSummaryWarmUrl) return;
        if (!Array.isArray(ssids) || ssids.length === 0) return;
        const url = new URL(studentSummaryWarmUrl, window.location.origin);
        url.searchParams.set('ssids', ssids.join(','));
        url.searchParams.set('limit', String(ssids.length));
        try {
            await fetch(url.toString(), { credentials: 'same-origin' });
        } catch (e) {
            // best-effort warm; ignore failures
        }
    };

    const scheduleWarmStudentSummaries = () => {
        if (!studentSummaryWarmUrl) return;
        const ssids = Array.from(document.querySelectorAll('#student-table [data-action="open-student-summary"][data-ssid]'))
            .map((el) => el.getAttribute('data-ssid'))
            .filter((v) => v && String(v).trim());

        const toWarm = [];
        for (const ssid of ssids) {
            if (warmedSsids.has(ssid)) continue;
            warmedSsids.add(ssid);
            toWarm.push(ssid);
            if (toWarm.length >= 8) break;
        }
        if (toWarm.length === 0) return;

        const run = () => warmStudentSummaryCache(toWarm);
        if (typeof window.requestIdleCallback === 'function') {
            window.requestIdleCallback(run, { timeout: 1200 });
        } else {
            window.setTimeout(run, 50);
        }
    };

    const showOverlay = (el) => {
        if (!el) return;
        el.classList.remove('hidden');
        el.setAttribute('aria-hidden', 'false');
    };
    const hideOverlay = (el) => {
        if (!el) return;
        el.classList.add('hidden');
        el.setAttribute('aria-hidden', 'true');
    };

    const showLoading = (message = 'Loading data...') => {
        const loadingBar = document.getElementById('loading-bar');
        const loadingBarProgress = document.getElementById('loading-bar-progress');
        if (loadingBar) {
            loadingBar.classList.remove('hidden');
            loadingBar.setAttribute('aria-hidden', 'false');
        }
        if (loadingBarProgress) {
            // Animate progress bar from 0 to 90% while loading
            loadingBarProgress.style.width = '0%';
            setTimeout(() => {
                loadingBarProgress.style.width = '30%';
            }, 50);
            setTimeout(() => {
                loadingBarProgress.style.width = '60%';
            }, 300);
            setTimeout(() => {
                loadingBarProgress.style.width = '90%';
            }, 800);
        }
    };

    const hideLoading = () => {
        const loadingBar = document.getElementById('loading-bar');
        const loadingBarProgress = document.getElementById('loading-bar-progress');
        if (loadingBarProgress) {
            // Complete the progress bar to 100%
            loadingBarProgress.style.width = '100%';
        }
        // Hide after a brief moment to show completion
        setTimeout(() => {
            if (loadingBar) {
                loadingBar.classList.add('hidden');
                loadingBar.setAttribute('aria-hidden', 'true');
            }
            if (loadingBarProgress) {
                loadingBarProgress.style.width = '0%';
            }
        }, 200);
    };

    const loadOverlayHtml = async (url, targetEl) => {
        if (!url || !targetEl) return;
        targetEl.innerHTML = '<div class="p-6 text-slate-500">Loading…</div>';
        const res = await fetch(url, { credentials: 'same-origin' });
        if (!res.ok) {
            targetEl.innerHTML = `<div class="p-6 text-red-700">Failed to load (${res.status}).</div>`;
            return;
        }
        const html = await res.text();
        targetEl.innerHTML = html;
        if (window.htmx) {
            htmx.process(targetEl);
        }
    };

    try {
        selectedColumns = JSON.parse(appDataEl.dataset.defaultColumns || '[]');
    } catch (e) {
        selectedColumns = [];
    }

    try {
        columnDefinitions = JSON.parse(appDataEl.dataset.columnDefinitions || '{}');
    } catch (e) {
        columnDefinitions = {};
    }

    try {
        yearAliasMap = JSON.parse(appDataEl.dataset.yearAliasMap || '{}');
    } catch (e) {
        yearAliasMap = {};
    }

    // --- Year Mode Helpers ---

    /** Convert a column key with a real year segment to its relative alias equivalent. */
    function toRelativeKey(col) {
        for (const [yearUs, alias] of Object.entries(yearAliasMap)) {
            if (col.includes(yearUs)) {
                return col.replace(yearUs, alias);
            }
        }
        return col;
    }

    /** Convert a column key with a relative alias segment to its real year equivalent. */
    function toSpecificKey(col) {
        for (const [yearUs, alias] of Object.entries(yearAliasMap)) {
            if (col.includes(alias)) {
                return col.replace(alias, yearUs);
            }
        }
        return col;
    }

    /** Normalise a column key to always be in specific-year form. */
    function normaliseKey(col) {
        return toSpecificKey(col);
    }

    /** Get the column key that should be stored given the current yearMode. */
    function storageKey(col) {
        const spec = normaliseKey(col);
        return yearMode === 'relative' ? toRelativeKey(spec) : spec;
    }

    function updateYearModeButtons() {
        document.querySelectorAll('[data-year-mode-btn]').forEach(btn => {
            const isActive = btn.dataset.yearModeBtn === yearMode;
            btn.classList.toggle('bg-blue-500', isActive);
            btn.classList.toggle('text-white', isActive);
            btn.classList.toggle('font-medium', isActive);
            btn.classList.toggle('bg-white', !isActive);
            btn.classList.toggle('text-slate-500', !isActive);
        });
        // Show/hide year group rows: in relative mode hide years without an alias
        document.querySelectorAll('[data-year-group]').forEach(el => {
            const alias = el.dataset.yearAlias || '';
            if (yearMode === 'relative') {
                el.style.display = alias ? '' : 'none';
            } else {
                el.style.display = '';
            }
        });
    }

    function setYearMode(mode) {
        yearMode = mode;
        localStorage.setItem('dwYearMode', mode);
        // Convert selectedColumns to new mode
        selectedColumns = selectedColumns.map(col => storageKey(col));
        updateYearModeButtons();
        syncCheckboxes();
    }

    // Wire toggle buttons (may not be in DOM yet when column sidebar loads via HTMX)
    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-year-mode-btn]');
        if (btn) {
            setYearMode(btn.dataset.yearModeBtn);
        }
    });
    const selectedCountEl = document.getElementById('selected-count');
    const selectedCountToolbarEl = document.getElementById('selected-count-toolbar');
    const sidebarEl = document.getElementById('column-selector');

    const selectedStudentCountEls = Array.from(
        document.querySelectorAll('#selected-student-count, #selected-student-count-toolbar')
    );
    const renderSelectedStudentCount = () => {
        selectedStudentCountEls.forEach((el) => {
            el.textContent = String(selectedStudentSsids.size);
        });
    };

    const getCsrfToken = () => {
        const direct = (appDataEl?.dataset?.csrfToken || '').trim();
        if (direct && direct !== 'NOTPROVIDED') return direct;

        const cookie = (typeof window.dwGetCookie === 'function')
            ? window.dwGetCookie('csrftoken')
            : (document.cookie.match(/(?:^|; )csrftoken=([^;]*)/) || [])[1];
        if (cookie) return decodeURIComponent(cookie);

        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return (input?.value || '').trim();
    };

    const getCurrentFiltersForSave = () => {
        return {
            school_year: document.getElementById('school_year')?.value,
            grade: document.getElementById('grade')?.value,
            status: document.getElementById('status')?.value,
            search: document.getElementById('search')?.value,
            ...filters,
        };
    };

    const saveViewState = () => {
        try {
            const payload = {
                selectedColumns,
                filters,
                sortBy,
                sortDir,
                search: document.getElementById('search')?.value || '',
                school_year: document.getElementById('school_year')?.value || '',
                grade: document.getElementById('grade')?.value || '',
                status: document.getElementById('status')?.value || '',
                page,
            };
            sessionStorage.setItem(stateStorageKey, JSON.stringify(payload));
        } catch (e) {
            // best-effort
        }
    };

    const restoreViewState = () => {
        if (stateRestored) return;
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('saved_query') || urlParams.get('student_list')) {
            stateRestored = true;
            return;
        }
        try {
            const raw = sessionStorage.getItem(stateStorageKey);
            if (!raw) {
                stateRestored = true;
                return;
            }
            const stored = JSON.parse(raw);
            if (Array.isArray(stored.selectedColumns) && stored.selectedColumns.length) {
                selectedColumns = stored.selectedColumns;
            }
            if (stored.filters && typeof stored.filters === 'object') {
                filters = stored.filters;
            }
            if (stored.sortBy) sortBy = stored.sortBy;
            if (stored.sortDir) sortDir = stored.sortDir;
            if (typeof stored.page === 'number' && stored.page > 0) page = stored.page;

            const searchInput = document.getElementById('search');
            const schoolYearSelect = document.getElementById('school_year');
            const gradeSelect = document.getElementById('grade');
            const statusInput = document.getElementById('status');

            if (searchInput && stored.search !== undefined) searchInput.value = stored.search || '';
            if (schoolYearSelect && stored.school_year !== undefined) schoolYearSelect.value = stored.school_year || '';
            if (gradeSelect && stored.grade !== undefined) gradeSelect.value = stored.grade || '';
            if (statusInput && stored.status !== undefined) statusInput.value = stored.status || '';
        } catch (e) {
            // ignore
        } finally {
            stateRestored = true;
        }
    };

    const getCurrentQueryConfigForSave = () => {
        return {
            columns: selectedColumns,
            filters: getCurrentFiltersForSave(),
            sort_by: sortBy,
            sort_dir: sortDir,
        };
    };

    const statsCache = {};
    const clearStatsCache = () => {
        Object.keys(statsCache).forEach(k => delete statsCache[k]);
    };

    const fetchColumnStats = async (col) => {
        if (statsCache[col]) return statsCache[col];
        if (!columnStatisticsUrl) return null;

        const url = new URL(columnStatisticsUrl, window.location.origin);
        url.searchParams.set('target_columns', JSON.stringify([col]));
        url.searchParams.set('school_year', document.getElementById('school_year')?.value || '');
        url.searchParams.set('grade', document.getElementById('grade')?.value || '');
        url.searchParams.set('status', document.getElementById('status')?.value || '');
        url.searchParams.set('search', document.getElementById('search')?.value || '');
        if (filters && Object.keys(filters).length) {
             url.searchParams.set('filters', JSON.stringify(filters));
        }

        try {
            const res = await fetch(url.toString(), { credentials: 'same-origin' });
            if (!res.ok) return null;
            const data = await res.json();
            const stats = data.columns && data.columns[col];
            if (stats) statsCache[col] = stats;
            return stats;
        } catch (e) {
            console.error(e);
            return null;
        }
    };

    const refreshColumnStructures = async () => {
        if (!columnStructuresUrl) return;
        const target = document.getElementById('dynamic-column-structures');
        if (!target) return;

        const openCategories = Array.from(target.querySelectorAll('[data-category]'))
            .filter((el) => !el.classList.contains('hidden'))
            .map((el) => el.getAttribute('data-category'))
            .filter(Boolean);

        const columnFilters = {
            school_year: document.getElementById('school_year')?.value || '',
            grade: document.getElementById('grade')?.value || '',
            status: document.getElementById('status')?.value || '',
            search: document.getElementById('search')?.value || '',
            ...filters,
        };

        const url = new URL(columnStructuresUrl, window.location.origin);
        url.searchParams.set('column_filters', JSON.stringify(columnFilters));

        try {
            const res = await fetch(url.toString(), { credentials: 'same-origin' });
            if (!res.ok) return;
            target.innerHTML = await res.text();
            if (window.htmx) htmx.process(target);
            if (openCategories.length) {
                openCategories.forEach((category) => {
                    const el = target.querySelector(`[data-category="${cssEscape(category)}"]`);
                    if (el) {
                        el.classList.remove('hidden');
                        const button = el.previousElementSibling;
                        if (button) {
                            button.querySelectorAll('[data-caret="open"]').forEach((c) => c.classList.remove('hidden'));
                            button.querySelectorAll('[data-caret="closed"]').forEach((c) => c.classList.add('hidden'));
                        }
                    }
                });
            }
            renderSelectedColumns();
            updateYearModeButtons();
        } catch (e) {
            // best-effort
        }
    };

    const getCurrentPageSsids = () => {
        if (!lastPayload || !Array.isArray(lastPayload.students)) return [];
        return lastPayload.students.map((s) => s?.ssid).filter((v) => v && String(v).trim());
    };

    const syncSelectAllCheckboxState = () => {
        const selectAllEl = document.querySelector('#student-table input[data-action="select-all-visible"]');
        if (!selectAllEl) return;
        const pageSsids = getCurrentPageSsids();
        if (pageSsids.length === 0) {
            selectAllEl.checked = false;
            selectAllEl.indeterminate = false;
            return;
        }
        let selectedOnPage = 0;
        for (const ssid of pageSsids) {
            if (selectedStudentSsids.has(String(ssid))) selectedOnPage += 1;
        }
        selectAllEl.checked = selectedOnPage === pageSsids.length;
        selectAllEl.indeterminate = selectedOnPage > 0 && selectedOnPage < pageSsids.length;
    };

    const syncCheckboxes = () => {
        document.querySelectorAll('input[data-column]').forEach((checkbox) => {
            const col = checkbox.dataset.column;
            // Match regardless of whether stored as specific or relative key
            const matches = selectedColumns.includes(col)
                || selectedColumns.includes(normaliseKey(col))
                || selectedColumns.includes(toRelativeKey(normaliseKey(col)));
            checkbox.checked = matches;
        });
        document.querySelectorAll('input[data-action="select-topic"]').forEach((topicCb) => {
            const targetCategory = topicCb.dataset.targetCategory;
            const container = document.querySelector(`[data-category="${targetCategory}"]`);
            if (!container) return;
            const cols = Array.from(container.querySelectorAll('input[data-column]'))
                .map(cb => cb.dataset.column).filter(Boolean);
            if (cols.length === 0) { topicCb.checked = false; topicCb.indeterminate = false; return; }
            const selectedCount = cols.filter(c => selectedColumns.includes(c)).length;
            topicCb.checked = selectedCount === cols.length;
            topicCb.indeterminate = selectedCount > 0 && selectedCount < cols.length;
        });
    };

    const renderSelectedColumns = () => {
        if (selectedCountEl) selectedCountEl.textContent = selectedColumns.length;
        if (selectedCountToolbarEl) selectedCountToolbarEl.textContent = String(selectedColumns.length);
        const selectedLabels = selectedColumns
            .map((col) => {
                const label = columnDefinitions[col] || col;
                return label.includes('\n') ? label.split('\n')[0] : label;
            })
            .join(', ');
        const tooltipText = selectedLabels || 'No columns selected';
        if (selectedCountEl) selectedCountEl.setAttribute('title', tooltipText);
        if (selectedCountToolbarEl) selectedCountToolbarEl.setAttribute('title', tooltipText);
        syncCheckboxes();
    };

    const toggleCategory = (category) => {
        const section = document.querySelector(`[data-category="${category}"]`);
        if (!section) return;
        section.classList.toggle('hidden');

        const button = document.querySelector(`[data-toggle-category="${category}"]`);
        if (button) {
            const openCaret = button.querySelector('[data-caret="open"]');
            const closedCaret = button.querySelector('[data-caret="closed"]');
            const isHidden = section.classList.contains('hidden');
            if (openCaret) openCaret.classList.toggle('hidden', isHidden);
            if (closedCaret) closedCaret.classList.toggle('hidden', !isHidden);
        }
    };

    const toggleSidebar = (show) => {
        if (!sidebarEl) return;
        
        if (show) {
            // Show sidebar
            sidebarEl.classList.remove('hidden');
            // Force reflow
            sidebarEl.offsetHeight;
            // Slide in
            sidebarEl.classList.remove('-translate-x-full');
        } else {
            // Slide out
            sidebarEl.classList.add('-translate-x-full');
            // Hide after animation
            setTimeout(() => {
                if (sidebarEl.classList.contains('-translate-x-full')) {
                    sidebarEl.classList.add('hidden');
                }
            }, 300);
        }
        
        // Handle backdrop overlay
        const backdrop = document.getElementById('column-selector-backdrop');
        if (backdrop) {
            backdrop.classList.toggle('hidden', !show);
        }
        
        try {
            sessionStorage.setItem('columnSidebarOpen', show ? 'true' : 'false');
        } catch (e) {
            // ignore
        }
        
        // Update floating tab position when sidebar opens/closes
        applyToggleEdgeLeft();
    };

    const draggableToggle = document.querySelector('[data-draggable="column-toggle"]');
    let dragState = null;
    let lastDragAt = 0;

    const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

    const getToggleEdgeLeft = () => {
        const sidebarOpen = sessionStorage.getItem('columnSidebarOpen') === 'true';
        const edgeLeft = sidebarOpen && window.innerWidth >= 1024 ? 320 : 0;
        const edgeOffset = 8;
        return edgeLeft + edgeOffset;
    };

    const applyToggleEdgeLeft = () => {
        if (!draggableToggle) return;
        draggableToggle.style.left = `${getToggleEdgeLeft()}px`;
    };

    const restoreTogglePosition = () => {
        if (!draggableToggle) return;
        const stored = localStorage.getItem('columnTogglePosition');
        applyToggleEdgeLeft();
        if (!stored) return;
        try {
            const pos = JSON.parse(stored);
            if (!pos || typeof pos.top !== 'number') return;
            draggableToggle.style.top = `${pos.top}px`;
            draggableToggle.style.transform = 'none';
        } catch (e) {
            // ignore
        }
    };

    const saveTogglePosition = () => {
        if (!draggableToggle) return;
        const rect = draggableToggle.getBoundingClientRect();
        localStorage.setItem('columnTogglePosition', JSON.stringify({
            top: rect.top,
        }));
        localStorage.setItem('columnToggleUserMoved', 'true');
    };

    const startDrag = (event) => {
        if (!draggableToggle) return;
        const isPrimary = event.button === 0 || event.type.startsWith('touch');
        if (!isPrimary) return;
        const rect = draggableToggle.getBoundingClientRect();
        const clientX = event.touches ? event.touches[0].clientX : event.clientX;
        const clientY = event.touches ? event.touches[0].clientY : event.clientY;

        dragState = {
            startX: clientX,
            startY: clientY,
            offsetX: clientX - rect.left,
            offsetY: clientY - rect.top,
            moved: false,
        };

        draggableToggle.classList.add('cursor-grabbing');
        event.preventDefault();
    };

    const moveDrag = (event) => {
        if (!dragState || !draggableToggle) return;
        const clientX = event.touches ? event.touches[0].clientX : event.clientX;
        const clientY = event.touches ? event.touches[0].clientY : event.clientY;
        const deltaX = Math.abs(clientX - dragState.startX);
        const deltaY = Math.abs(clientY - dragState.startY);
        if (deltaX > 4 || deltaY > 4) dragState.moved = true;

        const maxTop = window.innerHeight - draggableToggle.offsetHeight - 8;
        const nextTop = clamp(clientY - dragState.offsetY, 8, Math.max(8, maxTop));

        draggableToggle.style.left = `${getToggleEdgeLeft()}px`;
        draggableToggle.style.top = `${nextTop}px`;
        draggableToggle.style.transform = 'none';
        event.preventDefault();
    };

    const endDrag = () => {
        if (!dragState || !draggableToggle) return;
        draggableToggle.classList.remove('cursor-grabbing');
        if (dragState.moved) {
            saveTogglePosition();
            lastDragAt = Date.now();
        }
        dragState = null;
    };

    if (draggableToggle) {
        restoreTogglePosition();
        applyToggleEdgeLeft();
        draggableToggle.addEventListener('mousedown', startDrag);
        draggableToggle.addEventListener('touchstart', startDrag, { passive: false });
        window.addEventListener('mousemove', moveDrag);
        window.addEventListener('touchmove', moveDrag, { passive: false });
        window.addEventListener('mouseup', endDrag);
        window.addEventListener('touchend', endDrag);
        window.addEventListener('touchcancel', endDrag);
        window.addEventListener('resize', applyToggleEdgeLeft);
    }

    const escapeHtml = (value) => {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    };

    const cssEscape = (value) => {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value));
        return String(value).replace(/[^a-zA-Z0-9_\-]/g, '_');
    };

    const DATE_COLUMNS = new Set(['birth_date', 'us_enter_date', 'fep_date']);
    const isDateLikeString = (value) => {
        if (typeof value !== 'string') return false;
        return /^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$/.test(value);
    };
    const formatDateForDisplay = (value) => {
        if (!isDateLikeString(value)) return value;
        const datePart = value.slice(0, 10);
        const d = new Date(`${datePart}T00:00:00`);
        if (Number.isNaN(d.getTime())) return datePart;
        try {
            return new Intl.DateTimeFormat('en-US').format(d);
        } catch (e) {
            return datePart;
        }
    };
    const formatValueForColumn = (col, value) => {
        if (value === null || value === undefined) return '—';
        if (DATE_COLUMNS.has(col)) {
            const s = String(value);
            return formatDateForDisplay(s);
        }
        return value;
    };

    const renderTable = (payload) => {
        const targetEl = document.querySelector('#student-table');
        if (!targetEl) return;

        lastPayload = payload;

        if (payload.column_definitions) {
            // Merge definitions to capture dynamic test attributes
            Object.assign(columnDefinitions, payload.column_definitions);
        } else if (Object.keys(columnDefinitions).length === 0 && payload.column_definitions) {
            columnDefinitions = payload.column_definitions;
        }

        if (Array.isArray(payload.selected_columns)) {
            selectedColumns = payload.selected_columns;
        }

        if (payload.filters) {
            filters = payload.filters;
        }

        if (payload.sort_by) {
            sortBy = payload.sort_by;
        }
        if (payload.sort_dir) {
            sortDir = payload.sort_dir;
        }

        const searchInput = document.getElementById('search');
        const schoolYearSelect = document.getElementById('school_year');
        const gradeSelect = document.getElementById('grade');
        const statusInput = document.getElementById('status');

        if (payload.search !== undefined && searchInput) searchInput.value = payload.search || '';
        if (payload.school_year !== undefined && schoolYearSelect) schoolYearSelect.value = payload.school_year || '';
        if (payload.grade !== undefined && gradeSelect) gradeSelect.value = payload.grade || '';
        if (payload.status !== undefined && statusInput) statusInput.value = payload.status || '';

        const columns = selectedColumns || [];
        const students = payload.students || [];
        const total = payload.total || 0;
        const pageCount = payload.total_pages || 1;
        const currentPage = payload.page || 1;
        const filterOptions = payload.filter_options || {};
        const surveyColors = payload.survey_colors || {};

        // Update Total Count
        const totalCountEl = document.getElementById('total-student-count');
        if (totalCountEl) totalCountEl.textContent = total.toLocaleString();

        // Update Active Filters Display
        const activeFiltersListEl = document.getElementById('active-filters-list');
        const filterSeparatorEl = document.getElementById('filter-separator');

        if (activeFiltersListEl) {
            activeFiltersListEl.innerHTML = '';
            let hasActiveFilters = false;

            Object.keys(filters).forEach(col => {
                const vals = filters[col];
                if (Array.isArray(vals) && vals.length > 0) {
                    hasActiveFilters = true;
                    // Get first line of label for brevity
                    let colLabel = columnDefinitions[col] || col;
                    if (colLabel.includes('\n')) colLabel = colLabel.split('\n')[0];

                    const chip = document.createElement('span');
                    chip.className = 'inline-flex items-center gap-1 bg-blue-50 text-blue-700 px-2 py-0.5 rounded border border-blue-200';

                    let valText = vals.map(v => (v === '' || v === null) ? '(Blank)' : String(v)).join(', ');
                    if (valText.length > 20) valText = `${vals.length} selected`;

                    chip.innerHTML = `<span class="font-semibold">${escapeHtml(colLabel)}:</span> ${escapeHtml(valText)} <button type="button" data-action="clear-filter" data-column="${escapeHtml(col)}" class="ml-1 text-blue-500 hover:text-blue-900 font-bold focus:outline-none">×</button>`;
                    activeFiltersListEl.appendChild(chip);
                }
            });

            if (filterSeparatorEl) {
                filterSeparatorEl.classList.toggle('hidden', !hasActiveFilters);
            }
        }

        let html = '<div class="table-container">';
        html += '<table class="min-w-full divide-y divide-slate-200">';

        // Column widths (persisted client-side)
        html += '<colgroup>';
        html += '<col style="width:44px">';
        columns.forEach((col) => {
            const width = columnWidths[col];
            const widthStyle = Number.isFinite(width) ? ` style="width:${Math.max(60, width)}px"` : '';
            html += `<col data-col="${escapeHtml(col)}"${widthStyle}>`;
        });
        html += '</colgroup>';

        html += '<thead class="bg-slate-900 text-white sticky top-0 z-10 shadow-md"><tr class="bg-slate-900">';

        // Selection column header
        html += '<th class="px-4 py-3 text-left text-xs font-bold text-slate-400 uppercase bg-slate-900 border-b border-slate-800">';
        html += '<input type="checkbox" data-action="select-all-visible" class="rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-slate-900 cursor-pointer" aria-label="Select all visible students">';
        html += '</th>';

        columns.forEach((col, index) => {
            const label = columnDefinitions[col] || col;
            const optionsForCol = Array.isArray(filterOptions[col]) ? filterOptions[col] : [];
            const hasActiveFilter = Array.isArray(filters[col]) && filters[col].length > 0;
            const hasFilter = optionsForCol.length > 0 || hasActiveFilter;
            const isSorted = sortBy === col;
            const sortIcon = isSorted ? (sortDir === 'asc' ? '▲' : '▼') : '↕';
            const sortColorClass = isSorted ? 'text-blue-400' : 'text-slate-600 group-hover:text-slate-400';
            
            const isRightSide = index > (columns.length - 3) && columns.length > 3;
            const alignClass = isRightSide ? 'right-0' : 'left-0';

            // For survey columns use an abbreviated header; full question text goes in title attribute
            let headerLabel = label;
            let headerTitle = label;
            if (col.startsWith('survey_')) {
                const qMatch = col.match(/_Q(\d+)$/);
                const scInfo = surveyColors[col];
                const shortBase = scInfo && scInfo.short_label ? scInfo.short_label : '';
                const qPart = qMatch ? `Q${qMatch[1]}` : '';
                const topicPart = scInfo && scInfo.topic ? scInfo.topic : '';
                const parts = [shortBase, topicPart, qPart].filter(Boolean);
                headerLabel = parts.length > 0 ? parts.join('\n') : (qPart || label);
                headerTitle = label;
            }

            html += `<th draggable="true" data-column="${escapeHtml(col)}" title="${escapeHtml(headerTitle)}" class="relative group px-4 py-3 text-left text-xs font-bold text-slate-300 uppercase bg-slate-900 border-b border-slate-800 hover:bg-slate-800 transition-colors select-none">`;
            html += `<div class="flex items-center justify-between gap-2">`;
            html += `<div class="flex items-center gap-2 min-w-0 flex-1 cursor-pointer group/sort" data-action="sort" data-column="${escapeHtml(col)}">`;
            html += `<span class="whitespace-pre-wrap break-words group-hover/sort:text-white transition-colors">${escapeHtml(headerLabel)}</span>`;
            html += `<span class="text-[10px] ${sortColorClass} group-hover/sort:text-white transition-colors flex-shrink-0">${sortIcon}</span>`;
            html += `</div>`;

            // Stats Button
            html += `<div class="relative ml-1">`;
            html += `<button draggable="false" type="button" data-action="toggle-stats" data-column="${escapeHtml(col)}" class="p-1 rounded hover:bg-slate-700 text-slate-500 group-hover:text-slate-300 transition-colors" title="Column Statistics">`;
            html += `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 002 2h2a2 2 0 002-2z"></path></svg>`;
            html += `</button>`;
            html += `<div class="hidden absolute ${alignClass} top-full mt-2 w-72 bg-white rounded-md shadow-xl border border-slate-200 z-30 text-slate-700 font-normal normal-case text-left cursor-default p-0" data-stats-panel="${escapeHtml(col)}">`;
            html += `<div class="p-2 border-b border-slate-100 font-bold text-slate-800 bg-slate-50 rounded-t-md flex justify-between items-center text-xs"><span>Statistics</span><button data-action="toggle-stats" data-column="${escapeHtml(col)}" class="text-slate-400 hover:text-red-500 px-1">×</button></div>`;
            html += `<div class="p-2 max-h-80 overflow-y-auto" data-stats-content="${escapeHtml(col)}"><div class="animate-pulse flex space-x-4"><div class="flex-1 space-y-2 py-1"><div class="h-2 bg-slate-200 rounded"></div><div class="h-2 bg-slate-200 rounded w-5/6"></div></div></div></div>`;
            html += `</div>`;
            html += `</div>`;

            if (hasFilter || col === 'atendace_rate') {
                const filterIconClass = hasActiveFilter ? 'text-blue-400' : 'text-slate-500 group-hover:text-slate-300';
                html += `<div class="relative ml-1">`;
                html += `<button draggable="false" type="button" data-action="toggle-filter" data-column="${escapeHtml(col)}" class="p-1 rounded hover:bg-slate-700 ${filterIconClass} transition-colors" title="Filter list">`;
                html += `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"></path></svg>`;
                html += `</button>`;
                html += `<div class="hidden absolute right-0 mt-2 w-56 bg-white rounded-md shadow-xl border border-slate-200 py-1 z-20 text-slate-700 font-normal normal-case text-left" data-filter-panel="${escapeHtml(col)}">`;
                
                if (col === 'atendace_rate') {
                     html += `<div class="p-2 border-b border-slate-200 space-y-2 bg-slate-50">`;
                     html += `<div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider">Range %</div>`;
                     html += `<div class="flex gap-2 items-center">`;
                     html += `<input type="number" placeholder="Min" id="filter-min-${col}" class="w-16 text-xs border border-slate-300 rounded px-1.5 py-1" min="0" max="100">`;
                     html += `<span class="text-slate-400 text-xs">to</span>`;
                     html += `<input type="number" placeholder="Max" id="filter-max-${col}" class="w-16 text-xs border border-slate-300 rounded px-1.5 py-1" min="0" max="100">`;
                     html += `</div>`;
                     html += `<div class="flex flex-wrap gap-1.5">`;
                     html += `<button type="button" data-action="quick-filter" data-column="${col}" data-value="<90" class="text-[10px] bg-white border border-slate-200 text-slate-600 px-2 py-0.5 rounded-full hover:bg-blue-50 hover:text-blue-600 hover:border-blue-200 transition-colors">< 90%</button>`;
                     html += `<button type="button" data-action="quick-filter" data-column="${col}" data-value=">=95" class="text-[10px] bg-white border border-slate-200 text-slate-600 px-2 py-0.5 rounded-full hover:bg-blue-50 hover:text-blue-600 hover:border-blue-200 transition-colors">95%+</button>`;
                     html += `</div>`;
                     html += `<button type="button" data-action="apply-range" data-column="${col}" class="w-full text-xs bg-blue-600 text-white py-1 rounded hover:bg-blue-700 transition-colors">Apply Range</button>`;
                     html += `</div>`;
                }

                html += `<div class="flex items-center justify-between px-2 py-1 text-[11px] text-slate-500 border-b border-slate-200">`;
                html += `<button draggable="false" type="button" data-action="select-all-filter-options" data-column="${escapeHtml(col)}" class="hover:text-slate-700">Select all</button>`;
                html += `<button draggable="false" type="button" data-action="clear-all-filter-options" data-column="${escapeHtml(col)}" class="hover:text-slate-700">Clear</button>`;
                html += `</div>`;
                html += `<div class="max-h-56 overflow-auto p-2 space-y-1">`;
                if (optionsForCol.length === 0) {
                    html += `<div class="text-xs text-slate-500">No options available.</div>`;
                } else {
                    optionsForCol.forEach((value) => {
                        const checked = filters[col]?.includes(value) ? 'checked' : '';
                        let labelValue = String(value);
                        if (value === '' || value === null) {
                            labelValue = '(Blank)';
                        } else if (DATE_COLUMNS.has(col)) {
                            labelValue = formatDateForDisplay(labelValue);
                        }
                        html += `<label class="flex items-center gap-2 text-xs text-slate-700 p-1 rounded hover:bg-slate-50">`;
                        html += `<input draggable="false" type="checkbox" data-filter-value="${escapeHtml(value)}" data-filter-column="${escapeHtml(col)}" ${checked} class="rounded border-slate-300 text-blue-600 focus:ring-blue-600">`;
                        html += `<span>${escapeHtml(labelValue)}</span>`;
                        html += `</label>`;
                    });
                }
                html += `</div>`;
                html += `<div class="flex items-center justify-between border-t border-slate-200 px-2 py-1 text-xs">`;
                html += `<button draggable="false" type="button" data-action="clear-filter" data-column="${escapeHtml(col)}" class="text-slate-500 hover:text-slate-700">Reset</button>`;
                html += `<button draggable="false" type="button" data-action="apply-filter" data-column="${escapeHtml(col)}" class="text-blue-600 hover:text-blue-700 font-medium">Apply</button>`;
                html += `</div>`;
                html += `</div>`;
                html += `</div>`;
            }

            // Remove column action (X)
            html += `<button draggable="false" type="button" data-action="remove-column" data-column="${escapeHtml(col)}" class="opacity-0 group-hover:opacity-100 transition-opacity text-slate-600 hover:text-red-400 hover:bg-slate-800 rounded p-0.5 ml-1" title="Remove column">✕</button>`;
            html += `</div>`;

            // Resize handle
            html += `<span draggable="false" data-action="resize-handle" data-column="${escapeHtml(col)}" class="absolute top-0 right-0 h-full w-1 cursor-col-resize"></span>`;
            html += `</th>`;
        });
        html += '</tr></thead><tbody class="bg-white divide-y divide-slate-200">';

        if (students.length === 0) {
            html += `<tr><td colspan="${columns.length + 1}" class="px-6 py-8 text-center text-slate-500">No students found</td></tr>`;
        } else {
            students.forEach((student) => {
                const ssid = student?.ssid ? String(student.ssid) : '';
                const checked = ssid && selectedStudentSsids.has(ssid) ? 'checked' : '';
                html += '<tr class="hover:bg-slate-50">';

                // Selection cell
                if (ssid) {
                    html += `<td class="px-4 py-3 text-sm whitespace-nowrap">`;
                    html += `<input type="checkbox" data-action="select-student" data-ssid="${escapeHtml(ssid)}" ${checked} class="rounded border-slate-300 text-blue-600 focus:ring-blue-600" aria-label="Select student">`;
                    html += `</td>`;
                } else {
                    html += `<td class="px-4 py-3 text-sm whitespace-nowrap">—</td>`;
                }

                columns.forEach((col) => {
                    const value = formatValueForColumn(col, student[col]);
                    if ((col === 'ssid' || col === 'student_id') && student?.ssid) {
                        const href = buildStudentSummaryUrl(student.ssid);
                        const ssidAttr = escapeHtml(student.ssid);
                        if (href) {
                            html += `<td class="px-4 py-3 text-sm whitespace-nowrap"><a class="text-blue-600 hover:text-blue-800 hover:underline" href="${escapeHtml(href)}" data-action="open-student-summary" data-ssid="${ssidAttr}">${escapeHtml(value)}</a></td>`;
                        } else {
                            html += `<td class="px-4 py-3 text-sm text-slate-900 whitespace-nowrap">${escapeHtml(value)}</td>`;
                        }
                    } else if (col.startsWith('survey_') && value !== '—' && value !== '' && surveyColors[col]) {
                        const _scInfo = surveyColors[col];
                        const _scMax = _scInfo.scale_max || 5;
                        const _scColors = (_scInfo.colors || {})['scale_' + _scMax] || {};
                        const _scBg = _scColors[String(value)];
                        const _scStyle = _scBg ? ` style="background-color:${escapeHtml(_scBg)};"` : '';
                        html += `<td class="px-4 py-3 text-sm text-slate-900 whitespace-nowrap text-center font-medium"${_scStyle}>${escapeHtml(value)}</td>`;
                    } else {
                        html += `<td class="px-4 py-3 text-sm text-slate-900 whitespace-nowrap">${escapeHtml(value)}</td>`;
                    }
                });
                html += '</tr>';
            });
        }

        html += '</tbody></table></div>';

        if (pageCount > 1) {
            html += '<div class="bg-slate-50 px-4 py-3 border-t border-slate-200 flex items-center justify-between">';
            html += `<div class="text-sm text-slate-700">Showing ${students.length} of ${total} students</div>`;
            html += '<div class="flex gap-2">';
            if (currentPage > 1) {
                html += `<button data-action="prev-page" data-page="${currentPage - 1}" class="px-4 py-2 border border-slate-300 text-sm font-medium rounded-md text-slate-700 bg-white hover:bg-slate-50">Previous</button>`;
            }
            html += `<span class="px-3 py-2 text-sm text-slate-700">Page ${currentPage} of ${pageCount}</span>`;
            if (currentPage < pageCount) {
                html += `<button data-action="next-page" data-page="${currentPage + 1}" class="px-4 py-2 border border-slate-300 text-sm font-medium rounded-md text-slate-700 bg-white hover:bg-slate-50">Next</button>`;
            }
            html += '</div></div>';
        }

        targetEl.innerHTML = html;
        renderSelectedColumns();
        renderSelectedStudentCount();
        syncSelectAllCheckboxState();
        scheduleWarmStudentSummaries();
        
        // Initialize horizontal scroll synchronization after table renders
        setTimeout(() => initializeScrollSync(), 100);
    };

    document.addEventListener('click', async (event) => {
        const toggleStatsEl = event.target.closest('[data-action="toggle-stats"]');
        if (toggleStatsEl) {
            event.preventDefault();
            event.stopPropagation();
            const col = toggleStatsEl.dataset.column;
            const panel = document.querySelector(`[data-stats-panel="${cssEscape(col)}"]`);
            if (!panel) return;
            
            const isHidden = panel.classList.contains('hidden');
            
            // Close all others
            document.querySelectorAll('[data-stats-panel]').forEach(p => {
                if (p !== panel && !p.classList.contains('hidden')) p.classList.add('hidden');
            });

            if (isHidden) {
                panel.classList.remove('hidden');
                const contentEl = panel.querySelector(`[data-stats-content]`);
                if (contentEl) {
                     contentEl.innerHTML = '<div class="space-y-2 p-2"><div class="animate-pulse h-2 bg-slate-200 rounded w-full"></div><div class="animate-pulse h-2 bg-slate-200 rounded w-3/4"></div></div>';
                     const stats = await fetchColumnStats(col);
                     if (stats) {
                         // Render stats
                         let html = `<div class="space-y-3 text-xs">`;
                         html += `<div class="grid grid-cols-2 gap-2 text-center pb-2 border-b border-slate-100">`;
                         html += `<div><div class="text-[10px] text-slate-500 uppercase tracking-wider">Count</div><div class="font-bold text-slate-800 text-sm">${stats.count}</div></div>`;
                         html += `<div><div class="text-[10px] text-slate-500 uppercase tracking-wider">Unique</div><div class="font-bold text-slate-800 text-sm">${stats.unique}</div></div>`;
                         if (stats.avg !== undefined && stats.avg !== null) {
                             html += `<div><div class="text-[10px] text-slate-500 uppercase tracking-wider">Avg</div><div class="font-bold text-slate-800 text-sm">${stats.avg}</div></div>`;
                             html += `<div><div class="text-[10px] text-slate-500 uppercase tracking-wider">Median</div><div class="font-bold text-slate-800 text-sm">${stats.median}</div></div>`;
                         }
                         html += `</div>`;
                         
                         html += `<div><div class="text-slate-500 mb-2 font-semibold flex justify-between items-center"><span>Values</span> <span class="text-[10px] font-normal text-slate-400">Top 50</span></div>`;
                         html += `<div class="space-y-1.5 max-h-48 overflow-y-auto pr-1">`;
                         
                         // Sort distribution by count desc
                         const dist = (stats.distribution || []).sort((a, b) => b.count - a.count);
                         
                         dist.forEach(d => {
                            const pct = d.percent;
                            html += `<div class="flex items-center gap-2 text-[11px]">`;
                            html += `<div class="w-24 truncate text-slate-700" title="${escapeHtml(d.value)}">${(d.value === '' || d.value === null) ? '<em class="text-slate-400">(Blank)</em>' : escapeHtml(d.value)}</div>`;
                            html += `<div class="flex-1 h-3 bg-slate-100 rounded-sm overflow-hidden relative"><div class="absolute top-0 left-0 h-full bg-blue-500" style="width:${pct}%"></div></div>`;
                            html += `<div class="w-16 text-right text-slate-500 font-mono text-[10px]"><span class="font-bold text-slate-700">${d.count}</span> (${pct}%)</div>`;
                            html += `</div>`;
                         });
                         html += `</div></div>`;
                         html += `</div>`;
                         contentEl.innerHTML = html;
                     } else {
                         contentEl.innerHTML = '<div class="text-red-500 p-2 text-xs">Failed to load statistics.</div>';
                     }
                }
            } else {
                panel.classList.add('hidden');
            }
            return;
        }

        const openSummaryEl = event.target.closest('[data-action="open-student-summary"]');
        if (openSummaryEl) {
            const isPlainLeftClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
            if (isPlainLeftClick) {
                event.preventDefault();
                const ssid = openSummaryEl.getAttribute('data-ssid') || '';
                const url = buildStudentSummaryPartialUrl(ssid);
                showOverlay(summaryOverlayEl);
                await loadOverlayHtml(url, summaryOverlayBodyEl);
            }
            return;
        }

        const closeSummaryEl = event.target.closest('[data-action="close-student-summary"]');
        if (closeSummaryEl) {
            event.preventDefault();
            hideOverlay(summaryOverlayEl);
            if (summaryOverlayBodyEl) summaryOverlayBodyEl.innerHTML = '';
            return;
        }


    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (summaryOverlayEl && !summaryOverlayEl.classList.contains('hidden')) {
            hideOverlay(summaryOverlayEl);
            if (summaryOverlayBodyEl) summaryOverlayBodyEl.innerHTML = '';
        }
    });

    const loadData = () => {
        showLoading('Loading student data...');
        clearStatsCache();

        restoreViewState();
        
        const url = new URL(loadStudentsUrl, window.location.origin);
        url.searchParams.set('page', page);

        refreshColumnStructures();

        // Include saved_query or student_list from URL if present
        const urlParams = new URLSearchParams(window.location.search);
        const savedQueryId = urlParams.get('saved_query');
        const studentListId = urlParams.get('student_list');
        if (savedQueryId) url.searchParams.set('saved_query', savedQueryId);
        if (studentListId) url.searchParams.set('student_list', studentListId);

        const isSavedQueryLoad = Boolean(savedQueryId);
        const isInitialSavedQueryLoad = isSavedQueryLoad && lastPayload === null;
        if (!isInitialSavedQueryLoad) {
            if (Array.isArray(selectedColumns) && selectedColumns.length > 0) {
                url.searchParams.set('columns', JSON.stringify(selectedColumns));
            }
            if (sortBy) {
                url.searchParams.set('sort_by', sortBy);
                url.searchParams.set('sort_dir', sortDir);
            }
            if (filters && Object.keys(filters).length > 0) {
                url.searchParams.set('filters', JSON.stringify(filters));
            } else if (lastPayload !== null) {
                url.searchParams.set('filters', '{}');
            }
        }
        url.searchParams.set('format', 'json');

        const searchInput = document.getElementById('search');
        const schoolYearSelect = document.getElementById('school_year');
        const gradeSelect = document.getElementById('grade');
        const statusInput = document.getElementById('status');

        if (!isInitialSavedQueryLoad) {
            if (pastedIdList && pastedIdList.length) {
                url.searchParams.set('search_ids', JSON.stringify(pastedIdList));
            } else if (searchInput && searchInput.value) {
                url.searchParams.set('search', searchInput.value);
            }
            if (schoolYearSelect && schoolYearSelect.value) url.searchParams.set('school_year', schoolYearSelect.value);
            if (gradeSelect && gradeSelect.value) url.searchParams.set('grade', gradeSelect.value);
            if (statusInput) url.searchParams.set('status', statusInput.value);
        }

        fetch(url.toString(), { credentials: 'same-origin' })
            .then((response) => response.json())
            .then((payload) => {
                renderTable(payload);
                saveViewState();
                hideLoading();
            })
            .catch((error) => {
                console.error('Error loading data:', error);
                hideLoading();
                const targetEl = document.querySelector('#student-table');
                if (targetEl) {
                    targetEl.innerHTML = '<div class="text-center py-8 text-red-600">Failed to load data.</div>';
                }
            });
    };

    const updateColumnSelection = (col) => {
        const key = storageKey(col);
        // Check against all forms of the key
        const isSelected = selectedColumns.some(c => normaliseKey(c) === normaliseKey(key));
        if (isSelected) {
            if (selectedColumns.length <= 1) return;
            const normKey = normaliseKey(key);
            selectedColumns = selectedColumns.filter(c => normaliseKey(c) !== normKey);
            if (filters[key]) delete filters[key];
        } else {
            selectedColumns.push(key);
        }
        page = 1;
        renderSelectedColumns();
        loadData();
    };

    document.addEventListener('change', (event) => {
        const topicCheckbox = event.target.closest('input[data-action="select-topic"]');
        if (topicCheckbox) {
            const targetCategory = topicCheckbox.dataset.targetCategory;
            const container = document.querySelector(`[data-category="${targetCategory}"]`);
            if (!container) return;
            const cols = Array.from(container.querySelectorAll('input[data-column]'))
                .map(cb => storageKey(cb.dataset.column)).filter(Boolean);
            const allSelected = cols.length > 0 && cols.every(c => selectedColumns.some(sc => normaliseKey(sc) === normaliseKey(c)));
            if (allSelected) {
                cols.forEach(c => {
                    if (selectedColumns.length > 1) {
                        const normC = normaliseKey(c);
                        selectedColumns = selectedColumns.filter(sc => normaliseKey(sc) !== normC);
                    }
                });
            } else {
                cols.forEach(c => {
                    const normC = normaliseKey(c);
                    if (!selectedColumns.some(sc => normaliseKey(sc) === normC)) selectedColumns.push(c);
                });
            }
            page = 1;
            renderSelectedColumns();
            loadData();
            return;
        }

        const checkbox = event.target.closest('input[data-column]');
        if (checkbox) {
            updateColumnSelection(checkbox.dataset.column);
            return;
        }

        const studentCheckbox = event.target.closest('input[data-action="select-student"][data-ssid]');
        if (studentCheckbox) {
            const ssid = String(studentCheckbox.dataset.ssid || '').trim();
            if (!ssid) return;
            if (studentCheckbox.checked) {
                selectedStudentSsids.add(ssid);
            } else {
                selectedStudentSsids.delete(ssid);
            }
            renderSelectedStudentCount();
            syncSelectAllCheckboxState();
            return;
        }

        const selectAll = event.target.closest('input[data-action="select-all-visible"]');
        if (selectAll) {
            const pageSsids = getCurrentPageSsids();
            if (selectAll.checked) {
                pageSsids.forEach((ssid) => selectedStudentSsids.add(String(ssid)));
            } else {
                pageSsids.forEach((ssid) => selectedStudentSsids.delete(String(ssid)));
            }

            // Update row checkboxes to match
            document.querySelectorAll('#student-table input[data-action="select-student"][data-ssid]').forEach((el) => {
                const s = String(el.dataset.ssid || '').trim();
                el.checked = s ? selectedStudentSsids.has(s) : false;
            });
            renderSelectedStudentCount();
            syncSelectAllCheckboxState();
            return;
        }
    });

    // Drag & drop column reordering
    document.addEventListener('dragstart', (event) => {
        const th = event.target.closest('th[data-column]');
        if (!th) return;
        if (event.target.closest('[data-action="resize-handle"]')) {
            event.preventDefault();
            return;
        }
        draggedColumn = th.dataset.column;
        if (event.dataTransfer) {
            event.dataTransfer.setData('text/plain', draggedColumn);
            event.dataTransfer.effectAllowed = 'move';
        }
    });

    document.addEventListener('dragover', (event) => {
        const th = event.target.closest('th[data-column]');
        if (!th || !draggedColumn) return;
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });

    document.addEventListener('drop', (event) => {
        const th = event.target.closest('th[data-column]');
        if (!th || !draggedColumn) return;
        event.preventDefault();

        const targetCol = th.dataset.column;
        const fromIndex = selectedColumns.indexOf(draggedColumn);
        const toIndex = selectedColumns.indexOf(targetCol);
        if (fromIndex === -1 || toIndex === -1 || fromIndex === toIndex) {
            draggedColumn = null;
            return;
        }

        const next = selectedColumns.slice();
        next.splice(fromIndex, 1);
        next.splice(toIndex, 0, draggedColumn);
        selectedColumns = next;
        draggedColumn = null;

        // Re-render immediately without refetching
        renderSelectedColumns();
        if (lastPayload) {
            lastPayload.selected_columns = selectedColumns;
            renderTable(lastPayload);
        } else {
            loadData();
        }
    });

    // Column resizing
    const beginResize = (col, startX) => {
        const th = document.querySelector(`th[data-column="${cssEscape(col)}"]`);
        const currentWidth = th?.getBoundingClientRect().width;
        resizeState = {
            col,
            startX,
            startWidth: Number.isFinite(currentWidth) ? currentWidth : (columnWidths[col] || 120),
        };
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    };

    const applyResize = (clientX) => {
        if (!resizeState) return;
        const delta = clientX - resizeState.startX;
        const newWidth = Math.max(60, Math.round(resizeState.startWidth + delta));
        columnWidths[resizeState.col] = newWidth;

        const colEl = document.querySelector(`col[data-col="${cssEscape(resizeState.col)}"]`);
        if (colEl) colEl.style.width = `${newWidth}px`;
    };

    const endResize = () => {
        if (!resizeState) return;
        resizeState = null;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    };

    document.addEventListener('mousedown', (event) => {
        const handle = event.target.closest('[data-action="resize-handle"]');
        if (!handle) return;
        event.preventDefault();
        beginResize(handle.dataset.column, event.clientX);
    });

    window.addEventListener('mousemove', (event) => {
        if (!resizeState) return;
        applyResize(event.clientX);
    });

    window.addEventListener('mouseup', () => {
        endResize();
    });

    const debounce = (fn, wait) => {
        let timeout;
        return (...args) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => fn(...args), wait);
        };
    };

    const searchInput = document.getElementById('search');
    const searchIdListBadge = document.getElementById('search-id-list-badge');
    const searchIdListCount = document.getElementById('search-id-list-count');
    const searchIdListClear = document.getElementById('search-id-list-clear');

    const updateIdListBadge = () => {
        if (!searchIdListBadge) return;
        if (pastedIdList && pastedIdList.length) {
            searchIdListCount.textContent = `${pastedIdList.length} ID${pastedIdList.length === 1 ? '' : 's'} pasted`;
            searchIdListBadge.classList.remove('hidden');
        } else {
            searchIdListBadge.classList.add('hidden');
        }
    };

    const clearIdList = () => {
        pastedIdList = null;
        updateIdListBadge();
    };

    if (searchInput) {
        searchInput.addEventListener('paste', (e) => {
            const text = (e.clipboardData || window.clipboardData).getData('text');
            const tokens = text.split(/[\s,\t\r\n]+/).map(t => t.trim()).filter(Boolean);
            if (tokens.length < 2) return; // single term — let normal search handle it
            e.preventDefault();
            const MAX_IDS = 900;
            if (tokens.length > MAX_IDS) {
                alert(`Pasted list has ${tokens.length} IDs — only the first ${MAX_IDS} will be used (URL size limit).`);
                tokens.splice(MAX_IDS);
            }
            pastedIdList = tokens;
            searchInput.value = '';
            searchInput.placeholder = 'Clear list to search by name';
            updateIdListBadge();
            page = 1;
            loadData();
        });

        searchInput.addEventListener('input', debounce(() => {
            if (pastedIdList) clearIdList();
            page = 1;
            loadData();
        }, 400));
    }

    if (searchIdListClear) {
        searchIdListClear.addEventListener('click', () => {
            clearIdList();
            if (searchInput) {
                searchInput.value = '';
                searchInput.placeholder = 'Name, SSID, ID — or paste a list';
            }
            page = 1;
            loadData();
        });
    }

    const schoolYearSelect = document.getElementById('school_year');
    if (schoolYearSelect) {
        schoolYearSelect.addEventListener('change', () => {
            page = 1;
            loadData();
        });
    }

    const statusSelect = document.getElementById('status');
    if (statusSelect) {
        statusSelect.addEventListener('change', () => {
            page = 1;
            loadData();
        });
    }

    document.addEventListener('click', (event) => {
        // If resizing, ignore clicks
        if (resizeState) return;

        const clearSelectedBtn = event.target.closest('[data-action="clear-selected-students"]');
        if (clearSelectedBtn) {
            selectedStudentSsids.clear();
            renderSelectedStudentCount();
            syncSelectAllCheckboxState();
            if (lastPayload) renderTable(lastPayload);
            return;
        }

        const removeBtn = event.target.closest('[data-action="remove-column"]');
        if (removeBtn) {
            updateColumnSelection(removeBtn.dataset.column);
            return;
        }

        const sortBtn = event.target.closest('[data-action="sort"]');
        if (sortBtn) {
            const col = sortBtn.dataset.column;
            if (sortBy === col) {
                sortDir = sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                sortBy = col;
                sortDir = 'asc';
            }
            loadData();
            return;
        }

        const toggleFilterBtn = event.target.closest('[data-action="toggle-filter"]');
        if (toggleFilterBtn) {
            const col = toggleFilterBtn.dataset.column;
            const panel = document.querySelector(`[data-filter-panel="${col}"]`);
            if (panel) panel.classList.toggle('hidden');
            return;
        }

        const clearFilterBtn = event.target.closest('[data-action="clear-filter"]');
        if (clearFilterBtn) {
            const col = clearFilterBtn.dataset.column;
            delete filters[col];
            loadData();
            return;
        }

        const applyFilterBtn = event.target.closest('[data-action="apply-filter"]');
        if (applyFilterBtn) {
            const col = applyFilterBtn.dataset.column;
            const values = Array.from(document.querySelectorAll(`[data-filter-column="${col}"]:checked`))
                .map((input) => input.dataset.filterValue);
            if (values.length) {
                filters[col] = values;
            } else {
                delete filters[col];
            }
            loadData();
            return;
        }

        const applyRangeBtn = event.target.closest('[data-action="apply-range"]');
        if (applyRangeBtn) {
            const col = applyRangeBtn.dataset.column;
            const minInput = document.getElementById(`filter-min-${col}`);
            const maxInput = document.getElementById(`filter-max-${col}`);
            const min = minInput ? minInput.value : '';
            const max = maxInput ? maxInput.value : '';
            
            let val = '';
            if (min && max) {
                val = `${min}-${max}`;
            } else if (min) {
                val = `>=${min}`;
            } else if (max) {
                val = `<=${max}`;
            }

            if (val) {
                filters[col] = [val];
            } else {
                delete filters[col];
            }
            loadData();
            return;
        }

        const quickFilterBtn = event.target.closest('[data-action="quick-filter"]');
        if (quickFilterBtn) {
            const col = quickFilterBtn.dataset.column;
            const val = quickFilterBtn.dataset.value;
            if (val) {
                filters[col] = [val];
            }
            loadData();
            return;
        }

        const selectAllFilterBtn = event.target.closest('[data-action="select-all-filter-options"]');
        if (selectAllFilterBtn) {
            const col = selectAllFilterBtn.dataset.column;
            const inputs = document.querySelectorAll(`[data-filter-column="${col}"]`);
            inputs.forEach((input) => {
                input.checked = true;
            });
            return;
        }

        const clearAllFilterBtn = event.target.closest('[data-action="clear-all-filter-options"]');
        if (clearAllFilterBtn) {
            const col = clearAllFilterBtn.dataset.column;
            const inputs = document.querySelectorAll(`[data-filter-column="${col}"]`);
            inputs.forEach((input) => {
                input.checked = false;
            });
            return;
        }


        const toggleBtn = event.target.closest('[data-toggle-category]');
        if (toggleBtn) {
            toggleCategory(toggleBtn.dataset.toggleCategory);
            return;
        }

        const pageBtn = event.target.closest('[data-action$="page"]');
        if (pageBtn) {
            const nextPage = parseInt(pageBtn.dataset.page, 10);
            if (!Number.isNaN(nextPage)) {
                page = nextPage;
                loadData();
            }
            return;
        }

        const openSidebarBtn = event.target.closest('[data-action="toggle-column-selector"]');
        if (openSidebarBtn) {
            if (Date.now() - lastDragAt < 300) {
                return;
            }
            const isHidden = sidebarEl?.classList.contains('hidden');
            toggleSidebar(isHidden);
            return;
        }

        const closeSidebarBtn = event.target.closest('[data-action="close-column-selector"]');
        if (closeSidebarBtn) {
            toggleSidebar(false);
        }

        // New Actions
        const openFolderBrowserBtn = event.target.closest('[data-action="open-folder-browser"]');
        if (openFolderBrowserBtn) {
            const fbModal = document.getElementById('folder-browser-modal');
            if (fbModal) {
                fbModal.classList.remove('hidden');
                const content = document.getElementById('folder-browser-content');
                if (content && window.htmx) {
                    htmx.trigger(content, 'load-folders');
                }
            }
            return;
        }

        const saveQueryBtn = event.target.closest('[data-action="save-current-view"]');
        if (saveQueryBtn) {
            if (typeof window.openSaveQueryModal === 'function') {
                window.openSaveQueryModal();
            }
            return;
        }

        const saveListBtn = event.target.closest('[data-action="save-student-list"]');
        if (saveListBtn) {
            if (typeof window.openSaveListModal === 'function') {
                window.openSaveListModal();
            }
            return;
        }

        const exportBtn = event.target.closest('[data-action="export-csv"]');
        if (exportBtn) {
            showLoading('Preparing CSV export...');
            
            const baseUrl = appDataEl.dataset.exportCsvUrl;
            if (!baseUrl) {
                hideLoading();
                return;
            }

            const url = new URL(baseUrl, window.location.origin);
            const params = new URLSearchParams(window.location.search);
            const savedQueryId = params.get('saved_query');
            const studentListId = params.get('student_list');

            if (savedQueryId) url.searchParams.set('saved_query', savedQueryId);
            if (studentListId) url.searchParams.set('student_list', studentListId);

            if (Array.isArray(selectedColumns) && selectedColumns.length > 0) {
                url.searchParams.set('columns', JSON.stringify(selectedColumns));
            }
            if (sortBy) {
                url.searchParams.set('sort_by', sortBy);
                url.searchParams.set('sort_dir', sortDir);
            }
            if (filters && Object.keys(filters).length > 0) {
                url.searchParams.set('filters', JSON.stringify(filters));
            }

            const searchInput = document.getElementById('search');
            const schoolYearSelect = document.getElementById('school_year');
            const statusInput = document.getElementById('status');

            if (pastedIdList && pastedIdList.length) {
                url.searchParams.set('search_ids', JSON.stringify(pastedIdList));
            } else if (searchInput && searchInput.value) {
                url.searchParams.set('search', searchInput.value);
            }
            if (schoolYearSelect && schoolYearSelect.value) url.searchParams.set('school_year', schoolYearSelect.value);
            if (statusInput) url.searchParams.set('status', statusInput.value);

            window.location.href = url.toString();
            
            // Hide loading after a short delay (browser handles download)
            setTimeout(() => hideLoading(), 1000);
            return;
        }

        const applyStudentListBtn = event.target.closest('[data-action="apply-student-list"][data-student-list-id]');
        if (applyStudentListBtn) {
            event.preventDefault();

            const listId = String(applyStudentListBtn.dataset.studentListId || '').trim();
            if (!listId) return;

            // Lists define membership only; preserve current columns/filters UI state.
            // Clear saved_query since the list is being applied.
            const nextUrl = new URL(window.location.href);
            nextUrl.searchParams.set('student_list', listId);
            nextUrl.searchParams.delete('saved_query');
            nextUrl.searchParams.delete('page');
            window.history.replaceState({}, '', nextUrl.toString());

            const fbModal = document.getElementById('folder-browser-modal');
            if (fbModal) fbModal.classList.add('hidden');

            page = 1;
            loadData();
            return;
        }

        const clearLoadedBtn = event.target.closest('[data-action="clear-loaded"]');
        if (clearLoadedBtn) {
            event.preventDefault();
            const nextUrl = new URL(window.location.href);
            nextUrl.searchParams.delete('saved_query');
            nextUrl.searchParams.delete('student_list');
            nextUrl.searchParams.delete('page');
            window.history.replaceState({}, '', nextUrl.toString());
            try {
                selectedColumns = JSON.parse(appDataEl.dataset.defaultColumns || '[]');
            } catch (e) {
                selectedColumns = [];
            }
            filters = {};
            sortBy = '';
            sortDir = 'asc';

            const searchInput = document.getElementById('search');
            const schoolYearSelect = document.getElementById('school_year');
            const gradeSelect = document.getElementById('grade');
            const statusInput = document.getElementById('status');

            if (searchInput) searchInput.value = '';
            if (schoolYearSelect) schoolYearSelect.value = '';
            if (gradeSelect) gradeSelect.value = '';
            if (statusInput) statusInput.value = 'Active';

            renderSelectedColumns();
            page = 1;
            loadData();
            return;
        }
    });

    // Global functions for Folder Browser Actions
    window.showRenameModal = (id, type, name, description) => {
        const urlBase = appDataEl.dataset.renameModalUrl;
        if (!urlBase) return;
        const url = `${urlBase}?id=${id}&type=${type}`;
        const container = document.getElementById('modals-container');
        if (container) loadOverlayHtml(url, container);
    };

    window.showShareModal = (id, type, name) => {
        const urlBase = appDataEl.dataset.shareModalUrl;
        if (!urlBase) return;
        const url = `${urlBase}?id=${id}&type=${type}&name=${encodeURIComponent(name)}`;
        const container = document.getElementById('modals-container');
        if (container) loadOverlayHtml(url, container);
    };

    window.showCreateFolderModal = (parentId) => {
        const urlBase = appDataEl.dataset.createFolderModalUrl;
        if (!urlBase) return;
        let url = urlBase;
        if (parentId) url += `?parent_id=${parentId}`;
        const container = document.getElementById('modals-container');
        if (container) loadOverlayHtml(url, container);
    };

    // Expose modal openers so the folder browser can trigger overwrites.
    window.openSaveQueryModal = async ({ queryId = null, name = '', description = '' } = {}) => {
        const url = appDataEl.dataset.saveQueryModalUrl;
        const container = document.getElementById('modals-container');
        if (!url || !container) return;

        await loadOverlayHtml(url, container);

        const form = document.getElementById('save-query-form');
        const modal = document.getElementById('save-query-modal');
        if (!form || !modal) return;
        modal.classList.remove('hidden');

        // Ensure query_id exists
        let queryIdInput = form.querySelector('input[name="query_id"]');
        if (!queryIdInput) {
            queryIdInput = document.createElement('input');
            queryIdInput.type = 'hidden';
            queryIdInput.name = 'query_id';
            form.appendChild(queryIdInput);
        }
        queryIdInput.value = queryId ? String(queryId) : '';

        // Prefill fields (useful for overwrite/update)
        const nameInput = form.querySelector('input[name="name"]');
        const descInput = form.querySelector('textarea[name="description"]');
        if (nameInput && name) nameInput.value = name;
        if (descInput && typeof description === 'string') descInput.value = description;

        // Overwrite-by-selection dropdown (optional)
        const overwriteSelect = document.getElementById('overwrite_query_id');
        if (overwriteSelect && overwriteSelect.dataset.boundChange !== 'true') {
            overwriteSelect.dataset.boundChange = 'true';
            overwriteSelect.addEventListener('change', () => {
                const selectedId = String(overwriteSelect.value || '').trim();
                queryIdInput.value = selectedId;
                const opt = overwriteSelect.options[overwriteSelect.selectedIndex];
                if (!opt) return;
                const optName = opt.getAttribute('data-name') || '';
                const optDesc = opt.getAttribute('data-description') || '';
                if (selectedId) {
                    if (nameInput) nameInput.value = optName;
                    if (descInput) descInput.value = optDesc;
                }
            });
        }

        const configInput = form.querySelector('input[name="query_config"]');
        if (configInput) {
            configInput.value = JSON.stringify(getCurrentQueryConfigForSave());
        }

        // Bind submit handler once
        if (form.dataset.boundSubmit !== 'true') {
            form.dataset.boundSubmit = 'true';
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const formData = new FormData(form);

                // Always ensure we're saving current config
                formData.set('query_config', JSON.stringify(getCurrentQueryConfigForSave()));

                try {
                    const response = await fetch(form.action, {
                        method: 'POST',
                        body: formData,
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        credentials: 'same-origin'
                    });

                    if (response.status === 409) {
                        const data = await response.json();
                        if (data.status === 'confirm' && confirm(data.message)) {
                            formData.set('overwrite', 'true');
                            const retryResponse = await fetch(form.action, {
                                method: 'POST',
                                body: formData,
                                headers: {
                                    'X-Requested-With': 'XMLHttpRequest'
                                },
                                credentials: 'same-origin'
                            });
                            const html = await retryResponse.text();
                            container.innerHTML = html;
                            if (window.htmx) htmx.process(container);
                            if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                        }
                        return;
                    }

                    const html = await response.text();
                    container.innerHTML = html;
                    if (window.htmx) htmx.process(container);
                    if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                } catch (error) {
                    console.error('Error saving query:', error);
                    alert('Error saving query. Please try again.');
                }
            });
        }
    };

    window.openSaveListModal = async () => {
        const url = appDataEl.dataset.saveListModalUrl;
        const container = document.getElementById('modals-container');
        if (!url || !container) return;

        await loadOverlayHtml(url, container);
        const form = document.getElementById('save-list-form');
        const modal = document.getElementById('save-list-modal');
        if (!form || !modal) return;
        modal.classList.remove('hidden');

        const ssidsInput = form.querySelector('input[name="ssids"]');
        const countSpan = modal.querySelector('.student-count-badge');

        // Ensure query_id exists (lists are saved via the unified SavedQuery model)
        let queryIdInput = form.querySelector('input[name="query_id"]');
        if (!queryIdInput) {
            queryIdInput = document.createElement('input');
            queryIdInput.type = 'hidden';
            queryIdInput.name = 'query_id';
            form.appendChild(queryIdInput);
        }

        // Overwrite-by-selection dropdown (optional)
        const overwriteSelect = document.getElementById('overwrite_list_id');
        const listNameInput = form.querySelector('input[name="name"]');
        const listDescInput = form.querySelector('textarea[name="description"]');
        if (overwriteSelect && overwriteSelect.dataset.boundChange !== 'true') {
            overwriteSelect.dataset.boundChange = 'true';
            overwriteSelect.addEventListener('change', () => {
                const selectedId = String(overwriteSelect.value || '').trim();
                queryIdInput.value = selectedId;
                const opt = overwriteSelect.options[overwriteSelect.selectedIndex];
                if (!opt) return;
                const optName = opt.getAttribute('data-name') || '';
                const optDesc = opt.getAttribute('data-description') || '';
                if (selectedId) {
                    if (listNameInput) listNameInput.value = optName;
                    if (listDescInput) listDescInput.value = optDesc;
                }
            });
        }

        let ssids = Array.from(selectedStudentSsids);
        if (ssids.length === 0) {
            ssids = getCurrentPageSsids();
        }
        if (ssids.length === 0) {
            if (typeof window.showMessage === 'function') {
                window.showMessage('No students available to save. Try loading data or selecting rows first.', 'info');
            } else {
                alert('No students available to save. Try loading data or selecting rows first.');
            }
            return;
        }

        if (ssidsInput) ssidsInput.value = JSON.stringify(ssids);
        if (countSpan) countSpan.textContent = String(ssids.length);

        if (form.dataset.boundSubmit !== 'true') {
            form.dataset.boundSubmit = 'true';
            form.addEventListener('submit', async (e) => {
                e.preventDefault();
                const formData = new FormData(form);

                // Ensure we submit the latest selection
                let currentSsids = Array.from(selectedStudentSsids);
                if (currentSsids.length === 0) {
                    currentSsids = getCurrentPageSsids();
                }
                formData.set('ssids', JSON.stringify(currentSsids));

                try {
                    const response = await fetch(form.action, {
                        method: 'POST',
                        body: formData,
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest'
                        },
                        credentials: 'same-origin'
                    });

                    if (response.status === 409) {
                        const data = await response.json();
                        if (data.status === 'confirm' && confirm(data.message)) {
                            formData.set('overwrite', 'true');
                            const retryResponse = await fetch(form.action, {
                                method: 'POST',
                                body: formData,
                                headers: {
                                    'X-Requested-With': 'XMLHttpRequest'
                                },
                                credentials: 'same-origin'
                            });
                            const html = await retryResponse.text();
                            container.innerHTML = html;
                            if (window.htmx) htmx.process(container);
                            if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                        }
                        return;
                    }

                    const html = await response.text();
                    container.innerHTML = html;
                    if (window.htmx) htmx.process(container);
                    if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                } catch (error) {
                    console.error('Error saving list:', error);
                    alert('Error saving list. Please try again.');
                }
            });
        }
    };

    const initializeScrollSync = () => {
        const mainContainer = document.getElementById('table-scroll-container');
        const topScrollBar = document.getElementById('h-scroll-top');
        const topScrollContent = document.getElementById('h-scroll-content-top');
        const table = document.querySelector('#student-table table');
        
        if (!mainContainer || !topScrollBar || !topScrollContent || !table) return;
        
        // Set the width of the top scroll content to match table width
        const updateTopScrollWidth = () => {
            const tableWidth = table.offsetWidth;
            topScrollContent.style.width = `${tableWidth}px`;
        };
        
        updateTopScrollWidth();
        
        // Sync scroll positions
        let isTopScrolling = false;
        let isMainScrolling = false;
        
        topScrollBar.addEventListener('scroll', () => {
            if (isMainScrolling) return;
            isTopScrolling = true;
            mainContainer.scrollLeft = topScrollBar.scrollLeft;
            updateScrollIndicators();
            setTimeout(() => { isTopScrolling = false; }, 50);
        });
        
        mainContainer.addEventListener('scroll', () => {
            if (isTopScrolling) return;
            isMainScrolling = true;
            topScrollBar.scrollLeft = mainContainer.scrollLeft;
            updateScrollIndicators();
            setTimeout(() => { isMainScrolling = false; }, 50);
        });
        
        // Update scroll indicators
        const updateScrollIndicators = () => {
            const leftShadow = document.getElementById('scroll-shadow-left');
            const rightShadow = document.getElementById('scroll-shadow-right');
            
            if (!leftShadow || !rightShadow) return;
            
            const scrollLeft = mainContainer.scrollLeft;
            const maxScroll = mainContainer.scrollWidth - mainContainer.clientWidth;
            
            // Show left shadow if scrolled right
            leftShadow.style.opacity = scrollLeft > 10 ? '1' : '0';
            
            // Show right shadow if can scroll more right
            rightShadow.style.opacity = scrollLeft < maxScroll - 10 ? '1' : '0';
        };
        
        // Initial check
        updateScrollIndicators();
        
        // Update on window resize
        const resizeObserver = new ResizeObserver(() => {
            updateTopScrollWidth();
            updateScrollIndicators();
        });
        
        resizeObserver.observe(table);
    };

    renderSelectedColumns();
    renderSelectedStudentCount();
    restoreViewState();
    try {
        const sidebarOpen = sessionStorage.getItem('columnSidebarOpen') === 'true';
        if (sidebarOpen) {
            toggleSidebar(true);
        }
    } catch (e) {
        // ignore
    }
    updateYearModeButtons();
    loadData();

    // --- Folder browser helpers (the folder browser HTML is loaded via HTMX;
    // scripts inside the partial may not execute, so keep these globally available.)

    window.dwGetCookie = window.dwGetCookie || function (name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(';').shift();
        return null;
    };

    window.showMessage = window.showMessage || function (message, type = 'info') {
        const colors = {
            success: 'bg-green-100 text-green-700 border-green-300',
            error: 'bg-red-100 text-red-700 border-red-300',
            info: 'bg-blue-100 text-blue-700 border-blue-300'
        };

        const html = `
        <div class="fixed top-4 right-4 ${colors[type] || colors.info} border px-4 py-3 rounded-lg shadow-lg z-50 max-w-md" role="alert">
            <div class="flex items-center justify-between">
                <span>${String(message ?? '')}</span>
                <button type="button" onclick="this.parentElement.parentElement.remove()" class="ml-4">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                    </svg>
                </button>
            </div>
        </div>
        `;

        document.body.insertAdjacentHTML('beforeend', html);
        window.setTimeout(() => {
            const alert = document.querySelector('.fixed.top-4.right-4');
            if (alert) alert.remove();
        }, 5000);
    };

    window.updateQuery = window.updateQuery || function (queryId, queryName, queryDescription) {
        if (!confirm('Overwrite this saved query with your current view (columns + filters)?')) return;
        if (typeof window.openSaveQueryModal === 'function') {
            window.openSaveQueryModal({
                queryId,
                name: queryName,
                description: queryDescription
            });
            return;
        }
        window.showMessage('Save modal is not available on this page.', 'error');
    };

    window.showFiltersModal = window.showFiltersModal || async function (queryId) {
        try {
            const res = await fetch(`/dataexploration/api/query/${queryId}/details/`, { credentials: 'same-origin' });
            if (!res.ok) throw new Error(`Failed to load filters (${res.status})`);
            const data = await res.json();
            if (data && data.filters) {
                window.displayFilters?.(data.filters, data.query_name || 'Saved Query');
            } else {
                window.showMessage(data?.error || 'Failed to load filters', 'error');
            }
        } catch (e) {
            console.error('Error loading filters:', e);
            window.showMessage(e?.message || 'Error loading filters', 'error');
        }
    };

    window.displayFilters = window.displayFilters || function (filters, queryName) {
        let html = `
        <div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onclick="this.remove()">
            <div class="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] overflow-y-auto m-4" onclick="event.stopPropagation()">
                <div class="p-4 border-b border-gray-200 flex justify-between items-center">
                    <h3 class="text-lg font-semibold">Filters: ${String(queryName ?? '')}</h3>
                    <button type="button" onclick="this.closest('.fixed').remove()" class="text-gray-400 hover:text-gray-600">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                        </svg>
                    </button>
                </div>
                <div class="p-4 space-y-3">
        `;

        if (!filters || Object.keys(filters).length === 0) {
            html += '<p class="text-gray-500 italic">No filters applied</p>';
        } else {
            for (const [key, value] of Object.entries(filters)) {
                if (value && (Array.isArray(value) ? value.length > 0 : value !== '')) {
                    const displayValue = Array.isArray(value) ? value.join(', ') : value;
                    html += `
                    <div class="flex items-start gap-2 p-2 bg-gray-50 rounded">
                        <span class="font-medium text-gray-700 capitalize">${String(key).replace(/_/g, ' ')}:</span>
                        <span class="text-gray-600">${String(displayValue)}</span>
                    </div>
                    `;
                }
            }
        }

        html += `
                </div>
            </div>
        </div>
        `;
        document.body.insertAdjacentHTML('beforeend', html);
    };

    window.showRenameFolderModal = window.showRenameFolderModal || function (folderId, currentName, currentDescription) {
        const html = `
        <div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onclick="this.remove()">
            <div class="bg-white rounded-lg shadow-xl max-w-md w-full m-4" onclick="event.stopPropagation()">
                <div class="p-4 border-b border-gray-200">
                    <h3 class="text-lg font-semibold">Rename Folder</h3>
                </div>
                <div class="p-4 space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Folder Name</label>
                        <input type="text" id="folder-name-input" value="${String(currentName ?? '')}" 
                            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Description (Optional)</label>
                        <textarea id="folder-desc-input" rows="2"
                            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500">${String(currentDescription ?? '')}</textarea>
                    </div>
                </div>
                <div class="p-4 border-t border-gray-200 flex justify-end gap-2">
                    <button type="button" onclick="this.closest('.fixed').remove()" 
                        class="px-4 py-2 text-gray-700 bg-gray-100 rounded hover:bg-gray-200">Cancel</button>
                    <button type="button" onclick="window.renameFolder(${Number(folderId)})" 
                        class="px-4 py-2 text-white bg-blue-600 rounded hover:bg-blue-700">Save</button>
                </div>
            </div>
        </div>
        `;
        document.body.insertAdjacentHTML('beforeend', html);
    };

    window.renameFolder = window.renameFolder || async function (folderId) {
        try {
            const name = document.getElementById('folder-name-input')?.value || '';
            const description = document.getElementById('folder-desc-input')?.value || '';
            const csrf = getCsrfToken();

            const formData = new FormData();
            formData.append('name', name);
            formData.append('description', description);

            const res = await fetch(`/dataexploration/api/folder/${folderId}/rename/`, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrf || '' },
                body: formData,
                credentials: 'same-origin'
            });

            if (!res.ok) {
                let msg = 'Failed to rename folder';
                try {
                    const data = await res.json();
                    msg = data.message || data.error || msg;
                } catch (e) {
                    // ignore
                }
                throw new Error(msg);
            }

            document.querySelector('.fixed')?.remove();
            if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
            window.showMessage('Folder renamed successfully', 'success');
        } catch (e) {
            console.error('Error renaming folder:', e);
            window.showMessage(e?.message || 'Error renaming folder', 'error');
        }
    };

    window.deleteFolder = window.deleteFolder || async function (folderId, folderName) {
        if (!confirm(`Are you sure you want to delete the folder "${folderName}"? This will fail if the folder contains items.`)) return;
        try {
            const csrf = getCsrfToken();
            const res = await fetch(`/dataexploration/api/folder/${folderId}/delete/`, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrf || '' },
                credentials: 'same-origin'
            });

            if (res.ok) {
                if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                window.showMessage('Folder deleted successfully', 'success');
                return;
            }

            let msg = 'Failed to delete folder';
            try {
                const data = await res.json();
                msg = data.message || data.error || msg;
            } catch (e) {
                // ignore
            }
            throw new Error(msg);
        } catch (e) {
            console.error('Error deleting folder:', e);
            window.showMessage(e?.message || 'Error deleting folder', 'error');
        }
    };
});

/**
 * Toggle Save/Load Panel
 */
function toggleSaveLoadPanel() {
    const panel = document.getElementById('save-load-panel');
    const chevron = document.getElementById('panel-chevron');

    if (panel.classList.contains('hidden')) {
        panel.classList.remove('hidden');
        panel.classList.add('animate-slideDown');
        chevron.style.transform = 'rotate(180deg)';
        // Store preference
        localStorage.setItem('saveLoadPanelOpen', 'true');
    } else {
        panel.classList.add('hidden');
        panel.classList.remove('animate-slideDown');
        chevron.style.transform = 'rotate(0deg)';
        // Store preference
        localStorage.setItem('saveLoadPanelOpen', 'false');
    }
}

// Restore panel state on load
document.addEventListener('DOMContentLoaded', () => {
    const panelOpen = localStorage.getItem('saveLoadPanelOpen');
    if (panelOpen === 'true') {
        const panel = document.getElementById('save-load-panel');
        const chevron = document.getElementById('panel-chevron');
        if (panel && chevron) {
            panel.classList.remove('hidden');
            chevron.style.transform = 'rotate(180deg)';
        }
    }
});
