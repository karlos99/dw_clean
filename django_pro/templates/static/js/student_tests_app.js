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
    let nextCursor = '';
    let pendingCursor = '';
    let cursorStack = [];
    let loadAbort = null;
    let inFlightUrl = '';
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
    const studentPictureUrlTemplate = appDataEl.dataset.studentPictureUrlTemplate || '';
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
    const buildStudentPictureUrl = (ssid) => {
        if (!studentPictureUrlTemplate || !ssid) return '';
        return studentPictureUrlTemplate.replace('SSID_PLACEHOLDER', encodeURIComponent(String(ssid)));
    };
    const isPictureCol = (col) => String(col || '') === 'picture';
    const pinPictureFirst = (cols) => {
        if (!Array.isArray(cols) || cols.length < 2) return cols;
        const i = cols.findIndex(isPictureCol);
        if (i <= 0) return cols;
        const next = cols.slice();
        const [pic] = next.splice(i, 1);
        next.unshift(pic);
        return next;
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

    let loadingStartedAt = 0;
    let loadingHideTimer = null;

    const showLoading = (message = 'Loading data...') => {
        const loadingBar = document.getElementById('loading-bar');
        const loadingBarProgress = document.getElementById('loading-bar-progress');
        loadingStartedAt = Date.now();
        if (loadingHideTimer) {
            clearTimeout(loadingHideTimer);
            loadingHideTimer = null;
        }
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
        const elapsed = Date.now() - loadingStartedAt;
        const hideDelay = Math.max(0, 180 - elapsed);
        if (loadingHideTimer) {
            clearTimeout(loadingHideTimer);
        }
        loadingHideTimer = setTimeout(() => {
            if (loadingBar) {
                loadingBar.classList.add('hidden');
                loadingBar.setAttribute('aria-hidden', 'true');
            }
            if (loadingBarProgress) {
                loadingBarProgress.style.width = '0%';
            }
            loadingHideTimer = null;
        }, hideDelay);
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

    const scheduleFiltersEligible = appDataEl.dataset.scheduleFiltersEligible === '1';
    let scheduleFilterBarOpen = false;
    const scheduleFilterShowStaff = appDataEl.dataset.scheduleFilterShowStaff === '1';
    let scheduleFilterUiMode = appDataEl.dataset.scheduleFilterMode || 'district_full';
    const scheduleFilterOptionsUrl = appDataEl.dataset.scheduleFilterOptionsUrl || '';
    const projectDefaultSchoolYear = appDataEl.dataset.projectDefaultSchoolYear || '';
    const scheduleDistrictRequiresSchool = appDataEl.dataset.scheduleDistrictRequiresSchool === '1';

    const SCHEDULE_MS_KEYS = ['schedule_period', 'schedule_subject', 'schedule_course_name', 'schedule_staff'];
    const SCHEDULE_MS_PLACEHOLDERS = {
        schedule_period: 'All periods',
        schedule_subject: 'All subjects',
        schedule_course_name: 'All courses',
        schedule_staff: 'All staff',
    };

    const getScheduleFilterRoot = (key) => document.querySelector(`[data-schedule-filter="${key}"]`);

    const getScheduleSelectValues = (key) => {
        const root = getScheduleFilterRoot(key);
        if (!root) return [];
        return [...root.querySelectorAll('[data-schedule-ms-value]:checked')].map((cb) => cb.value);
    };

    const syncScheduleMsLabel = (key) => {
        const root = getScheduleFilterRoot(key);
        if (!root) return;
        const labelEl = root.querySelector('[data-schedule-ms-label]');
        const placeholder = SCHEDULE_MS_PLACEHOLDERS[key] || 'All';
        const checked = root.querySelectorAll('[data-schedule-ms-value]:checked');
        if (!labelEl) return;
        if (!checked.length) {
            labelEl.textContent = placeholder;
            labelEl.classList.add('text-slate-400');
            labelEl.classList.remove('text-slate-700');
            return;
        }
        const text = [...checked].map((cb) => {
            const lab = cb.closest('label')?.querySelector('span')?.textContent;
            return lab || cb.value;
        }).join(', ');
        labelEl.textContent = text.length > 48 ? `${checked.length} selected` : text;
        labelEl.classList.remove('text-slate-400');
        labelEl.classList.add('text-slate-700');
    };

    const setScheduleSelectValues = (key, values) => {
        const root = getScheduleFilterRoot(key);
        if (!root) return;
        const list = Array.isArray(values)
            ? values
            : (values ? String(values).split(',') : []);
        const selected = new Set(list.map((v) => String(v).trim()).filter(Boolean));
        root.querySelectorAll('[data-schedule-ms-value]').forEach((cb) => {
            cb.checked = selected.has(cb.value);
        });
        syncScheduleMsLabel(key);
    };

    const clearScheduleSelect = (key) => setScheduleSelectValues(key, []);

    const fillScheduleMultiSelect = (key, items, selectedValue, { valueKey = null, labelKey = null } = {}) => {
        const root = getScheduleFilterRoot(key);
        const panel = root?.querySelector('[data-schedule-ms-panel]');
        if (!panel) return;
        const prev = (selectedValue !== undefined && selectedValue !== null && selectedValue !== '')
            ? (Array.isArray(selectedValue) ? selectedValue : String(selectedValue).split(','))
            : getScheduleSelectValues(key);
        const selected = new Set(prev.map((v) => String(v).trim()).filter(Boolean));
        panel.innerHTML = (items || []).map((item) => {
            const val = (item && typeof item === 'object')
                ? String(item[valueKey || 'id'] || '')
                : String(item);
            const label = (item && typeof item === 'object')
                ? String(item[labelKey || 'name'] || val)
                : String(item);
            const checked = selected.has(val) ? ' checked' : '';
            return `<label class="flex items-center gap-2 px-1 py-1 hover:bg-slate-50 rounded cursor-pointer text-sm text-slate-700">
                <input type="checkbox" data-schedule-ms-value class="rounded border-slate-300 text-blue-600 focus:ring-blue-500" value="${escapeHtml(val)}"${checked}>
                <span class="truncate">${escapeHtml(label)}</span>
            </label>`;
        }).join('');
        syncScheduleMsLabel(key);
    };

    let scheduleMsDropdownInit = false;
    const initScheduleMultiSelectDropdowns = () => {
        if (scheduleMsDropdownInit) return;
        const bar = document.getElementById('schedule-filters-bar');
        if (!bar) return;
        scheduleMsDropdownInit = true;
        bar.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-schedule-ms-btn]');
            if (!btn) return;
            e.stopPropagation();
            const panel = btn.closest('[data-schedule-filter]')?.querySelector('[data-schedule-ms-panel]');
            if (!panel) return;
            bar.querySelectorAll('[data-schedule-ms-panel]').forEach((p) => {
                if (p !== panel) p.classList.add('hidden');
            });
            panel.classList.toggle('hidden');
        });
        document.addEventListener('click', (e) => {
            if (e.target.closest('#schedule-filters-bar')) return;
            bar.querySelectorAll('[data-schedule-ms-panel]').forEach((p) => p.classList.add('hidden'));
        });
    };

    const scheduleFilterShowsPeriod = () => (
        scheduleFilterUiMode === 'district_full'
        || scheduleFilterUiMode === 'teacher_schedule'
        || scheduleFilterUiMode === 'school_marks'
    );
    const scheduleFilterShowsSubject = () => (
        scheduleFilterUiMode === 'district_full'
        || scheduleFilterUiMode === 'school_marks'
        || scheduleFilterUiMode === 'school_elementary'
    );
    const scheduleFilterShowsStaff = () => (
        scheduleFilterShowStaff
        && scheduleFilterUiMode !== 'teacher_schedule'
        && scheduleFilterUiMode !== 'teacher_course'
    );
    const scheduleMarksCascade = () => (
        scheduleFilterUiMode === 'district_full'
        || scheduleFilterUiMode === 'school_marks'
        || scheduleFilterUiMode === 'school_elementary'
    );

    const syncScheduleFilterModeVisibility = () => {
        const periodField = document.getElementById('schedule-field-period');
        const subjectField = document.getElementById('schedule-field-subject');
        const staffField = document.getElementById('schedule-field-staff');
        if (periodField) periodField.classList.toggle('hidden', !scheduleFilterShowsPeriod());
        if (subjectField) subjectField.classList.toggle('hidden', !scheduleFilterShowsSubject());
        if (staffField) staffField.classList.toggle('hidden', !scheduleFilterShowsStaff());
    };

    const hasSingleSchoolColumnFilter = () => {
        for (const key of ['school', 'school_state_id']) {
            const vals = filters[key];
            if (!Array.isArray(vals)) continue;
            const cleaned = vals.filter((v) => v !== '' && v !== null && v !== undefined && v !== 'null' && v !== 'None');
            if (cleaned.length === 1) return true;
        }
        return false;
    };

    const isSelectedProjectSchoolYear = () => {
        if (!projectDefaultSchoolYear) return false;
        const raw = document.getElementById('school_year')?.value || '';
        const resolved = resolveRelativeSchoolYear(raw);
        return resolved === projectDefaultSchoolYear;
    };

    const canShowScheduleFilterToggle = () => {
        if (!scheduleFiltersEligible) return false;
        if (!isSelectedProjectSchoolYear()) return false;
        if (scheduleDistrictRequiresSchool && !hasSingleSchoolColumnFilter()) return false;
        return true;
    };

    const isScheduleFilterBarActive = () => (
        canShowScheduleFilterToggle() && scheduleFilterBarOpen
    );

    const syncScheduleFilterBarDisplay = () => {
        const bar = document.getElementById('schedule-filters-bar');
        const toggleBtn = document.getElementById('schedule-filters-toggle');
        if (bar) bar.classList.toggle('hidden', !scheduleFilterBarOpen);
        if (toggleBtn) {
            toggleBtn.textContent = scheduleFilterBarOpen
                ? 'Hide schedule filters'
                : 'Show schedule filters';
            toggleBtn.setAttribute('aria-expanded', scheduleFilterBarOpen ? 'true' : 'false');
        }
    };

    const updateScheduleFilterChrome = () => {
        const toggleWrap = document.getElementById('schedule-filters-toggle-wrap');
        const showToggle = canShowScheduleFilterToggle();
        if (toggleWrap) toggleWrap.classList.toggle('hidden', !showToggle);
        if (!showToggle) {
            scheduleFilterBarOpen = false;
            clearScheduleFilterControls();
        }
        syncScheduleFilterBarDisplay();
    };

    const clearScheduleFilterControls = () => {
        SCHEDULE_MS_KEYS.forEach((key) => clearScheduleSelect(key));
        syncScheduleCourseStaffDisabled();
    };

    // --- Year Mode Helpers ---
    const aliasLabelMap = {
        '__sy_this__': 'This School Year',
        '__sy_prev1__': 'Last School Year',
        '__sy_prev2__': 'Two Years Prior',
        '__sy_prev3__': 'Three Years Prior',
        '__sy_prev4__': 'Four Years Prior',
        '__sy_prev5__': 'Five Years Prior'
    };

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

    /** Resolve relative school year alias to its specific concrete year. */
    function resolveRelativeSchoolYear(val) {
        if (!val) return '';
        if (val.startsWith('__sy_')) {
            for (const [yearUs, alias] of Object.entries(yearAliasMap)) {
                if (alias === val) {
                    return yearUs.replace(/_/g, '-');
                }
            }
        }
        return val;
    }

    /** Get the display label for a column, dynamically translating to relative if active. */
    function getColumnLabel(col) {
        if (!col) return '';
        let label = columnDefinitions[col] || columnDefinitions[normaliseKey(col)] || col;
        if (yearMode === 'relative') {
            for (const [yearUs, alias] of Object.entries(yearAliasMap)) {
                const hyphenatedYear = yearUs.replace('_', '-');
                const relLabel = aliasLabelMap[alias];
                if (relLabel) {
                    if (label.includes(`(${hyphenatedYear})`)) {
                        label = label.replace(`(${hyphenatedYear})`, `(${relLabel})`);
                    } else if (label.includes(hyphenatedYear)) {
                        label = label.replace(hyphenatedYear, relLabel);
                    }
                }
            }
        }
        return label;
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
        // Update year header label texts
        document.querySelectorAll('[data-year-header-label]').forEach(el => {
            const specific = el.dataset.specificLabel || '';
            const relative = el.dataset.relativeLabel || '';
            el.textContent = yearMode === 'relative' ? relative : specific;
        });

        // Filter the school_year dropdown's optgroups to always show the specific school year instead of relative names
        const schoolYearSelect = document.getElementById('school_year');
        if (schoolYearSelect) {
            const optGroups = schoolYearSelect.querySelectorAll('optgroup[data-school-year-group]');
            optGroups.forEach(group => {
                const groupMode = group.dataset.schoolYearGroup;
                if (groupMode === 'specific') {
                    group.style.display = '';
                    group.disabled = false;
                } else {
                    group.style.display = 'none';
                    group.disabled = true;
                }
            });
        }
    }

    function setYearMode(mode) {
        yearMode = mode;
        localStorage.setItem('dwYearMode', mode);
        // Convert selectedColumns to new mode
        selectedColumns = selectedColumns.map(col => storageKey(col));
        updateYearModeButtons();
        syncCheckboxes();
    }

    // ─── "All Marks" course picker ────────────────────────────────────────
    // Selecting the All Marks umbrella adds one course/marks wide pair per course
    // the student has in that subject. The course picker below the umbrella lets
    // the user uncheck courses they don't want included; deselected courses are
    // stored on the umbrella key as 'exclude:<course>' values in the filters map.
    const normMarkallToken = (value) => String(value || '').replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
    // Homeroom courses are never part of the Elementary subject's All Marks view.
    const isMarkallHiddenCourse = (subject, name) => String(subject || '') === 'Elementary'
        && /Homeroom/i.test(String(name || ''));
    const markallUmbrellaKey = (year, subject) => {
        const yr = String(year || '').replace(/-/g, '_').replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
        return `markall_${yr}_${normMarkallToken(subject)}`;
    };

    // Fast lookups for column selection (All Marks expands to many pair keys server-side).
    let selectedColumnIndex = null;
    const invalidateSelectedColumnIndex = () => {
        selectedColumnIndex = null;
    };
    const getSelectedColumnIndex = () => {
        if (selectedColumnIndex) return selectedColumnIndex;
        const normKeys = new Set();
        const markallUmbrellas = new Set();
        const apallUmbrellas = new Set();
        for (const col of selectedColumns) {
            const n = normaliseKey(col);
            normKeys.add(n);
            normKeys.add(toRelativeKey(n));
            if (n.startsWith('markall_')) {
                const u = markallUmbrellaKeyFromColumn(n);
                if (u) markallUmbrellas.add(u);
            } else if (n.startsWith('apall_')) {
                const u = apallUmbrellaKeyFromColumn(n);
                if (u) apallUmbrellas.add(u);
            }
        }
        selectedColumnIndex = { normKeys, markallUmbrellas, apallUmbrellas };
        return selectedColumnIndex;
    };
    const isNormColumnSelected = (normCol) => {
        if (!normCol) return false;
        const idx = getSelectedColumnIndex();
        const n = normaliseKey(normCol);
        return idx.normKeys.has(n) || idx.normKeys.has(toRelativeKey(n));
    };
    const countLogicalSelectedColumns = () => {
        let n = 0;
        const seenMarkall = new Set();
        const seenApall = new Set();
        for (const col of selectedColumns) {
            const key = normaliseKey(col);
            if (key.startsWith('markall_')) {
                const u = markallUmbrellaKeyFromColumn(key);
                if (u) {
                    if (!seenMarkall.has(u)) {
                        seenMarkall.add(u);
                        n += 1;
                    }
                } else {
                    n += 1;
                }
                continue;
            }
            if (key.startsWith('apall_')) {
                const u = apallUmbrellaKeyFromColumn(key);
                if (u) {
                    if (!seenApall.has(u)) {
                        seenApall.add(u);
                        n += 1;
                    }
                } else {
                    n += 1;
                }
                continue;
            }
            n += 1;
        }
        return n;
    };
    // From an umbrella OR an already-expanded pair key, return the specific
    // umbrella key (e.g. "markall_2025_2026_Math"), or '' when unparseable.
    const markallUmbrellaKeyFromColumn = (colKey) => {
        const rest = String(colKey || '').slice('markall_'.length);
        const parts = rest.split('_');
        if (parts.length < 2 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) return '';
        let tail = parts.slice(2);
        if (tail.length >= 2 && /^\d+$/.test(tail[tail.length - 2])
            && (tail[tail.length - 1] === 'COURSE' || tail[tail.length - 1] === 'MARKS')) {
            tail = tail.slice(0, -2);
        }
        return `markall_${parts[0]}_${parts[1]}_${tail.join('_')}`;
    };
    const isMarkallPairKey = (col) => String(col || '').startsWith('markall_')
        && (String(col).endsWith('_COURSE') || String(col).endsWith('_MARKS'));
    const markallUmbrellaActive = (year, subject) => {
        const umbrella = markallUmbrellaKey(year, subject);
        const idx = getSelectedColumnIndex();
        return idx.markallUmbrellas.has(umbrella)
            || idx.normKeys.has(umbrella)
            || idx.normKeys.has(toRelativeKey(umbrella));
    };
    const markallPickerMode = (umbrellaKey) => {
        const vals = Array.isArray(filters[umbrellaKey]) ? filters[umbrellaKey] : [];
        return vals.some((v) => String(v) === 'mode:only') ? 'only' : 'all';
    };
    const markallExcludeSet = (umbrellaKey) => {
        const set = new Set();
        if (markallPickerMode(umbrellaKey) !== 'all') return set;
        const vals = Array.isArray(filters[umbrellaKey]) ? filters[umbrellaKey] : [];
        vals.forEach((v) => {
            const s = String(v);
            if (s.startsWith('exclude:')) set.add(s.slice('exclude:'.length));
        });
        return set;
    };
    const markallIncludeSet = (umbrellaKey) => {
        const set = new Set();
        if (markallPickerMode(umbrellaKey) !== 'only') return set;
        const vals = Array.isArray(filters[umbrellaKey]) ? filters[umbrellaKey] : [];
        vals.forEach((v) => {
            const s = String(v);
            if (s.startsWith('include:')) set.add(s.slice('include:'.length));
        });
        return set;
    };
    const getMarkallVisibleCourseInputs = (box, subject) => Array.from(box.querySelectorAll('input[data-markall-course]'))
        .filter((ci) => !isMarkallHiddenCourse(subject, ci.dataset.markallCourseName || ''));
    const getMarkallVisibleCourseNames = (box, subject) => getMarkallVisibleCourseInputs(box, subject)
        .map((ci) => String(ci.dataset.markallCourseName || '').trim())
        .filter(Boolean);
    const persistMarkallCoursePicker = (umbrellaKey, mode, checkedNames, allVisibleNames) => {
        const checked = [...new Set(checkedNames)];
        const visible = [...new Set(allVisibleNames)];
        if (mode === 'only') {
            filters[umbrellaKey] = ['mode:only', ...checked.map((n) => `include:${n}`)];
            return;
        }
        const excluded = visible.filter((n) => !checked.includes(n));
        if (excluded.length) {
            filters[umbrellaKey] = excluded.map((n) => `exclude:${n}`);
        } else {
            delete filters[umbrellaKey];
        }
    };
    const updateMarkallModeButtons = (box, mode) => {
        box.querySelectorAll('[data-markall-mode-btn]').forEach((btn) => {
            const active = btn.dataset.markallModeBtn === mode;
            btn.classList.toggle('bg-emerald-600', active);
            btn.classList.toggle('text-white', active);
            btn.classList.toggle('bg-white', !active);
            btn.classList.toggle('text-emerald-800', !active);
            btn.classList.toggle('hover:bg-emerald-50', !active);
        });
        const selectAllBtn = box.querySelector('[data-markall-select-all-courses]');
        if (selectAllBtn) {
            selectAllBtn.classList.toggle('hidden', mode === 'all');
        }
    };

    const syncMarkallCourseControls = () => {
        document.querySelectorAll('input[data-markall="1"]').forEach((cb) => {
            const container = cb.closest('[data-category^="marks_"]');
            const box = container ? container.querySelector('[data-markall-course-box]') : null;
            if (!box) return;
            const year = cb.dataset.markallYear || '';
            const subject = cb.dataset.markallSubject || '';
            const active = markallUmbrellaActive(year, subject);
            box.classList.toggle('hidden', !active);
            const hint = box.querySelector('[data-markall-course-hint]');
            if (!active) {
                if (hint) hint.textContent = '';
                return;
            }
            const umbrellaKey = markallUmbrellaKey(year, subject);
            const mode = markallPickerMode(umbrellaKey);
            const excluded = markallExcludeSet(umbrellaKey);
            const included = markallIncludeSet(umbrellaKey);
            updateMarkallModeButtons(box, mode);
            const courseInputs = Array.from(box.querySelectorAll('input[data-markall-course]'));
            courseInputs.forEach((ci) => {
                const name = ci.dataset.markallCourseName || '';
                const hidden = isMarkallHiddenCourse(subject, name);
                const label = ci.closest('label');
                if (label) label.style.display = hidden ? 'none' : '';
                if (hidden) {
                    ci.checked = false;
                    return;
                }
                if (mode === 'only') {
                    ci.checked = included.has(name);
                } else {
                    ci.checked = !excluded.has(name);
                }
            });
            const visible = getMarkallVisibleCourseInputs(box, subject);
            const checked = visible.filter((ci) => ci.checked).length;
            if (hint) {
                if (!visible.length) {
                    hint.textContent = '';
                } else if (mode === 'only') {
                    hint.textContent = checked === visible.length ? `${checked} courses` : `Only ${checked} of ${visible.length}`;
                } else {
                    hint.textContent = `${checked}/${visible.length} selected`;
                }
            }
        });
    };

    const handleMarkallCourseToggle = (courseCb) => {
        const container = courseCb.closest('[data-category^="marks_"]');
        const box = container ? container.querySelector('[data-markall-course-box]') : null;
        if (!box) return;
        const year = courseCb.dataset.markallYear || '';
        const subject = courseCb.dataset.markallSubject || '';
        const name = String(courseCb.dataset.markallCourseName || '').trim();
        if (isMarkallHiddenCourse(subject, name)) {
            courseCb.checked = false;
            return;
        }
        if (!markallUmbrellaActive(year, subject)) {
            syncMarkallCourseControls();
            return;
        }
        const umbrellaKey = markallUmbrellaKey(year, subject);
        if (!name) return;
        const visible = getMarkallVisibleCourseInputs(box, subject);
        const checkedAfter = visible.filter((ci) => ci.checked);
        if (checkedAfter.length === 0) {
            courseCb.checked = true;
            return;
        }
        const mode = markallPickerMode(umbrellaKey);
        const allNames = getMarkallVisibleCourseNames(box, subject);
        const checkedNames = checkedAfter.map((ci) => String(ci.dataset.markallCourseName || '').trim()).filter(Boolean);
        persistMarkallCoursePicker(umbrellaKey, mode, checkedNames, allNames);
        resetPage();
        syncMarkallCourseControls();
        triggerLoadData({ debounceMs: 250 });
    };

    const setMarkallPickerMode = (box, year, subject, mode) => {
        if (!markallUmbrellaActive(year, subject)) return;
        const umbrellaKey = markallUmbrellaKey(year, subject);
        const allNames = getMarkallVisibleCourseNames(box, subject);
        const visible = getMarkallVisibleCourseInputs(box, subject);
        const checkedNames = visible.filter((ci) => ci.checked).map((ci) => String(ci.dataset.markallCourseName || '').trim()).filter(Boolean);
        if (mode === 'only') {
            // Entering Only: whitelist only explicit picks — not the full All-mode set.
            let includes = checkedNames;
            if (allNames.length && includes.length >= allNames.length) {
                includes = [allNames[0]];
            } else if (!includes.length && allNames.length) {
                includes = [allNames[0]];
            }
            persistMarkallCoursePicker(umbrellaKey, 'only', includes, allNames);
        } else {
            persistMarkallCoursePicker(umbrellaKey, 'all', checkedNames.length ? checkedNames : allNames, allNames);
        }
        resetPage();
        syncMarkallCourseControls();
        triggerLoadData({ debounceMs: 250 });
    };

    const selectAllMarkallCourses = (box, year, subject) => {
        if (!markallUmbrellaActive(year, subject)) return;
        const umbrellaKey = markallUmbrellaKey(year, subject);
        const allNames = getMarkallVisibleCourseNames(box, subject);
        if (!allNames.length) return;
        persistMarkallCoursePicker(umbrellaKey, 'only', allNames, allNames);
        resetPage();
        syncMarkallCourseControls();
        triggerLoadData({ debounceMs: 250 });
    };

    // ─── "All Results" AP subject picker ──────────────────────────────────
    // Selecting "All Results" adds one assessment/result wide pair per AP exam
    // a student has. The subject picker below the umbrella lets the user
    // uncheck subjects; deselected subjects are stored on the umbrella key as
    // 'exclude:<subject>' values in the filters map.
    const apallUmbrellaKey = (year) => `apall_${String(year || '').replace(/-/g, '_')}`;
    // From an umbrella OR an already-expanded pair key, return the umbrella key
    // (e.g. "apall_2025_2026"), or '' when unparseable.
    const apallUmbrellaKeyFromColumn = (colKey) => {
        const rest = String(colKey || '').slice('apall_'.length);
        const parts = rest.split('_');
        if (parts.length < 2 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) return '';
        return `apall_${parts[0]}_${parts[1]}`;
    };
    const isApallPairKey = (col) => /^apall_.*_(SUBJECT|PL)$/.test(String(col || ''));
    const apallUmbrellaActive = (year) => {
        const umbrella = apallUmbrellaKey(year);
        const prefix = `${umbrella}_`;
        return selectedColumns.some((sc) => {
            const n = normaliseKey(sc);
            return n === umbrella || n.startsWith(prefix);
        });
    };
    const apallExcludeSet = (umbrellaKey) => {
        const set = new Set();
        const vals = Array.isArray(filters[umbrellaKey]) ? filters[umbrellaKey] : [];
        vals.forEach((v) => {
            const s = String(v);
            if (s.startsWith('exclude:')) set.add(s.slice('exclude:'.length));
        });
        return set;
    };
    const apallSubjectBoxForYear = (year) => {
        const target = String(year || '');
        return Array.from(document.querySelectorAll('[data-apall-subject-box]'))
            .find((box) => String(box.dataset.apallYear || '') === target) || null;
    };
    const syncApallSubjectControls = () => {
        document.querySelectorAll('input[data-apall="1"]').forEach((cb) => {
            const year = cb.dataset.apallYear || '';
            const box = apallSubjectBoxForYear(year);
            if (!box) return;
            const active = apallUmbrellaActive(year);
            box.classList.toggle('hidden', !active);
            const hint = box.querySelector('[data-apall-subject-hint]');
            if (!active) {
                if (hint) hint.textContent = '';
                return;
            }
            const umbrellaKey = apallUmbrellaKey(year);
            const excluded = apallExcludeSet(umbrellaKey);
            const inputs = Array.from(box.querySelectorAll('input[data-apall-subject]'));
            inputs.forEach((ci) => {
                ci.checked = !excluded.has(String(ci.dataset.apallSubjectName || '').trim());
            });
            const checked = inputs.filter((ci) => ci.checked).length;
            if (hint) hint.textContent = inputs.length ? `${checked}/${inputs.length} selected` : '';
        });
    };
    const handleApallSubjectToggle = (subjectCb) => {
        const year = subjectCb.dataset.apallYear || '';
        const name = String(subjectCb.dataset.apallSubjectName || '').trim();
        if (!apallUmbrellaActive(year)) {
            syncApallSubjectControls();
            return;
        }
        const umbrellaKey = apallUmbrellaKey(year);
        if (!name) return;
        const excluded = apallExcludeSet(umbrellaKey);
        if (subjectCb.checked) {
            excluded.delete(name);
        } else {
            excluded.add(name);
        }
        if (excluded.size) {
            filters[umbrellaKey] = Array.from(excluded).map((n) => `exclude:${n}`);
        } else {
            delete filters[umbrellaKey];
        }
        resetPage();
        syncApallSubjectControls();
        triggerLoadData();
    };

    // Wire toggle buttons (may not be in DOM yet when column sidebar loads via HTMX)
    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-year-mode-btn]');
        if (btn) {
            setYearMode(btn.dataset.yearModeBtn);
            return;
        }
        const markallModeBtn = e.target.closest('[data-markall-mode-btn]');
        if (markallModeBtn) {
            const box = markallModeBtn.closest('[data-markall-course-box]');
            const umbrellaCb = box?.closest('[data-category^="marks_"]')?.querySelector('input[data-markall="1"]');
            if (box && umbrellaCb) {
                setMarkallPickerMode(
                    box,
                    umbrellaCb.dataset.markallYear || '',
                    umbrellaCb.dataset.markallSubject || '',
                    markallModeBtn.dataset.markallModeBtn || 'all',
                );
            }
            return;
        }
        const markallSelectAll = e.target.closest('[data-markall-select-all-courses]');
        if (markallSelectAll) {
            const box = markallSelectAll.closest('[data-markall-course-box]');
            const umbrellaCb = box?.closest('[data-category^="marks_"]')?.querySelector('input[data-markall="1"]');
            if (box && umbrellaCb) {
                selectAllMarkallCourses(
                    box,
                    umbrellaCb.dataset.markallYear || '',
                    umbrellaCb.dataset.markallSubject || '',
                );
            }
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

    const getScheduleFilterValues = () => ({
        schedule_period: getScheduleSelectValues('schedule_period'),
        schedule_subject: getScheduleSelectValues('schedule_subject'),
        schedule_course_name: getScheduleSelectValues('schedule_course_name'),
        schedule_staff: getScheduleSelectValues('schedule_staff'),
    });

    const appendScheduleFilterParams = (url) => {
        if (!isScheduleFilterBarActive()) return;
        const sf = getScheduleFilterValues();
        Object.entries(sf).forEach(([key, vals]) => {
            url.searchParams.delete(key);
            (vals || []).forEach((v) => {
                if (v) url.searchParams.append(key, v);
            });
        });
    };

    const syncScheduleCourseStaffDisabled = () => {
        const courseBtn = getScheduleFilterRoot('schedule_course_name')?.querySelector('[data-schedule-ms-btn]');
        const staffBtn = getScheduleFilterRoot('schedule_staff')?.querySelector('[data-schedule-ms-btn]');
        if (courseBtn) courseBtn.disabled = false;
        if (staffBtn) staffBtn.disabled = false;
    };

    const appendScheduleQueryList = (url, key, values) => {
        url.searchParams.delete(key);
        (values || []).forEach((v) => {
            if (v) url.searchParams.append(key, v);
        });
    };

    let scheduleOptionsRefreshSeq = 0;
    let scheduleOptionsAbort = null;

    const resetScheduleFiltersForSchoolChange = () => {
        SCHEDULE_MS_KEYS.forEach((key) => clearScheduleSelect(key));
        syncScheduleCourseStaffDisabled();
    };

    const onSchoolColumnFilterChanged = () => {
        if (!scheduleFiltersEligible) return;
        updateScheduleFilterChrome();
        if (!isScheduleFilterBarActive()) return;
        resetScheduleFiltersForSchoolChange();
        refreshScheduleFilterDropdowns({ preserveSelections: false });
    };

    const fetchScheduleFilterOptions = async (refreshSeq) => {
        if (!isScheduleFilterBarActive() || !scheduleFilterOptionsUrl) return null;
        const schoolYearRaw = document.getElementById('school_year')?.value || '';
        const schoolYear = resolveRelativeSchoolYear(schoolYearRaw);
        if (!schoolYear) return null;
        const url = new URL(scheduleFilterOptionsUrl, window.location.origin);
        url.searchParams.set('school_year', schoolYear);
        const statusVal = document.getElementById('status')?.value || '';
        if (statusVal) url.searchParams.set('status', statusVal);
        appendScheduleQueryList(url, 'period', getScheduleSelectValues('schedule_period'));
        appendScheduleQueryList(url, 'subject', getScheduleSelectValues('schedule_subject'));
        appendScheduleQueryList(url, 'course_name', getScheduleSelectValues('schedule_course_name'));
        appendScheduleQueryList(url, 'staff', getScheduleSelectValues('schedule_staff'));
        url.searchParams.set('filters', JSON.stringify(filters || {}));

        if (scheduleOptionsAbort) scheduleOptionsAbort.abort();
        scheduleOptionsAbort = new AbortController();
        const signal = scheduleOptionsAbort.signal;
        try {
            const res = await fetch(url.toString(), { credentials: 'same-origin', signal });
            if (refreshSeq !== scheduleOptionsRefreshSeq) return null;
            if (!res.ok) return null;
            const data = await res.json();
            if (refreshSeq !== scheduleOptionsRefreshSeq) return null;
            if (data && data.error) return null;
            return data;
        } catch (e) {
            if (e.name === 'AbortError') return null;
            return null;
        }
    };

    const refreshScheduleFilterDropdowns = async ({ preserveSelections = true } = {}) => {
        if (!isScheduleFilterBarActive()) return;
        const refreshSeq = ++scheduleOptionsRefreshSeq;
        const keepPeriod = preserveSelections ? getScheduleSelectValues('schedule_period') : [];
        const keepSubject = preserveSelections ? getScheduleSelectValues('schedule_subject') : [];
        const keepCourse = preserveSelections ? getScheduleSelectValues('schedule_course_name') : [];
        const keepStaff = preserveSelections ? getScheduleSelectValues('schedule_staff') : [];

        const data = await fetchScheduleFilterOptions(refreshSeq);
        if (!data || refreshSeq !== scheduleOptionsRefreshSeq) return;

        if (data.ui_mode) {
            scheduleFilterUiMode = data.ui_mode;
            syncScheduleFilterModeVisibility();
        }

        if (scheduleFilterShowsPeriod()) {
            fillScheduleMultiSelect('schedule_period', data.periods || [], keepPeriod);
        } else {
            clearScheduleSelect('schedule_period');
        }
        if (scheduleFilterShowsSubject()) {
            fillScheduleMultiSelect('schedule_subject', data.subjects || [], keepSubject);
        }
        fillScheduleMultiSelect('schedule_course_name', data.courses || [], keepCourse);
        if (scheduleFilterShowsStaff() && scheduleMarksCascade()) {
            fillScheduleMultiSelect('schedule_staff', data.staff || [], keepStaff, { valueKey: 'id', labelKey: 'name' });
        } else {
            clearScheduleSelect('schedule_staff');
        }
        syncScheduleCourseStaffDisabled();
    };

    const applyScheduleFilterPayload = (payload) => {
        if (!payload) return;
        if (!isScheduleFilterBarActive()) return;
        const fromFilters = payload.filters || {};
        const period = payload.schedule_period ?? fromFilters.schedule_period ?? [];
        const subject = payload.schedule_subject ?? fromFilters.schedule_subject ?? [];
        const course = payload.schedule_course_name ?? fromFilters.schedule_course_name ?? [];
        const staff = payload.schedule_staff ?? fromFilters.schedule_staff ?? [];
        if (period?.length) setScheduleSelectValues('schedule_period', period);
        if (subject?.length) setScheduleSelectValues('schedule_subject', subject);
        if (course?.length) setScheduleSelectValues('schedule_course_name', course);
        if (staff?.length) setScheduleSelectValues('schedule_staff', staff);
        refreshScheduleFilterDropdowns({ preserveSelections: true });
    };

    const getCurrentFiltersForSave = () => {
        return {
            school_year: document.getElementById('school_year')?.value,
            grade: document.getElementById('grade')?.value,
            status: document.getElementById('status')?.value,
            search: document.getElementById('search')?.value,
            ...getScheduleFilterValues(),
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
                ...getScheduleFilterValues(),
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
            if (schoolYearSelect && stored.school_year !== undefined) schoolYearSelect.value = resolveRelativeSchoolYear(stored.school_year || '');
            if (gradeSelect && stored.grade !== undefined) gradeSelect.value = stored.grade || '';
            if (statusInput && stored.status !== undefined) statusInput.value = stored.status || '';
            if (scheduleFiltersEligible) {
                if (stored.schedule_period !== undefined) setScheduleSelectValues('schedule_period', stored.schedule_period);
                if (stored.schedule_subject !== undefined) setScheduleSelectValues('schedule_subject', stored.schedule_subject);
                if (stored.schedule_course_name !== undefined) setScheduleSelectValues('schedule_course_name', stored.schedule_course_name);
                if (stored.schedule_staff !== undefined) setScheduleSelectValues('schedule_staff', stored.schedule_staff);
            }
        } catch (e) {
            // ignore
        } finally {
            stateRestored = true;
        }
    };

    const appendStudentQueryParams = (url, { includeEmptyFilters = false } = {}) => {
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
            } else if (includeEmptyFilters || lastPayload !== null) {
                url.searchParams.set('filters', '{}');
            }
        }

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
            if (schoolYearSelect) url.searchParams.set('school_year', schoolYearSelect.value);
            if (gradeSelect && gradeSelect.value) url.searchParams.set('grade', gradeSelect.value);
            if (statusInput) url.searchParams.set('status', statusInput.value);
            appendScheduleFilterParams(url);
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
        appendScheduleFilterParams(url);

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

    // The dynamic selector only depends on these filters. Refetching it on
    // every loadData (pagination, sort, row selection) re-renders thousands of
    // checkboxes for no visible change, so track the last applied signature and
    // skip the request unless a structure-affecting filter actually changed.
    let lastStructuresSignature = '';

    const buildColumnStructureFilters = () => {
        const columnFilters = {
            school_year: document.getElementById('school_year')?.value || '',
            grade: document.getElementById('grade')?.value || '',
            status: document.getElementById('status')?.value || '',
            search: document.getElementById('search')?.value || '',
        };
        Object.keys(filters || {}).sort().forEach((k) => {
            columnFilters[k] = filters[k];
        });
        // Keep marks/test column trees aligned with schedule-filtered student cohort.
        if (scheduleFiltersEligible && isScheduleFilterBarActive()) {
            const sf = getScheduleFilterValues();
            SCHEDULE_MS_KEYS.forEach((key) => {
                const vals = sf[key];
                if (Array.isArray(vals) && vals.length) columnFilters[key] = vals;
            });
        }
        return columnFilters;
    };

    const refreshColumnStructures = async (opts = {}) => {
        if (!columnStructuresUrl) return;
        const target = document.getElementById('dynamic-column-structures');
        if (!target) return;

        const columnFilters = buildColumnStructureFilters();
        const signature = JSON.stringify(columnFilters);
        if (!opts.force && target.dataset.loaded === '1' && signature === lastStructuresSignature) {
            // Structures already reflect the current filters; just resync the
            // selected-column UI (cheap, no network).
            renderSelectedColumns();
            updateYearModeButtons();
            return;
        }

        const openCategories = Array.from(target.querySelectorAll('[data-category]'))
            .filter((el) => !el.classList.contains('hidden'))
            .map((el) => el.getAttribute('data-category'))
            .filter(Boolean);

        const url = new URL(columnStructuresUrl, window.location.origin);
        url.searchParams.set('column_filters', JSON.stringify(columnFilters));

        try {
            const res = await fetch(url.toString(), { credentials: 'same-origin' });
            if (!res.ok) return;
            target.innerHTML = await res.text();
            lastStructuresSignature = signature;
            target.dataset.loaded = '1';
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
                        if (el.dataset.lazyMarks === '1') loadLazyMarkCourse(el);
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

    const isColumnCheckboxSelected = (checkbox) => {
        const col = checkbox.dataset.column;
        if (!col) return false;
        if (checkbox.dataset.markall === '1') {
            const idx = getSelectedColumnIndex();
            const normCol = normaliseKey(col);
            const umbrella = markallUmbrellaKey(
                checkbox.dataset.markallYear || '',
                checkbox.dataset.markallSubject || '',
            );
            return idx.normKeys.has(normCol)
                || idx.normKeys.has(toRelativeKey(normCol))
                || idx.markallUmbrellas.has(umbrella);
        }
        if (checkbox.dataset.apall === '1') {
            return apallUmbrellaActive(checkbox.dataset.apallYear || '');
        }
        return isNormColumnSelected(col);
    };

    const countSelectedColumnsInSection = (sectionEl) => {
        if (!sectionEl) return 0;
        let count = 0;
        sectionEl.querySelectorAll('input[data-column]').forEach((checkbox) => {
            if (isColumnCheckboxSelected(checkbox)) count += 1;
        });
        return count;
    };

    const updateColumnSelectorSectionUi = () => {
        const root = document.getElementById('column-selector');
        if (!root) return;

        root.querySelectorAll('input[data-column]').forEach((checkbox) => {
            const label = checkbox.closest('label');
            if (!label) return;
            const selected = isColumnCheckboxSelected(checkbox);
            label.classList.toggle('bg-blue-50', selected);
            label.classList.toggle('ring-1', selected);
            label.classList.toggle('ring-inset', selected);
            label.classList.toggle('ring-blue-200', selected);
            const text = label.querySelector('span');
            if (text) {
                text.classList.toggle('font-semibold', selected);
                text.classList.toggle('text-blue-900', selected);
                text.classList.toggle('text-slate-700', !selected);
            }
        });

        root.querySelectorAll('[data-toggle-category]').forEach((button) => {
            const category = button.getAttribute('data-toggle-category');
            if (!category) return;
            const section = root.querySelector(`[data-category="${cssEscape(category)}"]`);
            const count = countSelectedColumnsInSection(section);

            const titleEl = [...button.children].find((el) => !el.hasAttribute('data-caret'));
            if (!titleEl) return;

            let badge = titleEl.querySelector('[data-section-selected-count]');
            if (!badge) {
                badge = document.createElement('span');
                badge.setAttribute('data-section-selected-count', '');
                titleEl.classList.add('inline-flex', 'items-center', 'gap-1.5', 'min-w-0');
                titleEl.appendChild(badge);
            }
            badge.className = 'inline-flex min-w-[0.875rem] h-3.5 px-0.5 items-center justify-center rounded-full text-[9px] font-semibold leading-none tabular-nums bg-blue-600 text-white';

            badge.textContent = String(count);
            badge.classList.toggle('hidden', count === 0);
            button.classList.toggle('bg-blue-50', count > 0);
            button.classList.toggle('ring-1', count > 0);
            button.classList.toggle('ring-inset', count > 0);
            button.classList.toggle('ring-blue-100', count > 0);
        });
    };

    let columnSelectorUiRaf = 0;
    const scheduleColumnSelectorSectionUi = () => {
        if (columnSelectorUiRaf) return;
        columnSelectorUiRaf = window.requestAnimationFrame(() => {
            columnSelectorUiRaf = 0;
            updateColumnSelectorSectionUi();
        });
    };

    const syncCheckboxes = () => {
        const selectorRoot = document.getElementById('column-selector');
        const checkboxRoot = selectorRoot || document;
        checkboxRoot.querySelectorAll('input[data-column]').forEach((checkbox) => {
            checkbox.checked = isColumnCheckboxSelected(checkbox);
        });
        checkboxRoot.querySelectorAll('input[data-action="select-topic"]').forEach((topicCb) => {
            const targetCategory = topicCb.dataset.targetCategory;
            const container = document.querySelector(`[data-category="${targetCategory}"]`);
            if (!container) return;
            const cols = Array.from(container.querySelectorAll('input[data-column]'))
                .map(cb => cb.dataset.column).filter(Boolean);
            if (cols.length === 0) { topicCb.checked = false; topicCb.indeterminate = false; return; }
            const selectedCount = cols.filter(c => isNormColumnSelected(c)).length;
            topicCb.checked = selectedCount === cols.length;
            topicCb.indeterminate = selectedCount > 0 && selectedCount < cols.length;
        });
        syncMarkallCourseControls();
        syncApallSubjectControls();
        scheduleColumnSelectorSectionUi();
    };

    const renderSelectedColumns = () => {
        invalidateSelectedColumnIndex();
        selectedColumns = pinPictureFirst(selectedColumns);
        const logicalCount = countLogicalSelectedColumns();
        if (selectedCountEl) selectedCountEl.textContent = logicalCount;
        if (selectedCountToolbarEl) selectedCountToolbarEl.textContent = String(logicalCount);
        const tooltipLabels = [];
        const seenMarkallTip = new Set();
        const seenApallTip = new Set();
        for (const col of selectedColumns) {
            const key = normaliseKey(col);
            if (key.startsWith('markall_')) {
                const u = markallUmbrellaKeyFromColumn(key);
                if (u && !seenMarkallTip.has(u)) {
                    seenMarkallTip.add(u);
                    tooltipLabels.push(getColumnLabel(u) || u);
                }
                continue;
            }
            if (key.startsWith('apall_')) {
                const u = apallUmbrellaKeyFromColumn(key);
                if (u && !seenApallTip.has(u)) {
                    seenApallTip.add(u);
                    tooltipLabels.push(getColumnLabel(u) || u);
                }
                continue;
            }
            const label = getColumnLabel(col);
            tooltipLabels.push(label.includes('\n') ? label.split('\n')[0] : label);
        }
        const maxTip = 12;
        let tooltipText = tooltipLabels.join(', ') || 'No columns selected';
        if (tooltipLabels.length > maxTip) {
            tooltipText = `${tooltipLabels.slice(0, maxTip).join(', ')} … +${tooltipLabels.length - maxTip} more`;
        }
        if (selectedCountEl) selectedCountEl.setAttribute('title', tooltipText);
        if (selectedCountToolbarEl) selectedCountToolbarEl.setAttribute('title', tooltipText);
        syncCheckboxes();
    };

    // Fetch a mark course's period checkboxes the first time its section is
    // expanded. The initial selector response intentionally omits them.
    const loadLazyMarkCourse = async (section) => {
        if (!section || !columnStructuresUrl) return;
        if (section.dataset.lazyLoaded === '1') return;
        section.dataset.lazyLoaded = '1';
        const url = new URL(columnStructuresUrl, window.location.origin);
        url.searchParams.set('marks_course_year', section.dataset.lazyYear || '');
        url.searchParams.set('marks_course_subject', section.dataset.lazySubject || '');
        url.searchParams.set('marks_course_name', section.dataset.lazyCourse || '');
        url.searchParams.set('column_filters', JSON.stringify(buildColumnStructureFilters()));
        try {
            const res = await fetch(url.toString(), { credentials: 'same-origin' });
            if (!res.ok) {
                section.dataset.lazyLoaded = '';
                return;
            }
            section.innerHTML = await res.text();
            if (window.htmx) htmx.process(section);
            renderSelectedColumns();
        } catch (e) {
            section.dataset.lazyLoaded = '';
        }
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

        if (!section.classList.contains('hidden') && section.dataset.lazyMarks === '1') {
            loadLazyMarkCourse(section);
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
        if (col === 'race_list') {
            if (Array.isArray(value)) {
                return value.filter(v => v !== null && v !== undefined && String(v).trim() !== '').join(', ');
            }
            const s = String(value).trim();
            if (!s || s === '—' || s === '[]' || s === 'null') return '—';
            // JSON array string → readable list
            if (s.startsWith('[')) {
                try {
                    const parsed = JSON.parse(s);
                    if (Array.isArray(parsed)) {
                        return parsed.filter(v => v !== null && v !== undefined && String(v).trim() !== '').join(', ') || '—';
                    }
                } catch (_) { /* keep raw */ }
            }
            return s;
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
            invalidateSelectedColumnIndex();
        }

        if (payload.filters) {
            filters = payload.filters;
        }
        updateScheduleFilterChrome();

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
        if (payload.school_year !== undefined && schoolYearSelect) {
            const resolvedYear = resolveRelativeSchoolYear(payload.school_year || '');
            if (resolvedYear && [...schoolYearSelect.options].some(o => o.value === resolvedYear)) {
                schoolYearSelect.value = resolvedYear;
            }
        }
        if (payload.grade !== undefined && gradeSelect) gradeSelect.value = payload.grade || '';
        if (payload.status !== undefined && statusInput) statusInput.value = payload.status || '';
        applyScheduleFilterPayload(payload);

        const columns = pinPictureFirst(selectedColumns || []);
        selectedColumns = columns;
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
                // Umbrella-level course/subject exclusions are represented by the
                // picker, not as an active-filter chip.
                if (String(col || '').startsWith('markall_') && !isMarkallPairKey(col)) return;
                if (String(col || '').startsWith('apall_') && !isApallPairKey(col)) return;
                if (Array.isArray(vals) && vals.length > 0) {
                    hasActiveFilters = true;
                    let colLabel = getColumnLabel(col);
                    if (colLabel.includes('\n')) colLabel = colLabel.split('\n')[0];

                    const chip = document.createElement('span');
                    chip.className = 'inline-flex items-center gap-1 bg-blue-50 text-blue-700 px-2 py-0.5 rounded border border-blue-200';

                    let valText = vals.map(v => {
                        if (v === '' || v === null) return '(Blank)';
                        const s = String(v);
                        if (s.startsWith('period:')) return s.slice('period:'.length);
                        if (s.startsWith('mark:')) return s.slice('mark:'.length);
                        if (s.startsWith('exclude:')) return s.slice('exclude:'.length);
                        return s;
                    }).join(', ');
                    if (valText.length > 20) valText = `${vals.length} selected`;

                    chip.innerHTML = `<span class="font-semibold">${escapeHtml(colLabel)}:</span> ${escapeHtml(valText)} <button type="button" data-action="clear-filter" data-column="${escapeHtml(col)}" class="ml-1 text-blue-500 hover:text-blue-900 font-bold focus:outline-none">×</button>`;
                    activeFiltersListEl.appendChild(chip);
                }
            });

            if (isScheduleFilterBarActive()) {
                const sf = getScheduleFilterValues();
                const scheduleParts = [];
                const joinVals = (vals) => (Array.isArray(vals) ? vals.join(', ') : String(vals || ''));
                if (sf.schedule_period?.length) scheduleParts.push(`Period ${joinVals(sf.schedule_period)}`);
                if (sf.schedule_subject?.length) scheduleParts.push(joinVals(sf.schedule_subject));
                if (sf.schedule_course_name?.length) scheduleParts.push(joinVals(sf.schedule_course_name));
                if (sf.schedule_staff?.length && scheduleFilterShowStaff) {
                    const staffRoot = getScheduleFilterRoot('schedule_staff');
                    const staffLabels = staffRoot
                        ? [...staffRoot.querySelectorAll('[data-schedule-ms-value]:checked')]
                            .map((cb) => cb.closest('label')?.querySelector('span')?.textContent || cb.value)
                        : sf.schedule_staff;
                    scheduleParts.push(Array.isArray(staffLabels) ? staffLabels.join(', ') : staffLabels);
                }
                if (scheduleParts.length) {
                    hasActiveFilters = true;
                    const chip = document.createElement('span');
                    chip.className = 'inline-flex items-center gap-1 bg-emerald-50 text-emerald-800 px-2 py-0.5 rounded border border-emerald-200';
                    chip.innerHTML = `<span class="font-semibold">Schedule:</span> ${escapeHtml(scheduleParts.join(' · '))} <button type="button" data-action="clear-schedule-filters" class="ml-1 text-emerald-600 hover:text-emerald-900 font-bold focus:outline-none">×</button>`;
                    activeFiltersListEl.appendChild(chip);
                }
            }

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
            if (isPictureCol(col) && !Number.isFinite(width)) {
                html += `<col data-col="${escapeHtml(col)}" style="width:44px">`;
                return;
            }
            const minW = isPictureCol(col) ? 40 : 60;
            const widthStyle = Number.isFinite(width) ? ` style="width:${Math.max(minW, width)}px"` : '';
            html += `<col data-col="${escapeHtml(col)}"${widthStyle}>`;
        });
        html += '</colgroup>';

        html += '<thead class="bg-slate-900 text-white sticky top-0 z-10 shadow-md"><tr class="bg-slate-900">';

        // Selection column header
        html += '<th class="px-4 py-3 text-left text-xs font-bold text-slate-400 uppercase bg-slate-900 border-b border-slate-800">';
        html += '<input type="checkbox" data-action="select-all-visible" class="rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-slate-900 cursor-pointer" aria-label="Select all visible students">';
        html += '</th>';

        columns.forEach((col, index) => {
            if (isPictureCol(col)) {
                html += `<th data-column="${escapeHtml(col)}" class="relative group px-2 py-3 text-left text-xs font-bold text-slate-300 uppercase bg-slate-900 border-b border-slate-800 select-none">`;
                html += `<div class="flex items-center justify-between gap-1">`;
                html += `<span>${escapeHtml(getColumnLabel(col))}</span>`;
                html += `<button draggable="false" type="button" data-action="remove-column" data-column="${escapeHtml(col)}" class="opacity-0 group-hover:opacity-100 transition-opacity text-slate-600 hover:text-red-400 hover:bg-slate-800 rounded p-0.5" title="Remove column">✕</button>`;
                html += `</div></th>`;
                return;
            }
            const label = getColumnLabel(col);
            const optionsForCol = Array.isArray(filterOptions[col]) ? filterOptions[col] : [];
            const hasActiveFilter = Array.isArray(filters[col]) && filters[col].length > 0;
            // Race List is stored in add_data JSON; always offer the filter UI when the
            // column is visible (options may load from PostgreSQL after the table renders).
            const hasFilter = optionsForCol.length > 0 || hasActiveFilter || col === 'race_list'
                || col.startsWith('course_prog_');
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
                    // "All Marks" MARKS columns offer marking-period and mark as
                    // separate individual selections; render them as two groups.
                    const isMarkallMarks = /^markall_.*_MARKS$/.test(col);
                    if (isMarkallMarks && optionsForCol.some(v => String(v).startsWith('period:') || String(v).startsWith('mark:'))) {
                        const periodOpts = optionsForCol.filter(v => String(v).startsWith('period:')).sort((a, b) => {
                            const aIsLive = String(a).toLowerCase() === 'period:live';
                            const bIsLive = String(b).toLowerCase() === 'period:live';
                            if (aIsLive && !bIsLive) return 1;
                            if (!aIsLive && bIsLive) return -1;
                            return String(a).localeCompare(String(b));
                        });
                        const markOpts = optionsForCol.filter(v => String(v).startsWith('mark:'));

                        if (periodOpts.length) {
                            html += `<div class="text-[9px] font-bold text-slate-400 uppercase tracking-wider px-1 pt-1">Marking Period</div>`;
                            periodOpts.forEach((value) => {
                                const checked = filters[col]?.includes(value) ? 'checked' : '';
                                const labelValue = String(value).replace(/^period:/, '');
                                const isLivePeriod = labelValue.toLowerCase() === 'live';
                                html += `<label class="flex items-center gap-2 text-xs text-slate-700 p-1 rounded hover:bg-slate-50">`;
                                html += `<input draggable="false" type="checkbox" data-filter-value="${escapeHtml(value)}" data-filter-column="${escapeHtml(col)}" ${checked} class="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500">`;
                                html += `<span class="${isLivePeriod ? 'font-bold text-slate-900' : ''}">${escapeHtml(labelValue)}</span>`;
                                html += `</label>`;
                            });
                        }
                        if (markOpts.length) {
                            html += `<div class="text-[9px] font-bold text-slate-400 uppercase tracking-wider px-1 pt-1 mt-1">Mark</div>`;
                            markOpts.forEach((value) => {
                                const checked = filters[col]?.includes(value) ? 'checked' : '';
                                const labelValue = String(value).replace(/^mark:/, '');
                                html += `<label class="flex items-center gap-2 text-xs text-slate-700 p-1 rounded hover:bg-slate-50">`;
                                html += `<input draggable="false" type="checkbox" data-filter-value="${escapeHtml(value)}" data-filter-column="${escapeHtml(col)}" ${checked} class="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500">`;
                                html += `<span>${escapeHtml(labelValue)}</span>`;
                                html += `</label>`;
                            });
                        }
                    } else {
                        optionsForCol.forEach((value) => {
                            const checked = filters[col]?.includes(value) ? 'checked' : '';
                            let labelValue = String(value);
                            if (value === '' || value === null) {
                                labelValue = '(Blank)';
                            } else if (DATE_COLUMNS.has(col)) {
                                labelValue = formatDateForDisplay(labelValue);
                            }
                            const isLiveVal = String(labelValue).toLowerCase() === 'live';
                            html += `<label class="flex items-center gap-2 text-xs text-slate-700 p-1 rounded hover:bg-slate-50">`;
                            html += `<input draggable="false" type="checkbox" data-filter-value="${escapeHtml(value)}" data-filter-column="${escapeHtml(col)}" ${checked} class="rounded border-slate-300 text-blue-600 focus:ring-blue-600">`;
                            html += `<span class="${isLiveVal ? 'font-bold text-slate-900' : ''}">${escapeHtml(labelValue)}</span>`;
                            html += `</label>`;
                        });
                    }
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
                    if (isPictureCol(col)) {
                        const picSrc = ssid ? buildStudentPictureUrl(ssid) : '';
                        html += `<td class="px-2 py-1">`;
                        if (picSrc) {
                            html += `<img src="${escapeHtml(picSrc)}" alt="" width="24" height="24" loading="lazy" decoding="async" class="h-6 w-6 rounded-full object-cover bg-slate-100 ring-1 ring-slate-200">`;
                        } else {
                            html += `—`;
                        }
                        html += `</td>`;
                        return;
                    }
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
                        let cellValHtml = escapeHtml(value);
                        if (typeof value === 'string' && (col.startsWith('markall_') || col.startsWith('mark_'))) {
                            cellValHtml = cellValHtml.replace(/(^|[\s,;])(Live)(:|\b)/g, '$1<span class="font-bold text-slate-900">$2</span>$3');
                        }
                        html += `<td class="px-4 py-3 text-sm text-slate-900 whitespace-nowrap">${cellValHtml}</td>`;
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
            const cteOverlayEl = document.getElementById('student-cte-overlay');
            if (cteOverlayEl) hideOverlay(cteOverlayEl);
            hideOverlay(summaryOverlayEl);
            if (summaryOverlayBodyEl) summaryOverlayBodyEl.innerHTML = '';
            return;
        }

        const openCteEl = event.target.closest('[data-action="open-student-cte"]');
        if (openCteEl) {
            event.preventDefault();
            const cteOverlayEl = document.getElementById('student-cte-overlay');
            if (cteOverlayEl) showOverlay(cteOverlayEl);
            return;
        }

        const closeCteEl = event.target.closest('[data-action="close-student-cte"]');
        if (closeCteEl) {
            event.preventDefault();
            const cteOverlayEl = document.getElementById('student-cte-overlay');
            if (cteOverlayEl) hideOverlay(cteOverlayEl);
            return;
        }


    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        const cteOverlayEl = document.getElementById('student-cte-overlay');
        if (cteOverlayEl && !cteOverlayEl.classList.contains('hidden')) {
            hideOverlay(cteOverlayEl);
            return;
        }
        if (summaryOverlayEl && !summaryOverlayEl.classList.contains('hidden')) {
            hideOverlay(summaryOverlayEl);
            if (summaryOverlayBodyEl) summaryOverlayBodyEl.innerHTML = '';
        }
    });

    let loadDataSeq = 0;

    const resetPage = () => {
        page = 1;
        pendingCursor = '';
        nextCursor = '';
        cursorStack = [];
    };

    const loadData = () => {
        restoreViewState();

        const url = new URL(loadStudentsUrl, window.location.origin);
        url.searchParams.set('page', page);
        if (page <= 1) pendingCursor = '';
        if (pendingCursor) url.searchParams.set('cursor', pendingCursor);

        refreshColumnStructures();

        appendStudentQueryParams(url);
        url.searchParams.set('format', 'json');

        const urlStr = url.toString();
        if (inFlightUrl === urlStr) return;
        if (loadAbort) loadAbort.abort();
        loadAbort = new AbortController();
        inFlightUrl = urlStr;
        const requestSeq = ++loadDataSeq;

        // Check SWR client cache for instant table render
        const deCacheKey = `de_data_${urlStr}`;
        let hasSWRHit = false;
        try {
            const cachedStr = sessionStorage.getItem(deCacheKey);
            if (cachedStr) {
                const cachedPayload = JSON.parse(cachedStr);
                if (cachedPayload && Array.isArray(cachedPayload.students)) {
                    renderTable(cachedPayload);
                    hasSWRHit = true;
                }
            }
        } catch (e) {}

        if (!hasSWRHit) {
            showLoading('Loading student data...');
        }
        clearStatsCache();

        fetch(urlStr, { credentials: 'same-origin', signal: loadAbort.signal })
            .then(async (response) => {
                if (!response.ok) {
                    const text = await response.text();
                    throw new Error(`HTTP ${response.status}: ${text.slice(0, 200)}`);
                }
                return response.json();
            })
            .then((payload) => {
                if (requestSeq !== loadDataSeq) return;
                nextCursor = payload.next_cursor || '';
                pendingCursor = '';
                try {
                    sessionStorage.setItem(deCacheKey, JSON.stringify(payload));
                } catch (e) {}
                renderTable(payload);
                saveViewState();
                hideLoading();
            })
            .catch((error) => {
                if (error.name === 'AbortError' || requestSeq !== loadDataSeq) return;
                console.error('Error loading data:', error);
                hideLoading();
                const targetEl = document.querySelector('#student-table');
                if (targetEl && !hasSWRHit) {
                    targetEl.innerHTML = '<div class="text-center py-8 text-red-600">Failed to load data.</div>';
                }
            })
            .finally(() => {
                if (inFlightUrl === urlStr) inFlightUrl = '';
            });
    };

    window.loadData = loadData;

    let loadDataDebounceTimer = null;
    const triggerLoadData = (opts = {}) => {
        const run = () => {
            loadDataDebounceTimer = null;
            if (typeof window.loadData === 'function') {
                window.loadData();
            } else {
                loadData();
            }
        };
        if (opts.debounceMs) {
            window.clearTimeout(loadDataDebounceTimer);
            loadDataDebounceTimer = window.setTimeout(run, opts.debounceMs);
            return;
        }
        window.clearTimeout(loadDataDebounceTimer);
        run();
    };

    const updateColumnSelection = (col) => {
        const key = storageKey(col);
        const normKey = normaliseKey(key);

        // "All Marks" mark umbrella handling
        if (normKey.startsWith('markall_')) {
            const umbrella = markallUmbrellaKeyFromColumn(normKey);
            if (!umbrella) {
                // Malformed key — fall back to plain toggle behavior
                const isSelectedPlain = selectedColumns.some(c => normaliseKey(c) === normKey);
                if (isSelectedPlain) {
                    if (selectedColumns.length <= 1) return;
                    selectedColumns = selectedColumns.filter(c => normaliseKey(c) !== normKey);
                    if (filters[key]) delete filters[key];
                } else {
                    selectedColumns.push(key);
                }
                resetPage();
                renderSelectedColumns();
                triggerLoadData();
                return;
            }
            const prefix = `${umbrella}_`;
            const yearUs = umbrella.slice('markall_'.length, 'markall_'.length + 9);
            const yearHyphen = yearUs.replace('_', '-');
            const subjNorm = umbrella.slice('markall_'.length + 10);
            const isSelected = selectedColumns.some(sc => {
                const n = normaliseKey(sc);
                return n === umbrella || n.startsWith(prefix);
            });
            if (isSelected) {
                // Uncheck: remove umbrella + expanded pairs
                selectedColumns = selectedColumns.filter(sc => {
                    const n = normaliseKey(sc);
                    return n !== umbrella && !n.startsWith(prefix);
                });
            } else {
                // Check: unselect individual mark_<year>_<subject>_* columns for this subject,
                // then select the umbrella (all courses included).
                selectedColumns = selectedColumns.filter(sc => {
                    const n = normaliseKey(sc);
                    if (!n.startsWith('mark_') || n.startsWith('markall_')) return true;
                    const tail = n.slice('mark_'.length);
                    const tp = tail.split('_');
                    if (tp.length >= 2 && /^\d{4}$/.test(tp[0]) && /^\d{4}$/.test(tp[1])) {
                        if (`${tp[0]}-${tp[1]}` !== yearHyphen) return true;
                        const restNorm = '_'.join(tp.slice(2));
                        return !restNorm.startsWith(`${subjNorm}_`);
                    }
                    return true;
                });
                selectedColumns.push(key);
            }
            // Starting fresh with "All Marks" (or removing it) clears any course excludes.
            delete filters[umbrella];
            resetPage();
            renderSelectedColumns();
            triggerLoadData();
            return;
        }

        // "All Results" AP umbrella handling (mirrors "All Marks"). Selecting it
        // expands (server-side) into assessment/result wide pairs; unchecking
        // removes them. The subject picker stores 'exclude:<subject>' values on
        // the umbrella key rather than toggling individual columns.
        if (normKey.startsWith('apall_')) {
            const umbrella = apallUmbrellaKeyFromColumn(normKey);
            if (!umbrella) {
                // Malformed key — fall back to plain toggle behavior
                const isSelectedPlain = selectedColumns.some(c => normaliseKey(c) === normKey);
                if (isSelectedPlain) {
                    if (selectedColumns.length <= 1) return;
                    selectedColumns = selectedColumns.filter(c => normaliseKey(c) !== normKey);
                    if (filters[key]) delete filters[key];
                } else {
                    selectedColumns.push(key);
                }
                resetPage();
                renderSelectedColumns();
                triggerLoadData();
                return;
            }
            const year = umbrella.slice('apall_'.length).replace('_', '-');
            const prefix = `${umbrella}_`;
            const isSelected = selectedColumns.some((sc) => {
                const n = normaliseKey(sc);
                return n === umbrella || n.startsWith(prefix);
            });
            if (isSelected) {
                // Uncheck: remove umbrella + expanded pairs
                selectedColumns = selectedColumns.filter((sc) => {
                    const n = normaliseKey(sc);
                    return n !== umbrella && !n.startsWith(prefix);
                });
            } else {
                // Check: unselect individual AP subject PLs for this year, then add umbrella
                selectedColumns = selectedColumns.filter((sc) => {
                    const n = normaliseKey(sc);
                    if (!n.startsWith('ap_') || n.startsWith('apaward_') || n.startsWith('apawards_all_') || n.startsWith('apall_')) {
                        return true;
                    }
                    const tail = n.slice('ap_'.length);
                    const parts = tail.split('_');
                    if (parts.length >= 2 && /^\d{4}$/.test(parts[0]) && /^\d{4}$/.test(parts[1])) {
                        return `${parts[0]}-${parts[1]}` !== year;
                    }
                    return true;
                });
                selectedColumns.push(key);
            }
            // Starting fresh with "All Results" (or removing it) clears subject excludes.
            delete filters[umbrella];
            resetPage();
            renderSelectedColumns();
            triggerLoadData();
            return;
        }

        // Check against all forms of the key
        const isSelected = selectedColumns.some(c => normaliseKey(c) === normKey);
        if (isSelected) {
            if (selectedColumns.length <= 1) return;
            selectedColumns = selectedColumns.filter(c => normaliseKey(c) !== normKey);
            if (filters[key]) delete filters[key];
        } else {
            selectedColumns.push(key);
        }
        resetPage();
        renderSelectedColumns();
        triggerLoadData();
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
            resetPage();
            renderSelectedColumns();
            triggerLoadData();
            return;
        }

        const courseCheckbox = event.target.closest('input[data-markall-course]');
        if (courseCheckbox) {
            handleMarkallCourseToggle(courseCheckbox);
            return;
        }

        const apallSubjectCheckbox = event.target.closest('input[data-apall-subject]');
        if (apallSubjectCheckbox) {
            handleApallSubjectToggle(apallSubjectCheckbox);
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
            triggerLoadData();
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
        updateTopScrollWidth();
        syncTableToTopBar();
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
            resetPage();
            triggerLoadData();
        });

        searchInput.addEventListener('input', debounce(() => {
            if (pastedIdList) clearIdList();
            resetPage();
            triggerLoadData();
        }, 400));
    }

    if (searchIdListClear) {
        searchIdListClear.addEventListener('click', () => {
            clearIdList();
            if (searchInput) {
                searchInput.value = '';
                searchInput.placeholder = 'Name, SSID, ID — or paste a list';
            }
            resetPage();
            triggerLoadData();
        });
    }

    const schoolYearSelect = document.getElementById('school_year');
    if (schoolYearSelect) {
        schoolYearSelect.addEventListener('change', () => {
            if (scheduleFiltersEligible) {
                updateScheduleFilterChrome();
                if (scheduleFilterBarOpen) {
                    refreshScheduleFilterDropdowns({ preserveSelections: false });
                } else {
                    clearScheduleFilterControls();
                }
            }
            resetPage();
            triggerLoadData();
        });
    }

    if (scheduleFiltersEligible) {
        initScheduleMultiSelectDropdowns();
        syncScheduleFilterBarDisplay();
        syncScheduleFilterModeVisibility();

        document.getElementById('schedule-filters-toggle')?.addEventListener('click', () => {
            scheduleFilterBarOpen = !scheduleFilterBarOpen;
            syncScheduleFilterBarDisplay();
            if (scheduleFilterBarOpen) {
                syncScheduleFilterModeVisibility();
                refreshScheduleFilterDropdowns({ preserveSelections: true });
            }
            resetPage();
            triggerLoadData();
        });

        const onScheduleFilterChange = (key) => {
            syncScheduleCourseStaffDisabled();
            refreshScheduleFilterDropdowns({ preserveSelections: true }).then(() => {
                resetPage();
                triggerLoadData();
            });
        };

        const scheduleBar = document.getElementById('schedule-filters-bar');
        scheduleBar?.addEventListener('change', (e) => {
            if (!e.target.matches('[data-schedule-ms-value]')) return;
            const key = e.target.closest('[data-schedule-filter]')?.dataset.scheduleFilter;
            if (!key) return;
            syncScheduleMsLabel(key);
            onScheduleFilterChange(key);
        });
    }

    const statusSelect = document.getElementById('status');
    if (statusSelect) {
        statusSelect.addEventListener('change', () => {
            if (scheduleFiltersEligible && scheduleFilterBarOpen) {
                refreshScheduleFilterDropdowns({ preserveSelections: true });
            }
            resetPage();
            triggerLoadData();
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
            triggerLoadData();
            return;
        }

        const toggleFilterBtn = event.target.closest('[data-action="toggle-filter"]');
        if (toggleFilterBtn) {
            const col = toggleFilterBtn.dataset.column;
            const panel = document.querySelector(`[data-filter-panel="${col}"]`);
            if (panel) panel.classList.toggle('hidden');
            return;
        }

        const clearScheduleFiltersBtn = event.target.closest('[data-action="clear-schedule-filters"]');
        if (clearScheduleFiltersBtn) {
            SCHEDULE_MS_KEYS.forEach((key) => clearScheduleSelect(key));
            syncScheduleCourseStaffDisabled();
            refreshScheduleFilterDropdowns({ preserveSelections: false });
            resetPage();
            triggerLoadData();
            return;
        }

        const clearFilterBtn = event.target.closest('[data-action="clear-filter"]');
        if (clearFilterBtn) {
            const col = clearFilterBtn.dataset.column;
            delete filters[col];
            if (scheduleFiltersEligible && (col === 'school' || col === 'school_state_id')) {
                onSchoolColumnFilterChanged();
            }
            triggerLoadData();
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
            if (scheduleFiltersEligible && (col === 'school' || col === 'school_state_id')) {
                onSchoolColumnFilterChanged();
            }
            triggerLoadData();
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
            triggerLoadData();
            return;
        }

        const quickFilterBtn = event.target.closest('[data-action="quick-filter"]');
        if (quickFilterBtn) {
            const col = quickFilterBtn.dataset.column;
            const val = quickFilterBtn.dataset.value;
            if (val) {
                filters[col] = [val];
            }
            if (scheduleFiltersEligible && (col === 'school' || col === 'school_state_id')) {
                onSchoolColumnFilterChanged();
            }
            triggerLoadData();
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
                if (nextPage > page) {
                    if (nextCursor) cursorStack.push(nextCursor);
                    pendingCursor = nextCursor || '';
                } else if (nextPage < page) {
                    cursorStack.pop();
                    pendingCursor = cursorStack.length ? cursorStack[cursorStack.length - 1] : '';
                } else {
                    pendingCursor = '';
                }
                page = nextPage;
                triggerLoadData();
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
            appendStudentQueryParams(url, { includeEmptyFilters: true });

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

            resetPage();
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
            if (scheduleFiltersEligible) {
                updateScheduleFilterChrome();
                refreshScheduleFilterDropdowns({ preserveSelections: false });
            }

            renderSelectedColumns();
            resetPage();
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

    let _scrollSyncResizeObserver = null;
    let _scrollSyncMainEl = null;
    let _scrollSyncTopScrolling = false;
    let _scrollSyncMainScrolling = false;

    const getTableScrollEl = () => {
        const inner = document.querySelector('#student-table .table-container');
        const outer = document.getElementById('table-scroll-container');
        const candidates = [inner, outer].filter(Boolean);
        for (const el of candidates) {
            if (el.scrollWidth > el.clientWidth + 1) return el;
        }
        return inner || outer;
    };

    const syncTopBarToTable = () => {
        const topScrollBar = document.getElementById('h-scroll-top');
        const scrollEl = getTableScrollEl();
        if (!topScrollBar || !scrollEl || _scrollSyncMainScrolling) return;
        _scrollSyncTopScrolling = true;
        scrollEl.scrollLeft = topScrollBar.scrollLeft;
        updateScrollIndicators();
        window.setTimeout(() => { _scrollSyncTopScrolling = false; }, 50);
    };

    const syncTableToTopBar = () => {
        const topScrollBar = document.getElementById('h-scroll-top');
        const scrollEl = getTableScrollEl();
        if (!topScrollBar || !scrollEl || _scrollSyncTopScrolling) return;
        _scrollSyncMainScrolling = true;
        topScrollBar.scrollLeft = scrollEl.scrollLeft;
        updateScrollIndicators();
        window.setTimeout(() => { _scrollSyncMainScrolling = false; }, 50);
    };

    const onTopScrollBarScroll = () => syncTopBarToTable();
    const onTableScrollElScroll = () => syncTableToTopBar();

    const updateTopScrollWidth = () => {
        const topScrollBar = document.getElementById('h-scroll-top');
        const topScrollContent = document.getElementById('h-scroll-content-top');
        const scrollEl = getTableScrollEl();
        if (!topScrollBar || !topScrollContent || !scrollEl) return;
        // Match the top bar's scroll range to the table scroller's range so a
        // 1:1 scrollLeft sync stays aligned across the full width (including
        // the outer container's horizontal padding).
        const range = scrollEl.scrollWidth - scrollEl.clientWidth;
        topScrollContent.style.width = `${topScrollBar.clientWidth + range}px`;
    };

    const updateScrollIndicators = () => {
        const scrollEl = getTableScrollEl();
        const leftShadow = document.getElementById('scroll-shadow-left');
        const rightShadow = document.getElementById('scroll-shadow-right');
        if (!scrollEl || !leftShadow || !rightShadow) return;
        const scrollLeft = scrollEl.scrollLeft;
        const maxScroll = scrollEl.scrollWidth - scrollEl.clientWidth;
        leftShadow.style.opacity = scrollLeft > 10 ? '1' : '0';
        rightShadow.style.opacity = scrollLeft < maxScroll - 10 ? '1' : '0';
    };

    const initializeScrollSync = () => {
        const scrollEl = getTableScrollEl();
        const topScrollBar = document.getElementById('h-scroll-top');
        const topScrollContent = document.getElementById('h-scroll-content-top');
        if (!scrollEl || !topScrollBar || !topScrollContent) return;

        // Disconnect old resize observer before creating a new one
        if (_scrollSyncResizeObserver) {
            _scrollSyncResizeObserver.disconnect();
            _scrollSyncResizeObserver = null;
        }

        if (_scrollSyncMainEl && _scrollSyncMainEl !== scrollEl) {
            _scrollSyncMainEl.removeEventListener('scroll', onTableScrollElScroll);
        }
        _scrollSyncMainEl = scrollEl;

        if (!topScrollBar.dataset.scrollSyncBound) {
            topScrollBar.addEventListener('scroll', onTopScrollBarScroll);
            topScrollBar.dataset.scrollSyncBound = '1';
        }
        scrollEl.removeEventListener('scroll', onTableScrollElScroll);
        scrollEl.addEventListener('scroll', onTableScrollElScroll);

        // Wait for layout, then set width and observe the scroll container
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                updateTopScrollWidth();
                syncTableToTopBar();
                updateScrollIndicators();
                const table = scrollEl.querySelector('table');
                _scrollSyncResizeObserver = new ResizeObserver(() => {
                    updateTopScrollWidth();
                    syncTableToTopBar();
                    updateScrollIndicators();
                });
                _scrollSyncResizeObserver.observe(scrollEl);
                if (table) _scrollSyncResizeObserver.observe(table);
            });
        });
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
    if (scheduleFiltersEligible) {
        const bar = document.getElementById('schedule-filters-bar');
        if (bar) {
            if (bar.dataset.initialPeriod) setScheduleSelectValues('schedule_period', bar.dataset.initialPeriod);
            if (bar.dataset.initialSubject) setScheduleSelectValues('schedule_subject', bar.dataset.initialSubject);
            if (bar.dataset.initialCourse) setScheduleSelectValues('schedule_course_name', bar.dataset.initialCourse);
            if (bar.dataset.initialStaff) setScheduleSelectValues('schedule_staff', bar.dataset.initialStaff);
        }
        updateScheduleFilterChrome();
        loadData();
    } else {
        loadData();
    }

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
