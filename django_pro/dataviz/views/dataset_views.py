"""Visual Dataset views: list, canvas builder, save, delete, preview API."""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST, require_GET
from django.views.generic import TemplateView
from django.db import connection

from dataviz.models import Dataset
from dataviz.utils.compiler import (
    compile_dataset_to_sql, execute_compiled_query,
    COLUMN_WHITELIST, TABLE_WHITELIST,
    generate_column_aliases,
)
from .mixins import ProjectAdminMixin, is_project_admin

logger = logging.getLogger(__name__)

FORBIDDEN = HttpResponseForbidden('Project Admins only.')


@login_required
def dataset_index(request):
    """List all visual datasets."""
    if not is_project_admin(request.user):
        return HttpResponseForbidden('Project Admins only.')

    datasets_qs = Dataset.objects.all().order_by('-updated_at')[:200]
    datasets = list(datasets_qs)
    
    from dataviz.models import SavedChart, DataVizPageSection
    from collections import defaultdict
    
    dataset_ids = [ds.pk for ds in datasets]
    
    charts_using_ds = SavedChart.objects.filter(
        source_type=SavedChart.SOURCE_DATASET,
        source_id__in=dataset_ids
    ).values('id', 'name', 'source_id')
    
    ds_charts = defaultdict(list)
    chart_id_to_ds = {}
    for c in charts_using_ds:
        ds_charts[c['source_id']].append({'id': c['id'], 'name': c['name']})
        chart_id_to_ds[c['id']] = c['source_id']
        
    chart_ids = [c['id'] for c in charts_using_ds]
    sections = DataVizPageSection.objects.filter(
        element_type=DataVizPageSection.TYPE_CHART,
        chart_id__in=chart_ids
    ).select_related('page')
    
    ds_pages = defaultdict(dict)
    for s in sections:
        if s.chart_id in chart_id_to_ds:
            ds_id = chart_id_to_ds[s.chart_id]
            ds_pages[ds_id][s.page.id] = {'id': s.page.id, 'title': s.page.title, 'slug': s.page.slug}
            
    for ds in datasets:
        ds.usage_charts = ds_charts.get(ds.pk, [])
        ds.usage_pages = list(ds_pages.get(ds.pk, {}).values())

    return render(request, 'dataviz/datasets/index.html', {'datasets': datasets})


class DatasetBuilderView(ProjectAdminMixin, TemplateView):
    """Create or edit a visual dataset with the join canvas."""
    template_name = 'dataviz/datasets/builder.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dataset_id = self.kwargs.get('dataset_id')

        existing_dataset = None
        if dataset_id:
            from django.shortcuts import get_object_or_404
            existing_dataset = get_object_or_404(Dataset, pk=dataset_id)

        # Schema metadata for the frontend column/table pickers
        from django.core.cache import cache
        from dataviz.utils.compiler import get_allowed_dataset_tables, get_columns_for_table
        from dataexploration.constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES

        allowed_tables = get_allowed_dataset_tables()
        tables = sorted(list(allowed_tables.keys()))

        try:
            schema_json = cache.get('dataviz:schema_json')
        except Exception:
            schema_json = None

        if schema_json is None:
            schema_json = {table: sorted(list(get_columns_for_table(table))) for table in tables}
            try:
                cache.set('dataviz:schema_json', schema_json, 3600)
            except Exception:
                pass

        # Build a flat list of explorer column options grouped by category
        explorer_groups = [
            {
                'title': cat['title'],
                'columns': [
                    {'key': k, 'label': COLUMN_DEFINITIONS.get(k, k)}
                    for k in cat['columns']
                    if k in COLUMN_DEFINITIONS
                ],
            }
            for cat in COLUMN_CATEGORIES.values()
        ]

        # Add dynamic assessment columns (test_* and mark_*)
        try:
            from dataexploration.column_utils import get_test_columns, get_mark_columns

            test_cols = get_test_columns()
            for year in sorted(test_cols.keys(), reverse=True):
                for assessment_name, assess_data in sorted(test_cols[year].items()):
                    cols = []
                    windows = assess_data.get('windows', {})
                    for window_name, subjects in sorted(windows.items()):
                        for testing_name, metrics in sorted(subjects.items()):
                            ss_key = metrics.get('SS')
                            pl_key = metrics.get('PL')
                            label_prefix = f"{testing_name} {window_name}".strip() if window_name else testing_name
                            if ss_key:
                                cols.append({'key': ss_key, 'label': f"{label_prefix} SS"})
                            if pl_key:
                                cols.append({'key': pl_key, 'label': f"{label_prefix} PL"})
                    add_info = assess_data.get('additional_info', {})
                    for window_name, subjects in sorted(add_info.items()):
                        for testing_name, entries in sorted(subjects.items()):
                            for entry in (entries or []):
                                cols.append({'key': entry['key'], 'label': entry['label']})
                    if cols:
                        explorer_groups.append({'title': f"{assessment_name} ({year})", 'columns': cols})

            mark_cols = get_mark_columns()
            # Marks can be very numerous; include only the most recent year, capped at 500.
            recent_mark_years = sorted(mark_cols.keys(), reverse=True)[:1]
            for year in recent_mark_years:
                cols = []
                for subject, courses in sorted(mark_cols[year].items()):
                    for course, periods in sorted(courses.items()):
                        for period, key in sorted(periods.items()):
                            cols.append({'key': key, 'label': f"{course} ({subject}) {period}"})
                            if len(cols) >= 500:
                                break
                        if len(cols) >= 500:
                            break
                    if len(cols) >= 500:
                        break
                if cols:
                    explorer_groups.append({'title': f"Marks ({year})", 'columns': cols})
        except Exception:
            logger.exception("Failed to load assessment columns for dataset builder")

        context.update({
            'dataset': existing_dataset,
            'schema_json': json.dumps(schema_json),
            'tables': tables,
            'explorer_groups_json': json.dumps(explorer_groups),
        })
        return context


