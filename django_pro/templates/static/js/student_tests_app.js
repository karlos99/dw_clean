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

        if (payload.sort_by) {
            sortBy = payload.sort_by;
        }
        if (payload.sort_dir) {
            sortDir = payload.sort_dir;
        }

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
        columns.forEach((col) => {
            const width = columnWidths[col];
            const widthStyle = Number.isFinite(width) ? ` style="width:${Math.max(60, width)}px"` : '';
            html += `<col data-col="${escapeHtml(col)}"${widthStyle}>`;
        });
        html += '</colgroup>';

        html += '<thead class="bg-slate-50 sticky top-0 z-10"><tr class="bg-slate-50">';
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
            html += `<tr><td colspan="${columns.length}" class="px-6 py-8 text-center text-slate-500">No students found</td></tr>`;
        } else {
            students.forEach((student) => {
                html += '<tr class="hover:bg-slate-50">';
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
        url.searchParams.set('format', 'json');

        const searchInput = document.getElementById('search');
        const schoolYearSelect = document.getElementById('school_year');
        const statusInput = document.getElementById('status');

        if (searchInput && searchInput.value) url.searchParams.set('search', searchInput.value);
        if (schoolYearSelect && schoolYearSelect.value) url.searchParams.set('school_year', schoolYearSelect.value);
        if (statusInput && statusInput.value) url.searchParams.set('status', statusInput.value);

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
            const fbSidebar = document.getElementById('folder-browser-sidebar');
            if (fbSidebar) {
                fbSidebar.classList.remove('translate-x-full');
                const content = document.getElementById('folder-browser-content');
                if (content && window.htmx) {
                    htmx.trigger(content, 'load-folders');
                }
            }
            return;
        }

        const saveQueryBtn = event.target.closest('[data-action="save-current-view"]');
        if (saveQueryBtn) {
            const url = appDataEl.dataset.saveQueryModalUrl;
            const container = document.getElementById('modals-container');
            if (url && container) {
                loadOverlayHtml(url, container).then(() => {
                    const form = document.getElementById('save-query-form');
                    const modal = document.getElementById('save-query-modal');
                    if (form && modal) {
                        modal.classList.remove('hidden');
                        const configInput = form.querySelector('input[name="query_config"]');
                        if (configInput) {
                            const currentFilters = {
                                school_year: document.getElementById('school_year')?.value,
                                status: document.getElementById('status')?.value,
                                search: document.getElementById('search')?.value,
                                ...filters
                            };
                            const config = {
                                columns: selectedColumns,
                                filters: currentFilters
                            };
                            configInput.value = JSON.stringify(config);
                        }

                        // Add custom submit handler for overwrite confirmation
                        form.addEventListener('submit', async (e) => {
                            e.preventDefault();
                            const formData = new FormData(form);

                            try {
                                const response = await fetch(form.action, {
                                    method: 'POST',
                                    body: formData,
                                    headers: {
                                        'X-Requested-With': 'XMLHttpRequest'
                                    }
                                });

                                if (response.status === 409) {
                                    const data = await response.json();
                                    if (data.status === 'confirm' && confirm(data.message)) {
                                        formData.append('overwrite', 'true');
                                        const retryResponse = await fetch(form.action, {
                                            method: 'POST',
                                            body: formData,
                                            headers: {
                                                'X-Requested-With': 'XMLHttpRequest'
                                            }
                                        });
                                        const html = await retryResponse.text();
                                        container.innerHTML = html;
                                        htmx.process(container);
                                        if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                                    }
                                } else {
                                    const html = await response.text();
                                    container.innerHTML = html;
                                    htmx.process(container);
                                    if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                                }
                            } catch (error) {
                                console.error('Error saving query:', error);
                                alert('Error saving query. Please try again.');
                            }
                        });
                    }
                });
            }
            return;
        }

        const saveListBtn = event.target.closest('[data-action="save-student-list"]');
        if (saveListBtn) {
            const url = appDataEl.dataset.saveListModalUrl;
            const container = document.getElementById('modals-container');
            if (url && container) {
                loadOverlayHtml(url, container).then(() => {
                    const form = document.getElementById('save-list-form');
                    const modal = document.getElementById('save-list-modal');
                    if (form && modal) {
                        modal.classList.remove('hidden');
                        const ssidsInput = form.querySelector('input[name="ssids"]');
                        if (ssidsInput && lastPayload && lastPayload.students) {
                            const ssids = lastPayload.students.map(s => s.ssid).filter(Boolean);
                            ssidsInput.value = JSON.stringify(ssids);
                            // Also update badge count
                            const countSpan = modal.querySelector('.student-count-badge');
                            if (countSpan) countSpan.textContent = ssids.length;
                        }

                        // Add custom submit handler for overwrite confirmation
                        form.addEventListener('submit', async (e) => {
                            e.preventDefault();
                            const formData = new FormData(form);

                            try {
                                const response = await fetch(form.action, {
                                    method: 'POST',
                                    body: formData,
                                    headers: {
                                        'X-Requested-With': 'XMLHttpRequest'
                                    }
                                });

                                if (response.status === 409) {
                                    const data = await response.json();
                                    if (data.status === 'confirm' && confirm(data.message)) {
                                        formData.append('overwrite', 'true');
                                        const retryResponse = await fetch(form.action, {
                                            method: 'POST',
                                            body: formData,
                                            headers: {
                                                'X-Requested-With': 'XMLHttpRequest'
                                            }
                                        });
                                        const html = await retryResponse.text();
                                        container.innerHTML = html;
                                        htmx.process(container);
                                        if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                                    }
                                } else {
                                    const html = await response.text();
                                    container.innerHTML = html;
                                    htmx.process(container);
                                    if (window.htmx) htmx.trigger(document.body, 'refresh-folders');
                                }
                            } catch (error) {
                                console.error('Error saving list:', error);
                                alert('Error saving list. Please try again.');
                            }
                        });
                    }
                });
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

    renderSelectedColumns();
    loadData();
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
