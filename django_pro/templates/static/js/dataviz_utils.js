(function () {
    function isBlankValue(value) {
        if (value === null || value === undefined) {
            return true;
        }
        const normalized = String(value).trim();
        return normalized === '' || normalized === '—' || normalized === '-' || normalized.toLowerCase() === 'n/a';
    }

    function parseJSON(value, fallback) {
        try {
            return JSON.parse(value);
        } catch (_) {
            return fallback;
        }
    }

    function toNumber(value) {
        if (isBlankValue(value)) {
            return null;
        }
        const normalized = typeof value === 'string' ? value.replace(/,/g, '') : value;
        const n = Number(normalized);
        return Number.isFinite(n) ? n : null;
    }

    function inferColumnType(values) {
        let numericCount = 0;
        let sampleCount = 0;

        for (const value of values) {
            if (isBlankValue(value)) {
                continue;
            }
            sampleCount += 1;
            if (toNumber(value) !== null) {
                numericCount += 1;
            }
            if (sampleCount >= 40) {
                break;
            }
        }

        if (sampleCount === 0) {
            return 'string';
        }
        return numericCount / sampleCount >= 0.8 ? 'number' : 'string';
    }

    function normalizeLabel(label) {
        return String(label || '').replace(/\r?\n/g, ' — ');
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    const DATE_COLUMNS = new Set(['birth_date', 'us_enter_date', 'fep_date']);

    function isDateLikeString(value) {
        if (typeof value !== 'string') {
            return false;
        }
        return /^\d{4}-\d{2}-\d{2}(?:[T\s].*)?$/.test(value);
    }

    function formatDateForDisplay(value) {
        if (!isDateLikeString(value)) {
            return value;
        }
        const datePart = String(value).slice(0, 10);
        const dateObj = new Date(`${datePart}T00:00:00`);
        if (Number.isNaN(dateObj.getTime())) {
            return datePart;
        }
        try {
            return new Intl.DateTimeFormat('en-US').format(dateObj);
        } catch (_) {
            return datePart;
        }
    }

    function formatValueForColumn(col, value) {
        if (value === null || value === undefined) {
            return '—';
        }
        if (DATE_COLUMNS.has(col)) {
            return formatDateForDisplay(String(value));
        }
        if (typeof value === 'string' && isDateLikeString(value)) {
            return formatDateForDisplay(value);
        }
        return String(value);
    }

    function fillSelect(selectEl, options, placeholder) {
        if (!selectEl) {
            return;
        }

        const placeholderHtml = placeholder
            ? `<option value="">${placeholder}</option>`
            : '';

        selectEl.innerHTML = placeholderHtml + options.map((opt) => {
            const value = String(opt.value ?? '');
            const label = normalizeLabel(opt.label ?? opt.value ?? '');
            return `<option value="${value}">${label}</option>`;
        }).join('');
    }

    async function readJsonResponse(response, contextLabel) {
        const rawText = await response.text();
        try {
            return JSON.parse(rawText);
        } catch (_) {
            const snippet = String(rawText || '').slice(0, 220).replace(/\s+/g, ' ').trim();
            throw new Error(`${contextLabel} returned non-JSON response${snippet ? `: ${snippet}` : ''}`);
        }
    }

    window.DVUtils = {
        isBlankValue,
        parseJSON,
        toNumber,
        inferColumnType,
        normalizeLabel,
        escapeHtml,
        formatValueForColumn,
        fillSelect,
        readJsonResponse,
    };
})();