dataset_builder = DatasetBuilderView.as_view()


# ── API endpoints ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def dataset_save_api(request):
    """Create or update a visual dataset (JSON POST body)."""
    if not is_project_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)

    try:
        data = json.loads(request.body)
        name = data.get('name')
        description = data.get('description', '')
        base_table = data.get('base_table')
        distinct = data.get('distinct', True)
        joins = data.get('joins', [])
        filters = data.get('filters', [])
        columns = data.get('columns', [])

        if not name or not base_table or not columns:
            return JsonResponse({'success': False, 'error': 'Missing required fields (Name, Base Table, Columns)'}, status=400)

        ds = Dataset(owner=request.user)
        ds.name = name
        ds.description = description
        ds.base_table = base_table
        ds.distinct = distinct
        ds.joins = joins
        ds.filters = filters
        ds.columns = columns
        # Auto-generate clean aliases; user-provided overrides in the request body
        # are preserved — generate_column_aliases only fills in unmapped columns.
        user_mappings = data.get('column_mappings', {}) or {}
        ds.column_mappings = generate_column_aliases(columns, user_mappings)

        sql, params = compile_dataset_to_sql(ds, user=request.user)
        ds.sql_preview = sql
        ds.save()
        return JsonResponse({'success': True, 'id': ds.pk, 'column_mappings': ds.column_mappings})
    except Exception as e:
        logger.exception("Error saving dataset")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
def dataset_detail_api(request, dataset_id):
    """GET a dataset's full dict; PUT to update; DELETE to remove."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    from django.shortcuts import get_object_or_404 as g4
    ds = g4(Dataset, pk=dataset_id)

    if request.method == 'GET':
        return JsonResponse(ds.to_dict())

    if request.method == 'DELETE':
        ds.delete()
        return JsonResponse({'success': True})

    if request.method in ('POST', 'PUT'):
        try:
            data = json.loads(request.body)
            name = data.get('name')
            description = data.get('description', '')
            base_table = data.get('base_table')
            distinct = data.get('distinct', True)
            joins = data.get('joins', [])
            filters = data.get('filters', [])
            columns = data.get('columns', [])

            if not name or not base_table or not columns:
                return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

            ds.name = name
            ds.description = description
            ds.base_table = base_table
            ds.distinct = distinct
            ds.joins = joins
            ds.filters = filters
            ds.columns = columns
            # Re-generate aliases when the column list changes; user-provided
            # overrides in the request body are preserved.
            user_mappings = data.get('column_mappings', ds.column_mappings or {}) or {}
            ds.column_mappings = generate_column_aliases(columns, user_mappings)

            sql, params = compile_dataset_to_sql(ds, user=request.user)
            ds.sql_preview = sql
            ds.save()
            return JsonResponse({'success': True, 'id': ds.pk, 'column_mappings': ds.column_mappings})
        except Exception as e:
            logger.exception("Error updating dataset")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
@require_GET
def dataset_list_api(request):
    """List datasets as JSON for pickers."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    datasets = Dataset.objects.all().order_by('name')[:200]
    return JsonResponse({'datasets': [ds.to_dict() for ds in datasets]})


