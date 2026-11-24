"""JSON API for the CA Dashboard Performance Map."""
from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from ca_dash.decorators import api_login_required
from ca_dash.config_helpers import (
    YEAR_COLORS,
    district_cds,
    district_full_name,
    district_short_name,
    year_palette,
)
from ca_dash.services.grid.renderer import (
    build_grid_payload,
    build_school_detail,
    build_typed_grids,
)
from ca_dash.services.constants import COLOR_MAP
from ca_dash.services.grid.templates import resolve_template_key
from ca_dash.services.indicators.registry import get_indicator, list_indicators
from ca_dash.services.loader import PostgresIndicatorLoader


def _loader() -> PostgresIndicatorLoader:
    return PostgresIndicatorLoader()


def _parse_reporting_years(request, indicator_key: str, loader: PostgresIndicatorLoader) -> list[int]:
    raw = request.GET.get('reporting_years', '')
    available = loader.get_reporting_years(indicator_key)
    if not available:
        return []
    if not raw:
        return [available[-1]]
    selected = [int(y.strip()) for y in raw.split(',') if y.strip().isdigit()]
    filtered = [y for y in selected if y in available]
    return filtered or [available[-1]]


def _parse_studentgroups(request) -> list[str] | None:
    if 'studentgroups' not in request.GET:
        return None
    raw = request.GET.get('studentgroups', '')
    if not raw.strip():
        return None
    return [s.strip() for s in raw.split(',') if s.strip()]


def _get_int_param(request, key: str, default=None):
    raw = request.GET.get(key)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _filter_subgroup_entities(
    entities_by_box: dict[int, list[dict]], studentgroups: list[str] | None
) -> dict[int, list[dict]]:
    if studentgroups is None:
        return entities_by_box
    allowed = set(studentgroups)
    return {
        box: [e for e in items if e.get('code') in allowed]
        for box, items in entities_by_box.items()
    }


@api_login_required
@require_GET
def api_config(request):
    try:
        loader = _loader()
        reporting_years = {}
        for ind in list_indicators():
            try:
                reporting_years[ind.key] = loader.get_reporting_years(ind.key)
            except FileNotFoundError:
                reporting_years[ind.key] = []
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)
    latest_years = {k: (v[-1] if v else None) for k, v in reporting_years.items()}
    return JsonResponse(
        {
            'district_cds': district_cds(),
            'district_short': district_short_name(),
            'district_full': district_full_name(),
            'reporting_years': latest_years,
            'available_reporting_years': reporting_years,
            'year_colors': {str(k): v for k, v in YEAR_COLORS.items()},
            'official_2026': {ind.key: False for ind in list_indicators()},
        }
    )


@api_login_required
@require_GET
def api_indicators(request):
    return JsonResponse(
        [
            {'key': i.key, 'label': i.label, 'layout_url': i.layout_url}
            for i in list_indicators()
        ],
        safe=False,
    )


