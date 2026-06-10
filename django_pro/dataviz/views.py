import hashlib
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.db import transaction

from dataexploration.models import SavedQuery
from dataexploration.utils import get_user_roles, can_user_view_query
from users.models_pages import Page as SitePage
from .models import Dataset, SavedChart, DataVizPage, DataVizPageSection
from .utils.compiler import compile_dataset_to_sql, compile_aggregated_dataset_to_sql, execute_compiled_query, COLUMN_WHITELIST, TABLE_WHITELIST
from .utils.chart_aggregator import aggregate_chart_data
from django.core.cache import cache

logger = logging.getLogger(__name__)


def is_user_admin(user):
    """Check if the user is a Project Admin, District Admin, or Superuser."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    roles = get_user_roles(user)
    return bool(roles & {'Project Admin', 'District Admin'})


def get_object_or_04copy(klass, *args, **kwargs):
    """Helper to fetch object or raise 404/return None."""
    try:
        return klass.objects.get(*args, **kwargs)
    except klass.DoesNotExist:
        return None


@login_required
def dashboard_home(request):
    """Landing dashboard page for the dataviz app."""
    if not is_user_admin(request.user):
        return HttpResponseForbidden("You do not have permission to access the Visualizer Admin.")
        
    if request.user.is_superuser:
        dataset_count = Dataset.objects.count()
        chart_count = SavedChart.objects.count()
        page_count = DataVizPage.objects.count()
    else:
        dataset_count = Dataset.objects.filter(owner=request.user).count()
        chart_count = SavedChart.objects.filter(owner=request.user).count()
        page_count = DataVizPage.objects.filter(owner=request.user).count()
    
    context = {
        'dataset_count': dataset_count,
        'chart_count': chart_count,
        'page_count': page_count,
    }
    return render(request, 'dataviz/dashboard.html', context)


# ─── Dataset Views ────────────────────────────────────────────────────────────

@login_required
def dataset_list(request):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
    datasets = Dataset.objects.all() if request.user.is_superuser else Dataset.objects.filter(owner=request.user)
    return render(request, 'dataviz/datasets/list.html', {'datasets': datasets})


@login_required
def dataset_builder(request, dataset_id=None):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
        
    dataset = None
    if dataset_id:
        dataset = get_object_or_04copy(Dataset, pk=dataset_id)
        if not dataset:
            return redirect('dataviz:dataset_list')
        if not request.user.is_superuser and dataset.owner != request.user:
            return HttpResponseForbidden("Access Denied.")
            
    # Serialize metadata schema for frontend dropdown lists (cached — it's static)
    from django.core.cache import cache
    try:
        schema_json = cache.get('dataviz:schema_json')
    except Exception:
        logger.warning("Cache unavailable", exc_info=True)
        schema_json = None
    if schema_json is None:
        schema_json = {table: sorted(list(cols)) for table, cols in COLUMN_WHITELIST.items()}
        try:
            cache.set('dataviz:schema_json', schema_json, None)  # no expiry
        except Exception:
            logger.warning("Cache unavailable for write", exc_info=True)

    context = {
        'dataset': dataset,
        'schema_json': json.dumps(schema_json),
        'tables': sorted(list(TABLE_WHITELIST.keys())),
    }
    return render(request, 'dataviz/datasets/builder.html', context)


@login_required
@require_POST
def dataset_save(request, dataset_id=None):
    if not is_user_admin(request.user):
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
            
        dataset = None
        if dataset_id:
            dataset = get_object_or_04copy(Dataset, pk=dataset_id)
            if not dataset:
                return JsonResponse({'success': False, 'error': 'Dataset not found.'}, status=444)
            if not request.user.is_superuser and dataset.owner != request.user:
                return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        else:
            dataset = Dataset(owner=request.user)
            
        dataset.name = name
        dataset.description = description
        dataset.base_table = base_table
        dataset.distinct = distinct
        dataset.joins = joins
        dataset.filters = filters
        dataset.columns = columns
        
        # Test compile to verify validity and store sql_preview
        sql, params = compile_dataset_to_sql(dataset, user=request.user)
        dataset.sql_preview = sql
        
        dataset.save()
        return JsonResponse({'success': True, 'id': dataset.pk})
    except Exception as e:
        logger.exception("Error saving dataset")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def dataset_delete(request, dataset_id):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
    dataset = get_object_or_04copy(Dataset, pk=dataset_id)
    if dataset:
        if request.user.is_superuser or dataset.owner == request.user:
            dataset.delete()
    return redirect('dataviz:dataset_list')


@login_required
@require_POST
def api_dataset_preview(request):
    """Generates an HTML table preview of compiled query data."""
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
        
    try:
        data = json.loads(request.body)
        # Create a transient dataset for compilation test
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
            
        rows = res.get('rows', [])[:50]  # Limit to 50 for preview
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


# ─── Chart Views ──────────────────────────────────────────────────────────────

@login_required
def chart_list(request):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
    charts = SavedChart.objects.all() if request.user.is_superuser else SavedChart.objects.filter(owner=request.user)
    return render(request, 'dataviz/charts/list.html', {'charts': charts})


@login_required
def chart_builder(request, chart_id=None):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
        
    chart = None
    if chart_id:
        chart = get_object_or_04copy(SavedChart, pk=chart_id)
        if not chart:
            return redirect('dataviz:chart_list')
        if not request.user.is_superuser and chart.owner != request.user:
            return HttpResponseForbidden("Access Denied.")
            
    datasets = Dataset.objects.filter(owner=request.user) if not request.user.is_superuser else Dataset.objects.all()
    if request.user.is_superuser:
        queries = SavedQuery.objects.filter(is_active=True, query_type='filter')
    else:
        from django.db.models import Q
        from dataexploration.utils import get_user_folders
        user_folders = get_user_folders(request.user)
        queries = SavedQuery.objects.filter(
            Q(owner=request.user) |
            Q(privacy='public') |
            Q(shares__shared_with=request.user) |
            Q(folder__in=user_folders),
            is_active=True,
            query_type='filter'
        ).distinct()
    
    context = {
        'chart': chart,
        'datasets': datasets,
        'queries': queries,
    }
    return render(request, 'dataviz/charts/builder.html', context)


@login_required
@require_POST
def chart_save(request, chart_id=None):
    if not is_user_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        
    try:
        data = json.loads(request.body)
        name = data.get('name')
        description = data.get('description', '')
        chart_type = data.get('chart_type', 'bar')
        source_type = data.get('source_type', 'dataset')
        
        dataset_id = data.get('dataset_id')
        query_id = data.get('query_id')
        source_sql = data.get('source_sql', '')
        
        config = data.get('config', {})
        
        if not name or not source_type:
            return JsonResponse({'success': False, 'error': 'Name and Source Type are required.'}, status=400)
            
        chart = None
        if chart_id:
            chart = get_object_or_04copy(SavedChart, pk=chart_id)
            if not chart:
                return JsonResponse({'success': False, 'error': 'Chart not found.'}, status=444)
            if not request.user.is_superuser and chart.owner != request.user:
                return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        else:
            chart = SavedChart(owner=request.user)
            
        chart.name = name
        chart.description = description
        chart.chart_type = chart_type
        chart.source_type = source_type
        chart.source_sql = source_sql
        chart.config = config
        
        if source_type == 'dataset' and dataset_id:
            chart.dataset = Dataset.objects.get(pk=dataset_id)
            chart.query_id = None
        elif source_type == 'query' and query_id:
            chart.query_id = int(query_id)
            chart.dataset = None
        else:
            chart.dataset = None
            chart.query_id = None
            
        chart.save()
        return JsonResponse({'success': True, 'id': chart.pk})
    except Exception as e:
        logger.exception("Error saving chart")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def chart_delete(request, chart_id):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
    chart = get_object_or_04copy(SavedChart, pk=chart_id)
    if chart:
        if request.user.is_superuser or chart.owner == request.user:
            chart.delete()
    return redirect('dataviz:chart_list')


@login_required
def api_chart_columns(request):
    """Returns a JSON list of available column names for the selected source."""
    source_type = request.GET.get('source_type')
    source_id = request.GET.get('source_id')
    
    if not source_type or not source_id:
        return JsonResponse({'columns': []})
        
    try:
        if source_type == 'dataset':
            dataset = Dataset.objects.get(pk=source_id)
            # Returns a list of 'table_name.column_name' strings
            return JsonResponse({'columns': dataset.columns or []})
        elif source_type == 'query':
            query = SavedQuery.objects.get(pk=source_id)
            if not can_user_view_query(request.user, query):
                return JsonResponse({'columns': [], 'error': 'Access denied'}, status=403)
            # From dataexploration config (usually stored in config['columns'])
            return JsonResponse({'columns': query.config.get('columns', [])})
    except Exception as e:
        return JsonResponse({'columns': [], 'error': str(e)}, status=400)
    return JsonResponse({'columns': []})


@login_required
def api_chart_preview(request):
    """Executes source queries, aggregates data, and returns the ECharts option payload."""
    # Accept both POST (form-encoded) and GET for backward compat
    params = request.POST if request.method == 'POST' else request.GET
    source_type = params.get('source_type')
    source_id = params.get('source_id')
    chart_type = params.get('chart_type', 'bar')
    x_col = params.get('x_col')
    y_col = params.get('y_col')
    aggregation = params.get('aggregation', 'count')
    series_col = params.get('series_col')
    
    # Custom config options can be passed as query params or body
    custom_config = {
        'title_custom': params.get('title_custom', ''),
        'x_title': params.get('x_title', ''),
        'y_title': params.get('y_title', ''),
        'show_legend': params.get('show_legend', 'true').lower() == 'true',
        'legend_position': params.get('legend_position', 'horizontal'),
        'show_gridlines': params.get('show_gridlines', 'true').lower() == 'true',
        'stacked': params.get('stacked', 'false').lower() == 'true',
        'variation': params.get('variation', 'standard'),
        'sort_order': params.get('sort_order', 'asc'),
        'value_prefix': params.get('value_prefix', ''),
        'value_suffix': params.get('value_suffix', ''),
        'show_tooltip': params.get('show_tooltip', 'true').lower() == 'true',
        'tooltip_show_percent': params.get('tooltip_show_percent', 'false').lower() == 'true',
        'stacked_percent': params.get('stacked_percent', 'false').lower() == 'true',
        'bar_gap': params.get('bar_gap') or None,
        'bar_category_gap': params.get('bar_category_gap') or None,
    }

    # Optional colors override (passed as comma-separated hex list)
    colors_str = params.get('colors')
    if colors_str:
        custom_config['colors'] = [c.strip() for c in colors_str.split(',') if c.strip()]

    # Value-level color and label mappings
    value_colors_str = params.get('value_colors')
    if value_colors_str:
        try:
            custom_config['value_colors'] = json.loads(value_colors_str)
        except:
            custom_config['value_colors'] = {}

    value_labels_str = params.get('value_labels')
    if value_labels_str:
        try:
            custom_config['value_labels'] = json.loads(value_labels_str)
        except:
            custom_config['value_labels'] = {}

    # Custom value ordering
    custom_order_str = params.get('custom_order')
    if custom_order_str:
        try:
            custom_config['custom_order'] = json.loads(custom_order_str)
        except:
            custom_config['custom_order'] = []
        
    # Extra filters from the layout page
    extra_filters = {}
    filters_json = params.get('extra_filters')
    if filters_json:
        try:
            extra_filters = json.loads(filters_json)
        except:
            pass

    if not source_type or not source_id or not x_col or not y_col:
        return JsonResponse({'error': 'Missing required preview parameters.'}, status=400)
        
    try:
        rows = []
        pre_aggregated = False
        if source_type == 'dataset':
            dataset = Dataset.objects.get(pk=source_id)

            # Apply page level dynamic overrides if any match the query columns
            if extra_filters:
                merged_filters = list(dataset.filters or [])
                for col_name, val in extra_filters.items():
                    if val:
                        merged_filters = [f for f in merged_filters if f.get('column') != col_name]
                        merged_filters.append({
                            'column': col_name,
                            'operator': 'in' if isinstance(val, list) else 'exact',
                            'value': val
                        })
                dataset.filters = merged_filters

            # Push aggregation to SQL for simple cases (reduces data transfer dramatically)
            use_sql_agg = (
                aggregation in ('count', 'sum', 'avg', 'min', 'max')
                and not custom_config.get('stacked_percent', False)
                and ',' not in x_col  # single x-axis column only
            )
            if use_sql_agg:
                group_by_cols = [x_col]
                if series_col:
                    group_by_cols.append(series_col)
                try:
                    sql, params = compile_aggregated_dataset_to_sql(
                        dataset, group_by_cols, y_col, aggregation,
                        series_col=series_col if series_col else None,
                        user=request.user
                    )
                    res = execute_compiled_query(sql, params)
                    if not res.get('error'):
                        rows = res.get('rows', [])
                        pre_aggregated = True
                except Exception:
                    logger.warning("SQL aggregation failed, falling back to Python", exc_info=True)

            if not pre_aggregated:
                sql, params = compile_dataset_to_sql(dataset, user=request.user)
                res = execute_compiled_query(sql, params)
                if res.get('error'):
                    return JsonResponse({'error': res['error']}, status=400)
                rows = res.get('rows', [])

        elif source_type == 'query':
            from dataexploration.utils import can_user_view_query
            from dataviz.utils.data_loader import run_saved_query
            query = SavedQuery.objects.get(pk=source_id)
            if not can_user_view_query(request.user, query):
                return JsonResponse({'error': 'Access denied'}, status=403)

            res = run_saved_query(
                query_id=query.pk,
                extra_filters=extra_filters,
                user=request.user,
                apply_user_filters=True
            )
            if res.get('error'):
                return JsonResponse({'error': res['error']}, status=400)
            rows = res.get('rows', [])

        # Cache key for whole chart preview response
        user_role = getattr(getattr(request.user, 'active_role', None), 'name', 'None')
        school_id = getattr(getattr(request.user, 'active_school', None), 'state_id', 'All')
        cache_parts = json.dumps({
            'st': source_type, 'si': source_id, 'ct': chart_type,
            'x': x_col, 'y': y_col, 'a': aggregation, 's': series_col,
            'cc': custom_config, 'ef': extra_filters, 'pa': pre_aggregated,
        }, sort_keys=True, default=str)
        cache_key = f"dv:chart_preview:{hashlib.md5(cache_parts.encode()).hexdigest()}:u{request.user.pk}:r{user_role}:s{school_id}"
        try:
            options = cache.get(cache_key)
        except Exception:
            options = None
        if options is None:
            # Aggregate row items into ECharts JSON
            options = aggregate_chart_data(
                rows=rows,
                chart_type=chart_type,
                x_col=x_col,
                y_col=y_col,
                aggregation=aggregation,
                series_col=series_col if series_col else None,
                custom_config=custom_config,
                pre_aggregated=pre_aggregated
            )
            try:
                cache.set(cache_key, options, 180)
            except Exception:
                pass
        return JsonResponse(options)
    except Exception as e:
        logger.exception("Error generating chart preview JSON")
        return JsonResponse({'error': str(e)}, status=400)


# ─── Page Views ───────────────────────────────────────────────────────────────

@login_required
def page_list(request):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
    pages = DataVizPage.objects.all() if request.user.is_superuser else DataVizPage.objects.filter(owner=request.user)
    return render(request, 'dataviz/pages/list.html', {'pages': pages})


@login_required
def page_builder(request, page_id=None):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
        
    page = None
    if page_id:
        page = get_object_or_04copy(DataVizPage, pk=page_id)
        if not page:
            return redirect('dataviz:page_list')
        if not request.user.is_superuser and page.owner != request.user:
            return HttpResponseForbidden("Access Denied.")
            
    charts = SavedChart.objects.filter(owner=request.user) if not request.user.is_superuser else SavedChart.objects.all()
    
    # Retrieve available columns from COLUMN_WHITELIST for configuring page filters (cached)
    try:
        schema_json = cache.get('dataviz:schema_json')
    except Exception:
        logger.warning("Cache unavailable", exc_info=True)
        schema_json = None
    if schema_json is None:
        schema_json = {table: sorted(list(cols)) for table, cols in COLUMN_WHITELIST.items()}
        try:
            cache.set('dataviz:schema_json', schema_json, None)  # no expiry
        except Exception:
            logger.warning("Cache unavailable for write", exc_info=True)

    sections_json = "[]"
    if page:
        sections_json = json.dumps([s.to_dict() for s in page.sections.all()])
        
    context = {
        'page': page,
        'charts': charts,
        'charts_json': json.dumps([c.to_dict() for c in charts]),
        'schema_json': json.dumps(schema_json),
        'sections_json': sections_json,
    }
    return render(request, 'dataviz/pages/builder.html', context)


@login_required
@require_POST
def page_save(request, page_id=None):
    if not is_user_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        
    try:
        data = json.loads(request.body)
        title = data.get('title')
        description = data.get('description', '')
        page_filters = data.get('page_filters', [])
        
        if not title:
            return JsonResponse({'success': False, 'error': 'Title is required.'}, status=400)
            
        page = None
        if page_id:
            page = get_object_or_04copy(DataVizPage, pk=page_id)
            if not page:
                return JsonResponse({'success': False, 'error': 'Page not found.'}, status=444)
            if not request.user.is_superuser and page.owner != request.user:
                return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        else:
            page = DataVizPage(owner=request.user)
            
        page.title = title
        page.slug = slugify(title)
        page.description = description
        page.page_filters = page_filters
        page.save()
        
        return JsonResponse({'success': True, 'id': page.pk})
    except Exception as e:
        logger.exception("Error saving page")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def page_delete(request, page_id):
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
    page = get_object_or_04copy(DataVizPage, pk=page_id)
    if page:
        if request.user.is_superuser or page.owner == request.user:
            # If there is a linked site page, delete it too
            if page.linked_site_page:
                page.linked_site_page.delete()
            page.delete()
    return redirect('dataviz:page_list')


@login_required
@require_POST
def page_layout_save(request, page_id):
    """Saves layout elements (GridStack grid layout changes) for the page."""
    if not is_user_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        
    page = get_object_or_04copy(DataVizPage, pk=page_id)
    if not page or (not request.user.is_superuser and page.owner != request.user):
        return JsonResponse({'success': False, 'error': 'Access Denied.'}, status=403)
        
    try:
        data = json.loads(request.body)
        sections = data.get('sections', [])
        
        with transaction.atomic():
            # Delete existing sections
            page.sections.all().delete()
            
            # Recreate sections
            for idx, s in enumerate(sections):
                chart_id = s.get('chart_id')
                chart = None
                if chart_id:
                    chart = SavedChart.objects.get(pk=chart_id)
                    
                DataVizPageSection.objects.create(
                    page=page,
                    element_type=s.get('element_type', 'chart'),
                    chart=chart,
                    html_content=s.get('html_content', ''),
                    gs_x=s.get('gs_x', 0),
                    gs_y=s.get('gs_y', 0),
                    gs_w=s.get('gs_w', 6),
                    gs_h=s.get('gs_h', 4),
                    position=idx
                )
        return JsonResponse({'success': True})
    except Exception as e:
        logger.exception("Error saving page layout")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def page_publish_toggle(request, page_id):
    """Toggles publishing of a page and links/unlinks it in Site Navigation (users.Page)."""
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
        
    page = get_object_or_04copy(DataVizPage, pk=page_id)
    if not page or (not request.user.is_superuser and page.owner != request.user):
        return HttpResponseForbidden("Access Denied.")
        
    try:
        if page.status == DataVizPage.STATUS_DRAFT:
            # Publish page: create SitePage entry in users.Page
            site_page_url = f"/dataviz/page/{page.slug}/"
            
            # Create or find SitePage
            site_page, created = SitePage.objects.get_or_create(
                slug=page.slug,
                defaults={
                    'name': page.title,
                    'app_reference': 'dataviz',
                    'description': page.description,
                    'url_path': site_page_url,
                    'icon': 'fa-dashboard',
                    'status': 'active',
                    'show_in_menu': True,
                    'is_public': False,
                    'created_by': request.user
                }
            )
            
            if not created:
                site_page.name = page.title
                site_page.description = page.description
                site_page.url_path = site_page_url
                site_page.status = 'active'
                site_page.show_in_menu = True
                site_page.save()
                
            page.linked_site_page = site_page
            page.status = DataVizPage.STATUS_ACTIVE
            page.save()
        else:
            # Unpublish page
            if page.linked_site_page:
                page.linked_site_page.delete()
                page.linked_site_page = None
            page.status = DataVizPage.STATUS_DRAFT
            page.save()
            
        return redirect('dataviz:page_list')
    except Exception as e:
        logger.exception("Error toggling page publishing")
        return render(request, 'dataviz/partials/error.html', {'error': str(e)})


@login_required
def public_page_view(request, slug):
    """Public viewing of a published DataViz Page."""
    from django.db import connection
    page = get_object_or_04copy(DataVizPage, slug=slug)
    if not page:
        return render(request, 'dataviz/partials/error.html', {'error': 'Page not found.'})
        
    # Standard check: if draft, only admin/owner can view
    if page.status == DataVizPage.STATUS_DRAFT:
        if not is_user_admin(request.user) or (not request.user.is_superuser and page.owner != request.user):
            return HttpResponseForbidden("Access Denied. This page is currently a draft.")
            
    # Verify SitePage role access permissions
    if page.linked_site_page:
        site_page = page.linked_site_page
        if site_page.status != 'active':
            if not is_user_admin(request.user):
                return HttpResponseForbidden("This page is currently inactive.")
        
        required_roles = site_page.required_roles or []
        if required_roles:
            user_roles = get_user_roles(request.user)
            if not (request.user.is_superuser or bool(user_roles & set(required_roles))):
                return HttpResponseForbidden("You do not have the required role to view this page.")
                
    # Check full-page render cache (5 min). Vary by user to avoid leaking
    # role-specific content across users.
    user_role = getattr(getattr(request.user, 'active_role', None), 'name', 'None')
    page_cache_key = f"dv:page_view:{slug}:u{request.user.pk}:r{user_role}"
    try:
        cached_response = cache.get(page_cache_key)
        if cached_response is not None:
            from django.http import HttpResponse
            return HttpResponse(cached_response)
    except Exception:
        cached_response = None

    # Build list of sections for layout rendering
    sections = page.sections.select_related('chart').all().order_by('position')

    # Load unique values for filters configured on the page (cached)
    filter_options = {}
    for f in page.page_filters or []:
        col_name = f.get('column')
        label = f.get('label')

        # Try cache first
        f_cache_key = f'dataviz:filter_vals:{col_name}'
        try:
            vals = cache.get(f_cache_key)
        except Exception:
            logger.warning("Cache unavailable", exc_info=True)
            vals = None
        if vals is None:
            # Load distinct values for filters from DB safely
            tbl, col = col_name.split('.')
            if tbl in TABLE_WHITELIST and col in COLUMN_WHITELIST[tbl]:
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT DISTINCT "{col}" FROM "{tbl}" WHERE "{col}" IS NOT NULL AND "{col}" != \'\' ORDER BY "{col}"')
                    vals = [row[0] for row in cursor.fetchall()]
                try:
                    cache.set(f_cache_key, vals, 300)
                except Exception:
                    logger.warning("Cache unavailable for write", exc_info=True)
            else:
                vals = []
        filter_options[col_name] = vals

    context = {
        'page': page,
        'sections': sections,
        'filter_options': filter_options,
    }
    response = render(request, 'dataviz/pages/view.html', context)
    try:
        cache.set(page_cache_key, response.content, 300)
    except Exception:
        pass
    return response


@login_required
def api_column_unique_values(request):
    """Returns a JSON list of unique values for a specified table.column."""
    if not is_user_admin(request.user):
        return HttpResponseForbidden("Access Denied.")
        
    column_str = request.GET.get('column')
    if not column_str:
        return JsonResponse({'values': []})
        
    try:
        parts = column_str.split('.')
        if len(parts) != 2:
            return JsonResponse({'values': []}, status=400)
            
        table_name, col_name = parts
        
        # Whitelist validation
        if table_name not in TABLE_WHITELIST or table_name not in COLUMN_WHITELIST:
            return JsonResponse({'values': []}, status=400)
        if col_name not in COLUMN_WHITELIST[table_name]:
            return JsonResponse({'values': []}, status=400)
            
        cache_key = f'dataviz:unique_vals:{table_name}.{col_name}'
        try:
            values = cache.get(cache_key)
        except Exception:
            logger.warning("Cache unavailable", exc_info=True)
            values = None
        if values is None:
            from django.db import connection
            sql = f'SELECT DISTINCT "{col_name}" FROM "{table_name}" WHERE "{col_name}" IS NOT NULL AND "{col_name}" != %s ORDER BY "{col_name}" LIMIT 500'
            
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
                logger.warning("Cache unavailable for write", exc_info=True)
        return JsonResponse({'values': values})
    except Exception as e:
        logger.exception("Error fetching unique values")
        return JsonResponse({'values': [], 'error': str(e)}, status=500)

