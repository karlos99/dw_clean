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

    let selectedColumns = [];
    let columnDefinitions = {};
    let page = 1;
    let sortBy = '';
    let sortDir = 'asc';
    let filters = {};
    let lastPayload = null;
    let columnWidths = {};
    let draggedColumn = null;
    let resizeState = null;

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

    const getCurrentQueryConfigForSave = () => {
        return {
            columns: selectedColumns,
            filters: getCurrentFiltersForSave(),
            sort_by: sortBy,
            sort_dir: sortDir,
        };
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
            checkbox.checked = selectedColumns.includes(checkbox.dataset.column);
        });
    };

    const renderSelectedColumns = () => {
        if (selectedCountEl) selectedCountEl.textContent = selectedColumns.length;
        if (selectedCountToolbarEl) selectedCountToolbarEl.textContent = `${selectedColumns.length} selected`;
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
        sidebarEl.classList.toggle('hidden', !show);
    };

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

        html += '<thead class="bg-slate-50 sticky top-0 z-10"><tr class="bg-slate-50">';

        // Selection column header
        html += '<th class="px-4 py-2 text-left text-xs font-semibold text-slate-700 uppercase bg-slate-50">';
        html += '<input type="checkbox" data-action="select-all-visible" class="rounded border-slate-300 text-blue-600 focus:ring-blue-600" aria-label="Select all visible students">';
        html += '</th>';

        columns.forEach((col) => {
            const label = columnDefinitions[col] || col;
            const optionsForCol = Array.isArray(filterOptions[col]) ? filterOptions[col] : [];
            const hasActiveFilter = Array.isArray(filters[col]) && filters[col].length > 0;
            const hasFilter = optionsForCol.length > 0 || hasActiveFilter;
            const isSorted = sortBy === col;
            const sortIcon = isSorted ? (sortDir === 'asc' ? '▲' : '▼') : '↕';
            html += `<th draggable="true" data-column="${escapeHtml(col)}" class="relative group px-4 py-2 text-left text-xs font-semibold text-slate-700 uppercase bg-slate-50">`;
            html += `<div class="flex items-center justify-between gap-2">`;
            html += `<div class="flex items-center gap-2 min-w-0">`;
            html += `<button draggable="false" type="button" data-action="sort" data-column="${escapeHtml(col)}" class="flex-1 text-left hover:text-slate-900 whitespace-pre-wrap break-words">${escapeHtml(label)}</button>`;
            html += `<button draggable="false" type="button" data-action="sort" data-column="${escapeHtml(col)}" class="text-xs text-slate-500 hover:text-slate-700 flex-shrink-0">${sortIcon}</button>`;
            html += `</div>`;
            if (hasFilter) {
                html += `<div class="relative">`;
                html += `<button draggable="false" type="button" data-action="toggle-filter" data-column="${escapeHtml(col)}" class="text-xs text-slate-500 hover:text-slate-700">▾</button>`;
                html += `<div class="hidden absolute right-0 mt-2 w-56 bg-white rounded-md shadow-lg border border-slate-200 py-1 z-20" data-filter-panel="${escapeHtml(col)}">`;
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
            html += `<button draggable="false" type="button" data-action="remove-column" data-column="${escapeHtml(col)}" class="opacity-0 group-hover:opacity-100 transition-opacity text-slate-400 hover:text-red-600 hover:bg-red-50 rounded p-0.5" title="Remove column">✕</button>`;
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
    };

    document.addEventListener('click', async (event) => {
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
        const url = new URL(loadStudentsUrl, window.location.origin);
        url.searchParams.set('page', page);

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
            if (searchInput && searchInput.value) url.searchParams.set('search', searchInput.value);
            if (schoolYearSelect && schoolYearSelect.value) url.searchParams.set('school_year', schoolYearSelect.value);
            if (gradeSelect && gradeSelect.value) url.searchParams.set('grade', gradeSelect.value);
            if (statusInput && statusInput.value) url.searchParams.set('status', statusInput.value);
        }

        fetch(url.toString(), { credentials: 'same-origin' })
            .then((response) => response.json())
            .then((payload) => {
                renderTable(payload);
            })
            .catch((error) => {
                console.error('Error loading data:', error);
                const targetEl = document.querySelector('#student-table');
                if (targetEl) {
                    targetEl.innerHTML = '<div class="text-center py-8 text-red-600">Failed to load data.</div>';
                }
            });
    };

    const updateColumnSelection = (col) => {
        if (selectedColumns.includes(col)) {
            if (selectedColumns.length <= 1) return;
            selectedColumns = selectedColumns.filter((c) => c !== col);
            if (filters[col]) {
                delete filters[col];
            }
        } else {
            selectedColumns.push(col);
        }
        page = 1;
        renderSelectedColumns();
        loadData();
    };

    document.addEventListener('change', (event) => {
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
    if (searchInput) {
        searchInput.addEventListener('input', debounce(() => {
            page = 1;
            loadData();
        }, 400));
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
            const baseUrl = appDataEl.dataset.exportCsvUrl;
            if (!baseUrl) return;

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

            if (searchInput && searchInput.value) url.searchParams.set('search', searchInput.value);
            if (schoolYearSelect && schoolYearSelect.value) url.searchParams.set('school_year', schoolYearSelect.value);
            if (statusInput && statusInput.value) url.searchParams.set('status', statusInput.value);

            window.location.href = url.toString();
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

    renderSelectedColumns();
    renderSelectedStudentCount();
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