@login_required
@require_POST
def dataset_duplicate_api(request, dataset_id):
    """Duplicate a saved dataset."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    from django.shortcuts import get_object_or_404
    ds = get_object_or_404(Dataset, pk=dataset_id)
    
    ds.pk = None
    ds.id = None
    ds.name = f"{ds.name} (Copy)"
    ds.save()
    
    return JsonResponse({'dataset': ds.to_dict(), 'success': True})


@login_required
@require_POST
def dataset_preview_api(request):
    """Compile a transient dataset and return an HTML table preview."""
    if not is_project_admin(request.user):
        return HttpResponseForbidden("Access Denied.")

    try:
        data = json.loads(request.body)
        dataset = Dataset(
            base_table=data.get('base_table'),
            distinct=data.get('distinct', True),
            joins=data.get('joins', []),
            filters=data.get('filters', []),
            columns=data.get('columns', [])
        )
        sql, params = compile_dataset_to_sql(dataset, user=request.user, limit=51)
        res = execute_compiled_query(sql, params)

        if res.get('error'):
            return render(request, 'dataviz/partials/error.html', {'error': res['error']})

        rows = res.get('rows', [])[:50]
        columns = res.get('columns', [])

        context = {
            'columns': columns,
            'rows': rows,
            'total_rows': res.get('row_count', 0),
            'has_more': res.get('row_count', 0) > 50,
        }
        return render(request, 'dataviz/datasets/partials/preview_table.html', context)
    except Exception as e:
        return render(request, 'dataviz/partials/error.html', {'error': str(e)})


@login_required
@require_POST
def dataset_export_csv_api(request):
    """Compile a transient dataset and return all rows as a streamed CSV download."""
    if not is_project_admin(request.user):
        return HttpResponseForbidden("Access Denied.")

    try:
        data = json.loads(request.body)
        dataset = Dataset(
            base_table=data.get('base_table'),
            distinct=data.get('distinct', True),
            joins=data.get('joins', []),
            filters=data.get('filters', []),
            columns=data.get('columns', [])
        )
        sql, params = compile_dataset_to_sql(dataset, user=request.user, limit=None)
        res = execute_compiled_query(sql, params)

        if res.get('error'):
            from django.http import HttpResponse
            return HttpResponse(res['error'], status=400)

        rows = res.get('rows', [])
        columns = res.get('columns', [])

        from django.http import StreamingHttpResponse
        def stream_csv():
            yield ','.join(f'"{str(c).replace(chr(34), chr(34)+chr(34))}"' for c in columns) + '\n'
            for row in rows:
                yield ','.join(
                    f'"{str(row.get(c, "")).replace(chr(34), chr(34)+chr(34))}"'
                    if row.get(c) is not None else '""'
                    for c in columns
                ) + '\n'

        response = StreamingHttpResponse(stream_csv(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="dataset_export.csv"'
        return response
    except Exception as e:
        from django.http import HttpResponse
        return HttpResponse(str(e), status=400)


@login_required
@require_GET
def api_column_unique_values(request):
    """Returns JSON list of unique values for a given table.column."""
    if not is_project_admin(request.user):
        return HttpResponseForbidden("Access Denied.")

    column_str = request.GET.get('column')
    if not column_str:
        return JsonResponse({'values': []})

    try:
        parts = column_str.split('.')
        if len(parts) != 2:
            return JsonResponse({'values': []}, status=400)

        table_name, col_name = parts
        if table_name not in TABLE_WHITELIST or table_name not in COLUMN_WHITELIST:
            return JsonResponse({'values': []}, status=400)
        if col_name not in COLUMN_WHITELIST[table_name]:
            return JsonResponse({'values': []}, status=400)

        from django.core.cache import cache
        cache_key = f'dataviz:unique_vals:{table_name}.{col_name}'
        try:
            values = cache.get(cache_key)
        except Exception:
            values = None

        if values is None:
            sql = f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL AND "{col_name}" != %s ORDER BY "{col_name}" LIMIT 500'
            from clickhouse_utils.executor import QueryExecutor, CLICKHOUSE_SYNCED_TABLES
            if table_name in CLICKHOUSE_SYNCED_TABLES:
                with QueryExecutor().cursor() as cursor:
                    cursor.execute(sql, [''])
                    rows = cursor.fetchall()
            else:
                with connection.cursor() as cursor:
                    cursor.execute("BEGIN READ ONLY")
                    try:
                        cursor.execute(sql, [''])
                        rows = cursor.fetchall()
                    finally:
                        cursor.execute("COMMIT")

            values = [str(r[0]) for r in rows if r[0] is not None]
            try:
                cache.set(cache_key, values, 300)
            except Exception:
                pass

        return JsonResponse({'values': values})
    except Exception as e:
        logger.exception("Error fetching unique values")
        return JsonResponse({'values': [], 'error': str(e)}, status=500)


@login_required
@require_GET
def dataset_filter_options_api(request, dataset_id):
    """Return filter options ({column: [values], labels: {column: label}}) for a dataset.

    Compiles and runs the dataset SQL (with any active current_filters applied)
    and extracts distinct values per aliased column from the result rows.
    This correctly handles JSONB columns and respects dataset joins/filters.
    Accepts ?current_filters=<JSON> to scope values by active filters (cascading).
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    from django.shortcuts import get_object_or_404 as g4
    from django.core.cache import cache
    import hashlib
    from dataviz.utils.compiler import (
        compile_dataset_to_sql, execute_compiled_query,
        apply_column_aliases, resolve_dataset_filters,
        generate_column_aliases,
    )
    ds = g4(Dataset, pk=dataset_id)

    current_filters_raw = request.GET.get('current_filters', '')
    current_filters = {}
    if current_filters_raw:
        try:
            current_filters = json.loads(current_filters_raw)
        except (json.JSONDecodeError, ValueError):
            pass

    # Resolve alias-format filter keys to table.column for compile_dataset_to_sql
    active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
    resolved_filters = resolve_dataset_filters(current_filters, ds, active_tables) if current_filters else {}

    # Cache key: dataset id + updated_at + resolved filters hash
    filters_hash = hashlib.md5(json.dumps(resolved_filters, sort_keys=True).encode()).hexdigest()[:10]
    updated_ts = int(ds.updated_at.timestamp()) if ds.updated_at else 0
    cache_key = f'dataviz:ds_filter_opts:{ds.pk}:{updated_ts}:{filters_hash}'
    try:
        cached = cache.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        return JsonResponse(cached)

    try:
        col_mappings = ds.column_mappings or generate_column_aliases(ds.columns or [], {})
        _SKIP = frozenset({'ssid', 'student_id', 'stu_annual_key', 'student_name'})
        _DISTINCT_LIMIT = 500  # max distinct values per filter column

        # Use the ClickHouse-aware executor only when every table this dataset
        # touches is actually mirrored into ClickHouse — otherwise the data
        # simply isn't there and Postgres remains the correct source.
        from clickhouse_utils.executor import QueryExecutor, CLICKHOUSE_SYNCED_TABLES
        use_ch = active_tables <= CLICKHOUSE_SYNCED_TABLES
        cursor_ctx = QueryExecutor().cursor() if use_ch else connection.cursor()

        filter_options = {}
        with cursor_ctx as cur:
            for sql_col, alias in col_mappings.items():
                if alias in _SKIP:
                    continue
                # Exclude the filter for the current column/alias from interactive filters
                col_filters = {
                    fk: fval for fk, fval in resolved_filters.items()
                    if not (fk == sql_col or fk.startswith(sql_col + '->'))
                }
                # Compile base SQL with the excluded filters
                sql, params = compile_dataset_to_sql(ds, user=request.user, interactive_filters=col_filters, limit=1)
                # Strip the trailing LIMIT clause so the subquery is unlimited
                sql_base = sql.rsplit(' LIMIT ', 1)[0]

                # Quote the sql_col for use as column reference in the outer SELECT
                quoted = f'"{sql_col}"'
                distinct_sql = (
                    f'SELECT DISTINCT {quoted} AS val '
                    f'FROM ({sql_base}) _q '
                    f'WHERE {quoted} IS NOT NULL AND CAST({quoted} AS TEXT) != \'\' '
                    f'ORDER BY 1 LIMIT {_DISTINCT_LIMIT}'
                )
                try:
                    cur.execute(distinct_sql, params)
                    vals = [str(row[0]) for row in cur.fetchall() if row[0] is not None]
                    if vals:
                        filter_options[alias] = vals
                except Exception:
                    pass  # skip columns that error (e.g. non-comparable types)

        filter_labels = {k: k for k in filter_options}
        result = {'filter_options': filter_options, 'filter_labels': filter_labels}
        try:
            cache.set(cache_key, result, 300)
        except Exception:
            pass
        return JsonResponse(result)
    except Exception:
        logger.exception("Error computing dataset filter options")
        return JsonResponse({'filter_options': {}, 'filter_labels': {}})


