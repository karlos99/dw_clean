        // ──────────────────────────────────────────────────────────────────
        // ── SQL LAB ────────────────────────────────────────────────────────
        // ──────────────────────────────────────────────────────────────────

        const sqlInputEl     = document.getElementById('dv-sql-input');
        const sqlRunBtn      = document.getElementById('dv-sql-run');
        const sqlStatusEl    = document.getElementById('dv-sql-status');
        const sqlHeadEl      = document.getElementById('dv-sql-head');
        const sqlBodyEl      = document.getElementById('dv-sql-body');
        const sqlRowCountEl  = document.getElementById('dv-sql-row-count');
        const sqlCreateChartBtn = document.getElementById('dv-sql-create-chart');
        const sqlTableListEl = document.getElementById('dv-sql-table-list');

        let sqlResultState = { columns: [], colTypes: {}, rows: [] };

        function loadSqlSchema() {
            if (!schemaUrl || !sqlTableListEl) return;
            fetch(schemaUrl, { credentials: 'same-origin' })
                .then((r) => r.json())
                .then((data) => {
                    if (!data.tables) return;
                    sqlTableListEl.innerHTML = '';
                    data.tables.forEach((t) => {
                        const item = document.createElement('div');
                        const isView = t.type === 'VIEW';
                        item.className = 'px-2 py-1 rounded hover:bg-white cursor-pointer text-xs text-slate-700 flex items-center gap-1.5';
                        item.innerHTML = `<span class="text-[10px] ${isView ? 'text-purple-500' : 'text-blue-500'}">${isView ? '⊕' : '▦'}</span>${escapeHtml(t.name)}`;
                        item.addEventListener('click', () => {
                            if (sqlInputEl) {
                                sqlInputEl.value = `SELECT * FROM ${t.name} LIMIT 100;`;
                                sqlInputEl.focus();
                            }
                        });
                        sqlTableListEl.appendChild(item);
                    });
                })
                .catch(() => {});
        }

        function runSqlQuery() {
            if (!sqlUrl || !sqlInputEl) return;
            const sql = sqlInputEl.value.trim();
            if (!sql) return;
            if (sqlStatusEl) sqlStatusEl.textContent = 'Running…';
            if (sqlRunBtn) sqlRunBtn.disabled = true;
            if (sqlCreateChartBtn) sqlCreateChartBtn.classList.add('hidden');

            fetch(sqlUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ sql }),
            })
                .then((r) => r.json())
                .then((data) => {
                    if (sqlRunBtn) sqlRunBtn.disabled = false;
                    if (data.error) {
                        if (sqlStatusEl) sqlStatusEl.textContent = `Error: ${data.error}`;
                        if (sqlHeadEl) sqlHeadEl.innerHTML = '';
                        if (sqlBodyEl) sqlBodyEl.innerHTML = '';
                        if (sqlRowCountEl) sqlRowCountEl.textContent = '';
                        return;
                    }
                    sqlResultState = { columns: data.columns || [], colTypes: data.col_types || {}, rows: data.rows || [] };

                    const cols = data.columns || [];
                    const rows = data.rows || [];

                    if (sqlHeadEl) {
                        sqlHeadEl.innerHTML = '<tr>' + cols.map((c) =>
                            `<th class="px-3 py-1.5 text-left text-[11px] font-bold text-slate-600 whitespace-nowrap border-r border-slate-200 last:border-r-0">${escapeHtml(c)}</th>`
                        ).join('') + '</tr>';
                    }
                    if (sqlBodyEl) {
                        sqlBodyEl.innerHTML = rows.slice(0, 500).map((row) =>
                            '<tr class="hover:bg-slate-50">' + cols.map((c) => {
                                const v = row[c];
                                return `<td class="px-3 py-1 text-[11px] text-slate-700 border-r border-slate-100 last:border-r-0 whitespace-nowrap max-w-[200px] truncate" title="${escapeHtml(String(v ?? ''))}">${escapeHtml(String(v ?? ''))}</td>`;
                            }).join('') + '</tr>'
                        ).join('');
                    }

                    const rowCount = rows.length;
                    const hasMore  = data.has_more;
                    if (sqlStatusEl) sqlStatusEl.textContent = `${rowCount} row${rowCount !== 1 ? 's' : ''} returned${hasMore ? ' (limited)' : ''}`;
                    if (sqlRowCountEl) sqlRowCountEl.textContent = hasMore ? '⚠ Results capped at 2,000 rows.' : '';

                    if (sqlCreateChartBtn && cols.length > 0 && rows.length > 0) {
                        sqlCreateChartBtn.classList.remove('hidden');
                    }
                })
                .catch((err) => {
                    if (sqlRunBtn) sqlRunBtn.disabled = false;
                    if (sqlStatusEl) sqlStatusEl.textContent = `Fetch error: ${err.message}`;
                });
        }

        if (sqlRunBtn) sqlRunBtn.addEventListener('click', runSqlQuery);
        if (sqlInputEl) {
            sqlInputEl.addEventListener('keydown', (e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runSqlQuery(); }
            });
        }

        // "Explore in Chart Builder" — load SQL results into Explore mode
        if (sqlCreateChartBtn) {
            sqlCreateChartBtn.addEventListener('click', () => {
                const { columns, colTypes, rows } = sqlResultState;
                if (!rows.length) return;

                // Inject into state
                state.rows    = rows;
                state.columns = columns;
                state.columnTypes = {};
                state.columnDefinitions = {};
                state.filteredRows = [...rows];
                columns.forEach((c) => {
                    state.columnTypes[c] = colTypes[c] || 'string';
                    state.columnDefinitions[c] = c;
                });

                // Re-trigger the post-load repopulation
                const colCount = columns.length;
                const rowCount = rows.length;

                // Repopulate dropdowns (reuse existing util if available)
                const allCols  = columns.map((c) => [c, c]);
                fillSelect && fillSelect(xColEl, allCols, columns[0] || '', false);
                fillSelect && fillSelect(yColEl, allCols, '', false);
                fillSelect && fillSelect(stackColEl, allCols, '', false);
                fillSelect && fillSelect(sizeColEl, allCols, '', false);
                [xColEl, yColEl, stackColEl, sizeColEl].forEach((el) => { if (el) el.disabled = false; });
                aggEl.disabled = false;
                renderBtn.disabled = false;
                chartTypeEl.disabled = false;

                if (datasetSummaryEl) {
                    datasetSummaryEl.textContent = `SQL: ${rowCount.toLocaleString()} rows × ${colCount} cols`;
                    datasetSummaryEl.classList.remove('hidden');
                }

                buildDimMetricPanels();
                state.chartGroupByCols = [];
                renderGroupByChips();

                // Track that this data came from SQL
                state.sourceType = 'sql';
                state.sourceSql  = sqlInputEl ? sqlInputEl.value.trim() : '';

                switchMode('explore');
            });
        }

