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
    const selectedListEl = document.getElementById('selected-columns-list');
    const sidebarEl = document.getElementById('column-selector');

    const syncCheckboxes = () => {
        document.querySelectorAll('input[data-column]').forEach((checkbox) => {
            checkbox.checked = selectedColumns.includes(checkbox.dataset.column);
        });
    };

    const renderSelectedColumns = () => {
        if (selectedCountEl) selectedCountEl.textContent = selectedColumns.length;
        if (selectedCountToolbarEl) selectedCountToolbarEl.textContent = `${selectedColumns.length} selected`;

        if (!selectedListEl) return;
        selectedListEl.innerHTML = '';

        selectedColumns.forEach((col) => {
            const row = document.createElement('div');
            row.className = 'flex items-center justify-between p-2 bg-blue-50 rounded text-sm';

            const label = document.createElement('span');
            label.className = 'text-slate-700 text-xs';
            label.textContent = columnDefinitions[col] || col;

            const removeBtn = document.createElement('button');
            removeBtn.className = 'text-slate-400 hover:text-red-600 text-xs';
            removeBtn.setAttribute('data-action', 'remove-column');
            removeBtn.setAttribute('data-column', col);
            removeBtn.textContent = '✕';

            row.appendChild(label);
            row.appendChild(removeBtn);
            selectedListEl.appendChild(row);
        });

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

    const renderTable = (payload) => {
        const targetEl = document.querySelector('#student-table');
        if (!targetEl) return;

        if (payload.column_definitions && Object.keys(columnDefinitions).length === 0) {
            columnDefinitions = payload.column_definitions;
        }

        const columns = payload.selected_columns || [];
        const students = payload.students || [];
        const total = payload.total || 0;
        const pageCount = payload.total_pages || 1;
        const currentPage = payload.page || 1;

        let html = '<div class="overflow-x-auto">';
        html += '<table class="min-w-full divide-y divide-slate-200 border">';
        html += '<thead class="bg-slate-50"><tr>';
        columns.forEach((col) => {
            const label = columnDefinitions[col] || col;
            html += `<th draggable="true" data-column="${escapeHtml(col)}" class="px-4 py-3 text-left text-xs font-medium text-slate-700 uppercase tracking-wider cursor-move hover:bg-slate-100 border-r">${escapeHtml(label)}</th>`;
        });
        html += '</tr></thead><tbody class="bg-white divide-y divide-slate-100">';

        if (students.length === 0) {
            html += `<tr><td colspan="${columns.length}" class="px-6 py-8 text-center text-slate-500">No students found</td></tr>`;
        } else {
            students.forEach((student) => {
                html += '<tr class="hover:bg-slate-50">';
                columns.forEach((col) => {
                    const value = student[col] ?? '—';
                    html += `<td class="px-4 py-2 text-sm text-slate-900 border-r whitespace-nowrap">${escapeHtml(value)}</td>`;
                });
                html += '</tr>';
            });
        }

        html += '</tbody></table></div>';

        if (pageCount > 1) {
            html += '<div class="px-4 py-3 border-t flex items-center justify-between">';
            html += `<div class="text-sm text-slate-700">Showing ${students.length} of ${total} students</div>`;
            html += '<div class="flex gap-2">';
            if (currentPage > 1) {
                html += `<button data-action="prev-page" data-page="${currentPage - 1}" class="px-3 py-1 text-sm border rounded hover:bg-slate-50">Previous</button>`;
            }
            html += `<span class="px-3 py-1 text-sm">Page ${currentPage} of ${pageCount}</span>`;
            if (currentPage < pageCount) {
                html += `<button data-action="next-page" data-page="${currentPage + 1}" class="px-3 py-1 text-sm border rounded hover:bg-slate-50">Next</button>`;
            }
            html += '</div></div>';
        }

        targetEl.innerHTML = html;
    };

    const loadData = () => {
        const url = new URL(loadStudentsUrl, window.location.origin);
        url.searchParams.set('page', page);
        url.searchParams.set('columns', JSON.stringify(selectedColumns));
        url.searchParams.set('format', 'json');

        const searchInput = document.getElementById('search');
        const schoolYearSelect = document.getElementById('school_year');
        const gradeSelect = document.getElementById('grade');

        if (searchInput && searchInput.value) url.searchParams.set('search', searchInput.value);
        if (schoolYearSelect && schoolYearSelect.value) url.searchParams.set('school_year', schoolYearSelect.value);
        if (gradeSelect && gradeSelect.value) url.searchParams.set('grade', gradeSelect.value);

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
            selectedColumns = selectedColumns.filter((c) => c !== col);
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

    document.addEventListener('click', (event) => {
        const removeBtn = event.target.closest('[data-action="remove-column"]');
        if (removeBtn) {
            updateColumnSelection(removeBtn.dataset.column);
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
    });

    renderSelectedColumns();
    loadData();
});