# ── Legacy per-column endpoint (kept for backward compat) ────────────────────

@login_required
@require_GET
def _dataset_filter_options_legacy(request, dataset_id):
    """Legacy per-column filter-options implementation (kept but no longer used)."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    from django.shortcuts import get_object_or_404 as g4
    ds = g4(Dataset, pk=dataset_id)

    # Parse optional current filter context
    current_filters_raw = request.GET.get('current_filters', '')
    current_filters = {}
    if current_filters_raw:
        try:
            current_filters = json.loads(current_filters_raw)
        except (json.JSONDecodeError, ValueError):
            pass

    filter_options = {}
    filter_labels = {}

    for col_str in ds.columns or []:
        parts = col_str.split('.')
        if len(parts) != 2:
            continue
        table_name, col_name = parts
        if table_name not in TABLE_WHITELIST or col_name not in COLUMN_WHITELIST.get(table_name, set()):
            continue

        from django.core.cache import cache
        import hashlib
        filter_hash = hashlib.md5(json.dumps(current_filters, sort_keys=True).encode()).hexdigest()[:8] if current_filters else ''
        cache_key = f'dataviz:unique_vals:{table_name}.{col_name}:{filter_hash}'
        try:
            values = cache.get(cache_key)
        except Exception:
            values = None

        if values is None:
            try:
                where_parts = []
                where_params = []
                # Apply current active filters to scope the distinct values
                for fcol, fval in current_filters.items():
                    if not fval:
                        continue
                    if fcol == col_str:
                        continue
                    fparts = fcol.split('.')
                    if len(fparts) != 2:
                        continue
                    ftbl, fname = fparts
                    if ftbl != table_name:
                        continue
                    if ftbl not in TABLE_WHITELIST or fname not in COLUMN_WHITELIST.get(ftbl, set()):
                        continue
                    if isinstance(fval, list):
                        vals = [v for v in fval if v]
                        if not vals:
                            continue
                        has_blank = '(Blank)' in vals
                        others = [v for v in vals if v != '(Blank)']
                        parts_list = []
                        if others:
                            ph = ', '.join(['%s'] * len(others))
                            parts_list.append(f'"{ftbl}"."{fname}" IN ({ph})')
                            where_params.extend(others)
                        if has_blank:
                            parts_list.append(f'("{ftbl}"."{fname}" IS NULL OR "{ftbl}"."{fname}" = \'\')')
                        if len(parts_list) > 1:
                            where_parts.append(f"({' OR '.join(parts_list)})")
                        elif parts_list:
                            where_parts.append(parts_list[0])
                    else:
                        if fval == '(Blank)':
                            where_parts.append(f'("{ftbl}"."{fname}" IS NULL OR "{ftbl}"."{fname}" = \'\')')
                        else:
                            where_parts.append(f'"{ftbl}"."{fname}" = %s')
                            where_params.append(fval)

                if where_parts:
                    where_sql = 'WHERE ' + ' AND '.join(where_parts) + ' AND'
                else:
                    where_sql = 'WHERE'

                sql = f'SELECT DISTINCT "{col_name}" FROM "{table_name}" {where_sql} "{col_name}" IS NOT NULL AND "{col_name}" != %s ORDER BY "{col_name}" LIMIT 200'
                all_params = where_params + ['']
                with connection.cursor() as cursor:
                    cursor.execute("BEGIN READ ONLY")
                    try:
                        cursor.execute(sql, all_params)
                        rows = cursor.fetchall()
                    finally:
                        cursor.execute("COMMIT")
                values = [str(r[0]) for r in rows if r[0] is not None]
            except Exception:
                values = []
            try:
                cache.set(cache_key, values, 300)
            except Exception:
                pass

        filter_options[col_str] = values
        filter_labels[col_str] = col_str

    return JsonResponse({'filter_options': filter_options, 'filter_labels': filter_labels})

