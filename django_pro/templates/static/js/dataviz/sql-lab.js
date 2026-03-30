/**
 * sql-lab.js — Minor helpers for the SQL lab textarea in chart builder.
 */
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    const sqlTextarea = document.getElementById('id_raw_sql');
    if (!sqlTextarea) return;

    // Tab key inserts 2 spaces instead of moving focus
    sqlTextarea.addEventListener('keydown', function (e) {
      if (e.key === 'Tab') {
        e.preventDefault();
        const start = this.selectionStart;
        const end   = this.selectionEnd;
        this.value  = this.value.substring(0, start) + '  ' + this.value.substring(end);
        this.selectionStart = this.selectionEnd = start + 2;
      }
    });

    // Ctrl+Enter / Cmd+Enter triggers run
    sqlTextarea.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        const runBtn = document.querySelector('[hx-post*="sql/"]');
        if (runBtn) runBtn.click();
      }
    });
  });
}());