@api_login_required
@require_GET
def api_schools(request):
    indicator = request.GET.get('indicator', 'chronic')
    district = district_cds()
    try:
        loader = _loader()
        years = _parse_reporting_years(request, indicator, loader)
        schools = loader._get_schools_for_years(indicator, district, years)
    except (KeyError, FileNotFoundError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse(schools, safe=False)


@api_login_required
@require_GET
def api_subgroups(request):
    indicator = request.GET.get('indicator', 'chronic')
    district = district_cds()
    school_cds = request.GET.get('school_cds', '')
    rtype = 'S' if school_cds else 'D'
    cds = school_cds or district
    try:
        loader = _loader()
        years = _parse_reporting_years(request, indicator, loader)
        if school_cds:
            subgroups = loader.get_school_subgroups(indicator, school_cds, years)
        else:
            subgroups = loader.get_subgroups(indicator, cds or None, rtype, district or None, years)
    except (KeyError, FileNotFoundError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse(subgroups, safe=False)


@api_login_required
@require_GET
def api_school_detail(request):
    indicator_key = request.GET.get('indicator', 'chronic')
    school_cds = request.GET.get('school_cds', '')
    reporting_year = _get_int_param(request, 'reporting_year')
    if not school_cds:
        return JsonResponse({'error': 'school_cds required'}, status=400)
    try:
        ind = get_indicator(indicator_key)
        loader = _loader()
        years = [reporting_year] if reporting_year else _parse_reporting_years(request, indicator_key, loader)
        rows = loader.get_school_subgroup_records(indicator_key, school_cds, years)
        detail_year = reporting_year or (years[-1] if years else None)
        year_rows = (
            [r for r in rows if int(r.get('reportingyear') or 0) == detail_year]
            if detail_year
            else rows
        )
        if not year_rows:
            year_rows = rows
        base_sg = 'EL' if indicator_key == 'elpi' else 'ALL'
        all_row = next(
            (r for r in year_rows if r.get('studentgroup') == base_sg),
            year_rows[0] if year_rows else None,
        )
        detail = build_school_detail(all_row, year_rows, ind.indicator_code)
        if detail:
            tkey = resolve_template_key(
                ind.grid_template,
                ind.grid_variant_field,
                all_row.get('type') or all_row.get('hscutpoints') if all_row else None,
                'S',
            )
            detail['template_key'] = tkey
        return JsonResponse(
            {
                'school_detail': detail,
                'reporting_year': detail_year or loader.get_latest_reporting_year(indicator_key),
                'indicator_label': ind.label,
            }
        )
    except (KeyError, FileNotFoundError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@api_login_required
@require_GET
def api_report(request):
    indicator_key = request.GET.get('indicator', 'chronic')
    district = district_cds()
    school_cds = request.GET.get('school_cds', '')
    studentgroup = request.GET.get('studentgroup', 'ALL')
    if indicator_key == 'elpi' and studentgroup == 'ALL':
        studentgroup = 'EL'
    d_short = district_short_name()

    try:
        ind = get_indicator(indicator_key)
        loader = _loader()
        reporting_years = _parse_reporting_years(request, indicator_key, loader)
        primary_year = reporting_years[-1] if reporting_years else None
        district_sg = 'EL' if indicator_key == 'elpi' else 'ALL'
        district_row = loader.get_record(indicator_key, district, 'D', district_sg, primary_year)

        school_grids: list = []
        school_grid = None
        school_detail = None
        view_mode = 'district_map'

        if school_cds:
            view_mode = 'school_subgroups'
            studentgroups = _parse_studentgroups(request)
            subgroup_rows = loader.get_school_subgroup_records(
                indicator_key, school_cds, reporting_years
            )
            if studentgroups is not None:
                subgroup_rows = [
                    r for r in subgroup_rows if r.get('studentgroup') in studentgroups
                ]
            primary_rows = (
                [r for r in subgroup_rows if int(r.get('reportingyear') or 0) == primary_year]
                if primary_year
                else subgroup_rows
            )
            base_sg = 'EL' if indicator_key == 'elpi' else 'ALL'
            all_row = next(
                (r for r in primary_rows if r.get('studentgroup') == base_sg), None
            )
            if not all_row:
                all_row = next(
                    (r for r in subgroup_rows if r.get('studentgroup') == base_sg), None
                )
            if not all_row and subgroup_rows:
                all_row = subgroup_rows[0]
            subgroups_by_box = loader.get_subgroups_grouped_by_box(
                indicator_key, school_cds, reporting_years
            )
            subgroups_by_box = _filter_subgroup_entities(subgroups_by_box, studentgroups)
            tkey = resolve_template_key(
                ind.grid_template,
                ind.grid_variant_field,
                all_row.get('type') or all_row.get('hscutpoints') if all_row else None,
                'S',
            )
            school_name = (
                (all_row.get('school_short_name') or all_row.get('schoolname'))
                if all_row
                else 'School'
            )
            title = school_name
            if len(reporting_years) > 1:
                title = f"{school_name} · {', '.join(str(y) for y in reporting_years)}"
            school_grid = build_grid_payload(
                ind,
                all_row,
                entities_by_box=subgroups_by_box,
                highlight_box=int(all_row['box']) if all_row and all_row.get('box') else None,
                title=title,
                entity_label='Student Group',
                tag_field='name',
                template_key=tkey,
                reporting_years=reporting_years,
            )
            school_grids = [school_grid]
            school_detail = build_school_detail(all_row, primary_rows, ind.indicator_code)
        else:
            map_rows = loader.get_school_map_rows(
                indicator_key, district, studentgroup, reporting_years
            )
            school_grids = build_typed_grids(
                ind, map_rows, studentgroup, school_cds or None, reporting_years
            )
            if len(school_grids) == 1:
                school_grid = school_grids[0]

        district_tkey = resolve_template_key(
            ind.grid_template,
            ind.grid_variant_field,
            district_row.get('type') or district_row.get('hscutpoints') if district_row else None,
            'D',
        )
        district_entities: dict[int, list] = {}
        district_map_rows = loader.get_district_map_rows(
            indicator_key, district, studentgroup, reporting_years
        )
        for drow in district_map_rows:
            box = drow.get('box')
            if not box or int(box) <= 0:
                continue
            box = int(box)
            district_entities.setdefault(box, []).append(
                {
                    'cds': drow.get('cds'),
                    'name': d_short,
                    'rtype': 'D',
                    'reportingyear': drow.get('reportingyear'),
                }
            )

        district_subgroups_enabled = request.GET.get('district_subgroups', '') == 'true'
        if district_subgroups_enabled:
            district_subgroup_boxes = loader.get_district_subgroups_by_box(
                indicator_key, district, reporting_years
            )
            for b, items in district_subgroup_boxes.items():
                district_entities.setdefault(b, []).extend(items)

        district_grid = build_grid_payload(
            ind,
            district_row,
            entities_by_box=district_entities,
            highlight_cds=district,
            highlight_box=int(district_row['box']) if district_row and district_row.get('box') else None,
            title=d_short,
            entity_label='District',
            template_key=district_tkey,
            reporting_years=reporting_years,
        )

        return JsonResponse(
            {
                'indicator': indicator_key,
                'indicator_label': ind.label,
                'reporting_year': primary_year,
                'reporting_years': reporting_years,
                'year_palette': year_palette(reporting_years),
                'official_2026': False,
                'district_cds': district,
                'district_short': d_short,
                'school_cds': school_cds or None,
                'studentgroup': studentgroup,
                'view_mode': view_mode,
                'school_grids': school_grids,
                'school_grid': school_grid or (school_grids[0] if school_grids else None),
                'district_grid': district_grid,
                'school_detail': school_detail,
            }
        )
    except (KeyError, FileNotFoundError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@api_login_required
@require_GET
def api_box_entities(request):
    indicator_key = request.GET.get('indicator', 'chronic')
    box = _get_int_param(request, 'box')
    rtype = request.GET.get('rtype', 'S')
    district = district_cds()
    school_cds = request.GET.get('school_cds', '')
    studentgroup = request.GET.get('studentgroup', 'ALL')
    if indicator_key == 'elpi' and studentgroup == 'ALL':
        studentgroup = 'EL'
    type_key = request.GET.get('type_key', '')

    if box is None:
        return JsonResponse({'error': 'box required'}, status=400)

    try:
        loader = _loader()
        years = _parse_reporting_years(request, indicator_key, loader)
        if school_cds and rtype == 'S':
            studentgroups = _parse_studentgroups(request)
            rows = loader.get_school_subgroup_records(indicator_key, school_cds, years)
            if studentgroups is not None:
                rows = [r for r in rows if r.get('studentgroup') in studentgroups]
            entities = [
                {
                    'cds': r.get('cds'),
                    'name': r.get('studentgroup_label') or r.get('studentgroup'),
                    'code': r.get('studentgroup'),
                    'rtype': 'S',
                    'currstatus': r.get('currstatus'),
                    'priorstatus': r.get('priorstatus'),
                    'change': r.get('change'),
                    'color': r.get('color'),
                    'box': r.get('box'),
                    'reportingyear': r.get('reportingyear'),
                }
                for r in rows
                if int(r.get('box') or 0) == box and int(r.get('box') or 0) > 0
            ]
        else:
            entities = loader.get_entities_in_box(
                indicator_key,
                box,
                rtype,
                district or None,
                studentgroup,
                school_cds or None,
                years,
            )
        if type_key and rtype == 'S':
            ind = get_indicator(indicator_key)
            entities = [
                e
                for e in entities
                if resolve_template_key(
                    ind.grid_template,
                    ind.grid_variant_field,
                    e.get('type') or e.get('hscutpoints'),
                    'S',
                )
                == type_key
            ]
        d_short = district_short_name()
        for entity in entities:
            color = int(entity.get('color') or 0)
            entity['color_name'] = COLOR_MAP.get(color, COLOR_MAP[0])['name']
            entity['color_hex'] = COLOR_MAP.get(color, COLOR_MAP[0])['hex']
            if entity.get('rtype') == 'D':
                entity['name'] = d_short
            elif entity.get('school_short_name'):
                entity['name'] = entity['school_short_name']
        return JsonResponse({'box': box, 'rtype': rtype, 'entities': entities})
    except (KeyError, FileNotFoundError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)