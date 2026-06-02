---
name: multi-year-enrollment-review
description: Review of multi-year enrollment selection implementation fixes applied on 2026-06-02
metadata:
  type: project
---

Conducted thorough code review of multi-year enrollment selection in the dataviz app on 2026-06-02. Found 3 bugs and applied fixes.

## Bugs Found and Fixed

### Bug 1 (HIGH): Custom years lost on source type switch
**File:** `django_pro/templates/dataviz/charts/builder.html`, `setSource()` method
**Why:** `yearContext` is `''` (falsy) in Custom mode, so `if (yearCtx)` skipped preserving `_custom_years` when resetting `chartFilters`.
**Fix:** Save `_custom_years` before reset, restore to `chartFilters` when `yearContext` is falsy but custom years exist.

### Bug 2 (MEDIUM): `_custom_years` leaked to database
**File:** `django_pro/templates/dataviz/charts/builder.html`, save payload
**Why:** The UI-only `_custom_years` tracking key was included in `JSON.stringify(chartFilters)` sent to the server on every save.
**Fix:** Added `cleanSourceFilters()` method that strips `_custom_years` before serialization. Wired into form input (`x-bind:value`), `_buildConfig()`, and `doSaveAndAttach()`.

### Bug 3 (LOW): Duplicate `yearAliasLabels` declaration
**File:** `django_pro/templates/dataviz/charts/builder.html`, Alpine state init
**Fix:** Removed the redundant `yearAliasLabels: {}` declaration (line 2296) since line 2318 overwrites it with parsed JSON.

## Findings (No Action Needed)

1. **school_filter_mode is metadata-only**: The field on SavedChart stores which mode was active but is never read during query execution. The actual tested-school filtering works via the `tested_school` key in `source_filters`. Not a bug — the field serves as UI state persistence.

2. **Empty custom years deletes year filter**: When all checkboxes are unchecked, `toggleCustomYear` deletes `school_year` from filters entirely, returning all years (capped at 50K rows). Current behavior is acceptable.

3. **Year context dropdown uses `_custom_years` from source_filters**: The init() method correctly syncs `_custom_years` from `school_year` array on load. When `school_year` is an array, `yearContext` is set to `''` (Custom).

## Data Flow Summary

- **Save path**: `yearContext` dropdown → `onYearContextChange()` → `chartFilters.school_year` (alias or list) → `cleanSourceFilters()` strips `_custom_years` → `source_filters` JSON → DB
- **Load path**: `existing.source_filters.school_year` → `init()` syncs `_custom_years` if array → `yearContext = ''` (Custom) or alias
- **Query path**: `source_filters.school_year` → `resolve_school_year()` resolves aliases → `_add_filter('cs.school_year', list)` → `cs.school_year IN (%s, %s, ...)` → `_needs_year_grade` forces enroll join → `COALESCE(se.grade, cs.grade)`
