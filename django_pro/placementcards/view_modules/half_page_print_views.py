from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from importlib import import_module
from io import BytesIO
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from placementcards.models import PlacementCard, QuestionTemplate, StudentPlacement
from placementcards.view_modules.card_data_utils import (
    can_user_access_placement_cards,
    can_user_access_teacher_notes,
    can_user_place_students,
    get_accessible_students,
    get_request_school,
    placement_card_map,
)
from studata.models import CurrentStudent, Result, Schedule, StudentEnroll, Test


CATEGORY_LABELS = {
    'academic': 'Academic',
    'behavior': 'Behavior & Study Habits',
    'physical': 'Physical & Health',
    'attendance': 'Behavior & Attendance',
    'parent': 'Parent Involvement',
    'additional': 'Additional Information',
}

PROFILE_FIELD_ORDER = [
    ('Student ID', 'student_id'),
    ('SSID', 'ssid'),
    ('Grade', 'grade'),
    ('School Year', 'school_year'),
    ('Birth Date', 'birth_date'),
    ('Gender', 'gender'),
    ('Race', 'race'),
    ('Status', 'status'),
    ('Attendance Rate', 'atendace_rate'),
    ('School State ID', 'school_state_id'),
    ('ELL Status', 'ell_status'),
    ('LTEL', 'ltel'),
    ('FEP Date', 'fep_date'),
    ('US Enter Date', 'us_enter_date'),
    ('State Enter Date', 'state_enter_date'),
    ('Instructional Setting', 'instructional_setting'),
    ('Special Program', 'special_program_code'),
    ('SPED', 'sped'),
    ('504 Plan', 'in_504'),
    ('GATE', 'gate'),
    ('SED', 'sed'),
    ('Foster', 'foster'),
    ('Homeless', 'homeless'),
    ('Migrant', 'migrant'),
    ('DLI', 'dli'),
    ('Suspension', 'suspension_indicator'),
]


def _load_reportlab():
    try:
        colors_module = import_module('reportlab.lib.colors')
        pagesizes_module = import_module('reportlab.lib.pagesizes')
        utils_module = import_module('reportlab.lib.utils')
        canvas_module = import_module('reportlab.pdfgen.canvas')
    except Exception:  # pragma: no cover
        return None
    return (
        colors_module,
        pagesizes_module.letter,
        pagesizes_module.landscape(pagesizes_module.letter),
        utils_module.simpleSplit,
        canvas_module,
    )


def _is_school_admin(user) -> bool:
    if user.is_superuser:
        return True
    role_names: set[str] = set()
    if hasattr(user, 'active_role') and user.active_role:
        role_names.add(user.active_role.name)
    if hasattr(user, 'roles'):
        role_names.update(user.roles.values_list('name', flat=True))
    return 'School Admin' in role_names


def _parse_tag_ids(request: HttpRequest) -> list[int]:
    return [int(tag_id) for tag_id in request.GET.getlist('tag_ids') if tag_id.isdigit()]


def _resolve_school_year(request: HttpRequest) -> str:
    school_year = request.GET.get('school_year', '').strip()
    if school_year:
        return school_year
    return get_accessible_students(request).values_list('school_year', flat=True).order_by('-school_year').first() or ''


def _questions_for_school(school_id: int):
    key = f'placement_questions:{school_id}'
    return cache.get_or_set(
        key,
        lambda: list(
            QuestionTemplate.objects.filter(school_id=school_id, is_active=True)
            .only('question_category', 'question_text', 'question_type', 'field_options', 'display_order')
            .order_by('question_category', 'display_order', 'id')
        ),
        timeout=3600,
    )


def _students_for_notes_export(request: HttpRequest, school_year: str, ssid: str | None = None):
    students = get_accessible_students(request, school_year=school_year)
    if ssid:
        students = students.filter(ssid=ssid)

    selected_homeroom_teachers = [value.strip() for value in request.GET.getlist('homeroom_teacher') if value.strip()]
    if selected_homeroom_teachers and _is_school_admin(request.user):
        students = students.filter(
            ssid__in=Schedule.objects.filter(
                school_year=school_year,
                period__in=['0', '00'],
                staff_name__in=selected_homeroom_teachers,
            ).values('ssid')
        ).distinct()

    selected_tag_ids = _parse_tag_ids(request)
    if selected_tag_ids:
        students = students.filter(
            ssid__in=PlacementCard.objects.filter(school_year=school_year, tags__id__in=selected_tag_ids).values('ssid')
        ).distinct()

    student_list = list(students)
    card_map = placement_card_map([student.ssid for student in student_list], school_year)
    for student in student_list:
        student.placement_card = card_map.get(student.ssid)
    return student_list


def _draw_text_wrapped(pdf: Any, text: str, x: int, y: int, width: int, simple_split, font_name: str = 'Helvetica', font_size: int = 8, leading: int = 10) -> int:
    lines = simple_split(str(text or ''), font_name, font_size, width)
    pdf.setFont(font_name, font_size)
    for line in lines:
        pdf.drawString(x, y, line)
        y -= leading
    return y


def _fit_single_line(text: str, max_len: int = 52) -> str:
    value = str(text or '').strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + '…'


def _draw_section_title(pdf: Any, x: int, y: int, width: int, title: str, colors_module):
    pdf.setFillColor(colors_module.HexColor('#F2F2F2'))
    pdf.setStrokeColor(colors_module.HexColor('#444444'))
    pdf.rect(x, y - 14, width, 14, stroke=1, fill=1)
    pdf.setFillColor(colors_module.black)
    pdf.setFont('Helvetica-Bold', 8)
    pdf.drawString(x + 6, y - 10, title)


def _fmt_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime('%m/%d/%Y')
    if isinstance(value, date):
        return value.strftime('%m/%d/%Y')
    return '—'


def _student_profile_map(students: list, school_year: str) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    ssids = [student.ssid for student in students if getattr(student, 'ssid', None)]
    if not ssids:
        return {}, {}

    cs_rows = {
        row.ssid: row
        for row in CurrentStudent.objects.filter(ssid__in=ssids, school_year=school_year).only(
            'ssid',
            'student_name',
            'student_id',
            'grade',
            'gender',
            'race',
            'school',
            'school_state_id',
            'status',
            'atendace_rate',
            'sped',
            'in_504',
            'gate',
            'sed',
            'foster',
            'homeless',
            'dli',
            'suspension_indicator',
            'ltel',
            'school_year',
        )
    }

    enroll_rows = (
        StudentEnroll.objects.filter(ssid__in=ssids)
        .only(
            'ssid',
            'birth_date',
            'instructional_setting',
            'ell_status',
            'fep_date',
            'us_enter_date',
            'state_enter_date',
            'special_program_code',
            'migrant',
            'school_year',
            'enter_date',
        )
        .order_by('ssid', '-school_year', '-enter_date')
    )
    enroll_by_ssid: dict[str, StudentEnroll] = {}
    for row in enroll_rows:
        if row.ssid not in enroll_by_ssid:
            enroll_by_ssid[row.ssid] = row

    homeroom_rows = (
        Schedule.objects.filter(ssid__in=ssids, school_year=school_year, period__in=['0', '00'])
        .only('ssid', 'staff_name')
        .order_by('ssid', 'staff_name')
    )
    homeroom_by_ssid: dict[str, str] = {}
    for row in homeroom_rows:
        if row.ssid not in homeroom_by_ssid:
            homeroom_by_ssid[row.ssid] = row.staff_name or '—'

    profile_by_ssid: dict[str, dict[str, str]] = {}
    for student in students:
        cs = cs_rows.get(student.ssid)
        se = enroll_by_ssid.get(student.ssid)
        profile_by_ssid[student.ssid] = {
            'student_name': (getattr(cs, 'student_name', '') or getattr(student, 'student_name', '') or '—'),
            'student_id': str(getattr(cs, 'student_id', '') or '—'),
            'ssid': str(getattr(student, 'ssid', '') or '—'),
            'grade': str(getattr(cs, 'grade', '') or getattr(student, 'grade', '') or '—'),
            'gender': str(getattr(cs, 'gender', '') or '—'),
            'race': str(getattr(cs, 'race', '') or '—'),
            'birth_date': _fmt_date(getattr(se, 'birth_date', None)),
            'school': str(getattr(cs, 'school', '') or getattr(student, 'school', '') or '—'),
            'school_state_id': str(getattr(cs, 'school_state_id', '') or '—'),
            'school_year': str(getattr(cs, 'school_year', '') or school_year or '—'),
            'status': str(getattr(cs, 'status', '') or '—'),
            'atendace_rate': f"{getattr(cs, 'atendace_rate', '')}%" if getattr(cs, 'atendace_rate', None) is not None else '—',
            'instructional_setting': str(getattr(se, 'instructional_setting', '') or '—'),
            'ell_status': str(getattr(se, 'ell_status', '') or '—'),
            'ltel': str(getattr(cs, 'ltel', '') or '—'),
            'fep_date': _fmt_date(getattr(se, 'fep_date', None)),
            'us_enter_date': _fmt_date(getattr(se, 'us_enter_date', None)),
            'state_enter_date': _fmt_date(getattr(se, 'state_enter_date', None)),
            'special_program_code': str(getattr(se, 'special_program_code', '') or '—'),
            'sped': str(getattr(cs, 'sped', '') or '—'),
            'in_504': str(getattr(cs, 'in_504', '') or '—'),
            'gate': str(getattr(cs, 'gate', '') or '—'),
            'sed': str(getattr(cs, 'sed', '') or '—'),
            'foster': str(getattr(cs, 'foster', '') or '—'),
            'homeless': str(getattr(cs, 'homeless', '') or '—'),
            'migrant': str(getattr(se, 'migrant', '') or '—'),
            'dli': str(getattr(cs, 'dli', '') or '—'),
            'suspension_indicator': str(getattr(cs, 'suspension_indicator', '') or '—'),
        }

    return profile_by_ssid, homeroom_by_ssid


def _normalize_test_name(value: str | None) -> str:
    text = (value or '').lower()
    return text.replace('-', '').replace('_', '').replace(' ', '')


def _assessment_type_label(test_name: str) -> str:
    normalized = _normalize_test_name(test_name)
    raw = (test_name or '').lower()
    if 'elpac' in normalized and ('summative' in normalized or 'summitive' in normalized) and 'alternate' not in raw:
        return 'ELPAC Summative'
    if 'caaspp' in normalized or 'caspp' in normalized:
        return 'CAASPP'
    if 'dibels' in normalized or 'dibbles' in normalized:
        return 'Dibels'
    if 'iready' in normalized:
        return 'iReady'
    return test_name or 'Assessment'


def _previous_school_year(school_year: str) -> str | None:
    value = (school_year or '').strip()
    if '-' not in value:
        return None
    parts = value.split('-', 1)
    if len(parts) != 2:
        return None
    start, end = parts[0].strip(), parts[1].strip()
    if not (start.isdigit() and end.isdigit()):
        return None
    return f'{int(start) - 1}-{int(end) - 1}'


def _short_school_year(school_year: str) -> str:
    value = (school_year or '').strip()
    if '-' not in value:
        return value
    parts = value.split('-', 1)
    if len(parts) != 2:
        return value
    start, end = parts[0].strip(), parts[1].strip()
    if not (start.isdigit() and end.isdigit()):
        return value
    return f"{start[-2:]}-{end[-2:]}"


def _school_year_variants(value: str) -> set[str]:
    raw = (value or '').strip()
    if not raw:
        return set()
    short = _short_school_year(raw)
    variants = {raw, short}
    if '-' in raw:
        left, right = raw.split('-', 1)
        if left.isdigit() and right.isdigit() and len(left) == 2 and len(right) == 2:
            variants.add(f"20{left}-20{right}")
    return {item for item in variants if item}


def _assessment_snapshot_map(ssids: list[str], school_year: str) -> dict[str, list[dict[str, str]]]:
    if not ssids:
        return {}

    results = list(
        Result.objects.filter(ssid__in=ssids)
        .only('ssid', 'school_year', 'test_group_key', 'pl', 'ss')
        .order_by('-school_year')
    )
    previous_year = _previous_school_year(school_year)
    current_year_variants = _school_year_variants(school_year)
    previous_year_variants = _school_year_variants(previous_year) if previous_year else set()
    allowed_years = current_year_variants | previous_year_variants
    group_keys = list({row.test_group_key for row in results if row.test_group_key})
    tests_by_group = {
        test.test_group_key: test
        for test in Test.objects.filter(test_group_key__in=group_keys).only('test_group_key', 'test_name', 'subject', 'test_window')
    }

    grouped: dict[str, list[dict[str, str]]] = {ssid: [] for ssid in ssids}
    seen: dict[str, set[tuple[str, str, str, str, str, str]]] = {ssid: set() for ssid in ssids}
    for row in results:
        row_year = str(getattr(row, 'school_year', '') or '')
        test = tests_by_group.get(row.test_group_key)
        if not test:
            continue

        test_name = str(getattr(test, 'test_name', '') or 'Assessment')
        assessment_type = _assessment_type_label(test_name)

        include_row = False
        if assessment_type in {'Dibels', 'iReady'}:
            include_row = row_year in current_year_variants
        elif assessment_type == 'CAASPP':
            include_row = row_year in allowed_years
        elif assessment_type in {'ELPAC Summative', 'ELPAC Summative (Regular)'}:
            include_row = row_year in allowed_years
        if not include_row:
            continue

        subject = str(getattr(test, 'subject', '') or 'General')
        window = str(getattr(test, 'test_window', '') or '')
        pl_value = str(getattr(row, 'pl', '') or '—').strip() or '—'
        ss_value = str(getattr(row, 'ss', '') or '—').strip() or '—'
        row_key = (row_year, test_name, subject, window, pl_value, ss_value)
        if row_key in seen[row.ssid]:
            continue
        seen[row.ssid].add(row_key)

        grouped[row.ssid].append(
            {
                'type': assessment_type,
                'year': _short_school_year(row_year) if row_year else '—',
                'test_name': test_name,
                'subject': subject,
                'window': window or '—',
                'pl': pl_value,
                'ss': ss_value,
            }
        )

    for ssid in grouped:
        grouped[ssid].sort(key=lambda row: (row.get('year', ''), row.get('test_name', ''), row.get('subject', '')), reverse=True)
    return grouped


def _draw_profile_three_columns(pdf: Any, profile: dict[str, str], x: int, y: int, width: int, colors_module, simple_split) -> int:
    total_fields = len(PROFILE_FIELD_ORDER)
    col_count = 2
    rows_per_col = (total_fields + (col_count - 1)) // col_count
    row_unit = 8
    max_h = 132
    min_h = 96
    box_h = max(min_h, min(max_h, 22 + (rows_per_col * row_unit)))
    pdf.setFillColor(colors_module.white)
    pdf.setStrokeColor(colors_module.HexColor('#444444'))
    pdf.rect(x, y - box_h, width, box_h, stroke=1, fill=1)
    _draw_section_title(pdf, x, y, width, 'Student Profile', colors_module)

    inner_top = y - 22
    inner_x = x + 8
    inner_w = width - 16
    col_gap = 10
    col_w = (inner_w - ((col_count - 1) * col_gap)) / col_count
    col_x_values = [inner_x + (idx * (col_w + col_gap)) for idx in range(col_count)]
    split_fields = [
        PROFILE_FIELD_ORDER[idx * rows_per_col:(idx + 1) * rows_per_col]
        for idx in range(col_count)
    ]

    def draw_col(col_x: float, fields: list[tuple[str, str]]):
        row_y = inner_top
        label_w = 64
        value_extra_indent = 10
        value_w = int(col_w - label_w - value_extra_indent - 2)
        for label, key in fields:
            value = str(profile.get(key, '—') or '—')
            pdf.setFillColor(colors_module.black)
            label_size = 5.7
            value_size = 5.7
            pdf.setFont('Helvetica', label_size)
            pdf.drawString(col_x, row_y, f'{label}:')
            value_lines = simple_split(value, 'Helvetica-Bold', value_size, value_w)
            if not value_lines:
                value_lines = ['—']
            value_lines = value_lines[:2]
            pdf.setFont('Helvetica-Bold', value_size)
            y_line = row_y
            for line in value_lines:
                pdf.drawString(col_x + label_w + value_extra_indent, y_line, line)
                y_line -= 6
            row_y -= max(8, 2 + (len(value_lines) * 6))

    for idx in range(col_count):
        draw_col(col_x_values[idx], split_fields[idx])

    return y - box_h - 8


def _draw_assessment_three_columns(pdf: Any, ssid: str, x: int, y: int, width: int, colors_module, simple_split, assessment_map, max_height: float) -> int:
    snapshot = assessment_map.get(ssid, [])
    inner_x = x + 3
    inner_w = width - 6
    blocks: list[tuple[str, list[str]]] = []

    if snapshot:
        iready_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        dibels_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        state_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

        for row in snapshot:
            row_type = row.get('type', '')
            if row_type == 'iReady':
                iready_groups[(row.get('year', '—'), row.get('subject', '—'))].append(row)
            elif row_type == 'Dibels':
                dibels_groups[row.get('year', '—')].append(row)
            elif row_type in {'ELPAC Summative', 'ELPAC Summative (Regular)', 'CAASPP'}:
                state_groups[(row.get('year', '—'), row.get('test_name', row_type))].append(row)

        for (year, subject) in sorted(iready_groups.keys(), reverse=True):
            rows = sorted(iready_groups[(year, subject)], key=lambda item: item.get('window', ''))
            lines = []
            for item in rows:
                period = item.get('window', '—') or '—'
                lines.append(f"{period}: {item.get('pl', '—')}")
            blocks.append((f"iReady {year} · {subject}", lines or ['—']))

        for year in sorted(dibels_groups.keys(), reverse=True):
            rows = sorted(dibels_groups[year], key=lambda item: item.get('window', ''))
            lines = []
            for item in rows:
                period = item.get('window', '—') or '—'
                lines.append(f"{period}: {item.get('pl', '—')}")
            blocks.append((f"Dibels {year}", lines or ['—']))

        for (year, test_name) in sorted(state_groups.keys(), reverse=True):
            rows = state_groups[(year, test_name)]
            pairs: list[str] = []
            seen_pairs: set[str] = set()
            for item in rows:
                subject_value = str(item.get('subject', '—') or '—').strip() or '—'
                pl_value = str(item.get('pl', '—') or '—').strip() or '—'
                ss_value = str(item.get('ss', '—') or '—').strip() or '—'
                pair = f"{subject_value} {pl_value} / {ss_value}"
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                pairs.append(pair)
            blocks.append((f"{year} {test_name} · Subject PL / SS:", pairs or ['— — / —']))

    estimated_lines = sum(1 + max(1, len(lines)) for _, lines in blocks) if blocks else 1
    line_unit = 5
    min_assessment_h = 44
    box_h = max(min_assessment_h, min(int(max_height), 20 + (estimated_lines * line_unit)))

    pdf.setFillColor(colors_module.white)
    pdf.setStrokeColor(colors_module.HexColor('#444444'))
    pdf.rect(x, y - box_h, width, box_h, stroke=1, fill=1)
    _draw_section_title(pdf, x, y, width, 'Assessments', colors_module)

    inner_top = y - 22

    if not snapshot:
        pdf.setFillColor(colors_module.black)
        pdf.setFont('Helvetica-Oblique', 6)
        pdf.drawString(inner_x, inner_top - 2, 'No assessments found')
    else:
        col_count = 3
        table_gap = 4
        table_w = (inner_w - ((col_count - 1) * table_gap)) / col_count
        column_x = [inner_x + (idx * (table_w + table_gap)) for idx in range(col_count)]
        column_y = [inner_top - 1 for _ in range(col_count)]
        bottom_limit = y - box_h + 4

        def draw_block(base_x: float, start_y: float, header: str, lines: list[str]):
            if start_y - 11 < bottom_limit:
                return start_y, 0

            pdf.setFillColor(colors_module.black)
            header_size = 5.8
            body_size = 5.2
            pdf.setFont('Helvetica-Bold', header_size)
            pdf.drawString(base_x + 2, start_y - 6.2, _fit_single_line(header, 26))

            row_y = start_y - 12
            drawn = 0
            for line in lines:
                if row_y < bottom_limit:
                    break
                pdf.setFont('Helvetica', body_size)
                compact_line = _fit_single_line(str(line), 34)
                pdf.drawString(base_x + 2, row_y, compact_line)
                row_y -= 4
                drawn += 1

            return row_y - 3, drawn

        remaining_blocks = 0
        for header, lines in blocks:
            target_col_idx = max(range(col_count), key=lambda idx: column_y[idx])
            new_y, drawn = draw_block(column_x[target_col_idx], column_y[target_col_idx], header, lines)
            if drawn == 0:
                remaining_blocks += 1
                continue
            column_y[target_col_idx] = new_y

            if drawn < len(lines):
                remaining_blocks += 1

        lowest_y = min(column_y)
        if remaining_blocks > 0 and lowest_y > bottom_limit:
            pdf.setFont('Helvetica-Oblique', 5.3)
            pdf.drawString(inner_x, lowest_y, f'+{remaining_blocks} more assessment groups')

    return y - box_h - 8


def _question_prompt(question) -> str:
    text = question.question_text
    if question.question_type == 'checkbox':
        return f"[   ] {text}"
    if question.question_type == 'choice':
        choices = question.field_options.get('choices', []) if isinstance(question.field_options, dict) else []
        if choices:
            return f"{text}: " + '  '.join([f"( ) {str(choice)}" for choice in choices[:4]])
    if question.question_type == 'student_list':
        return f"{text}:"
    return f"{text}:"


def _draw_questions_columns(pdf: Any, questions, x: int, y: int, width: int, bottom_y: int, colors_module, simple_split):
    title_body_size = 6
    prompt_size = 5.8
    prompt_leading = 6
    line_gap = 7
    after_header_gap = 16

    _draw_section_title(pdf, x, y, width, 'Teacher Notes', colors_module)
    pdf.setFillColor(colors_module.black)
    pdf.setFont('Helvetica', title_body_size)
    y -= after_header_gap

    grouped = defaultdict(list)
    for question in questions:
        grouped[question.question_category].append(question)

    main_categories = ['academic', 'behavior', 'physical', 'attendance', 'parent']
    additional_questions = grouped.get('additional', [])

    def question_height(question) -> int:
        base = 10
        if question.question_type in {'text', 'student_list', 'choice'}:
            base += 12
        if question.question_type == 'textarea':
            base += 20
        if question.requires_detail:
            base += 10
        return base

    additional_header_h = 10
    additional_gap_h = 4
    additional_h = 0
    available_notes_h = max(0, y - (bottom_y + 6))
    active_main_categories = [key for key in main_categories if grouped.get(key, [])]
    reserved_for_main = len(active_main_categories) * 20
    additional_available_h = max(0, available_notes_h - reserved_for_main)
    if additional_questions:
        desired_additional_h = max(42, additional_header_h + additional_gap_h + sum(question_height(q) for q in additional_questions) + 8)
        max_additional_h = max(28, int(additional_available_h * 0.40))
        additional_h = max_additional_h if max_additional_h > 0 else min(desired_additional_h, max_additional_h)

    additional_bottom_padding = 6
    additional_base_y = bottom_y + additional_bottom_padding
    column_bottom = max(bottom_y + 4, additional_base_y + additional_h + 6)

    col_count = 2 if width < 620 else 3
    col_gap = 8
    col_w = (width - ((col_count - 1) * col_gap)) / col_count
    col_x = [x + (i * (col_w + col_gap)) for i in range(col_count)]
    current_col = 0
    current_y = y

    for category_key in main_categories:
        category_questions = grouped.get(category_key, [])
        if not category_questions:
            continue

        needed_height = 16 + sum(question_height(q) for q in category_questions)
        if current_y - needed_height < column_bottom and current_col < (col_count - 1):
            current_col += 1
            current_y = y

        if current_y - 12 < column_bottom and additional_h > 0:
            min_additional_h = 24
            reclaim_needed = (column_bottom - (current_y - 12)) + 4
            reclaimable = max(0, additional_h - min_additional_h)
            reclaim_h = min(reclaimable, reclaim_needed)
            if reclaim_h > 0:
                additional_h -= reclaim_h
                column_bottom = max(bottom_y + 4, additional_base_y + additional_h + 6)

        if current_y - 12 < column_bottom and current_col < (col_count - 1):
            current_col += 1
            current_y = y

        if current_y - 12 < column_bottom:
            continue

        pdf.setFillColor(colors_module.black)
        pdf.setFont('Helvetica-Bold', 7)
        pdf.drawString(col_x[current_col] + 4, current_y - 6, CATEGORY_LABELS.get(category_key, category_key.title()))
        current_y -= 13

        for question in category_questions:
            if current_y - question_height(question) < column_bottom:
                break
            prompt = _question_prompt(question)
            current_y = _draw_text_wrapped(pdf, prompt, int(col_x[current_col] + 4), int(current_y), int(col_w - 8), simple_split, font_name='Helvetica', font_size=prompt_size, leading=prompt_leading)
            if question.question_type in {'text', 'textarea', 'student_list', 'choice'}:
                pdf.line(col_x[current_col] + 4, current_y + 1, col_x[current_col] + col_w - 4, current_y + 1)
                current_y -= line_gap
            if question.question_type == 'textarea':
                pdf.line(col_x[current_col] + 4, current_y + 1, col_x[current_col] + col_w - 4, current_y + 1)
                current_y -= line_gap
                pdf.line(col_x[current_col] + 4, current_y + 1, col_x[current_col] + col_w - 4, current_y + 1)
                current_y -= line_gap
            if question.requires_detail:
                pdf.setFont('Helvetica', prompt_size)
                pdf.drawString(col_x[current_col] + 8, current_y, 'Details: ____________________________')
                current_y -= (line_gap + 1)
            current_y -= 2

    if not additional_questions:
        return

    additional_top = additional_base_y + additional_h if additional_h > 0 else additional_base_y

    pdf.setFillColor(colors_module.white)
    pdf.setStrokeColor(colors_module.HexColor('#777777'))
    pdf.rect(x, additional_base_y, width, additional_h, stroke=1, fill=0)

    add_y = additional_top
    pdf.setFillColor(colors_module.black)
    pdf.setFont('Helvetica-Bold', 7)
    pdf.drawString(x + 4, add_y - 6, CATEGORY_LABELS.get('additional', 'Additional Information'))
    add_y -= 13

    additional_content_floor = additional_base_y + 6

    for question in additional_questions:
        if add_y - question_height(question) < additional_content_floor:
            break
        prompt = _question_prompt(question)
        add_y = _draw_text_wrapped(pdf, prompt, int(x + 4), int(add_y), int(width - 8), simple_split, font_name='Helvetica', font_size=prompt_size, leading=prompt_leading)
        normalized_question_text = str(getattr(question, 'question_text', '') or '').strip().lower().rstrip(':')
        is_additional_comments = normalized_question_text in {
            'additional comments',
            'additional comment',
            'other information',
            'other info',
            'other informations',
        }
        if (question.question_type in {'text', 'textarea', 'student_list', 'choice'}) and not is_additional_comments:
            pdf.line(x + 4, add_y + 1, x + width - 4, add_y + 1)
            add_y -= line_gap
        if question.question_type == 'textarea' and not is_additional_comments:
            pdf.line(x + 4, add_y + 1, x + width - 4, add_y + 1)
            add_y -= line_gap
            pdf.line(x + 4, add_y + 1, x + width - 4, add_y + 1)
            add_y -= line_gap
        if question.requires_detail:
            pdf.setFont('Helvetica', prompt_size)
            pdf.drawString(x + 8, add_y, 'Details: _________________________________________________')
            add_y -= (line_gap + 1)
        add_y -= 2


def _draw_student_page(pdf: Any, student, school_year: str, questions, colors_module, simple_split, assessment_map, profile_map, homeroom_map, frame_x: float, frame_top: float, frame_w: float, frame_h: float):
    x = frame_x
    y = frame_top
    content_w = frame_w
    bottom_y = frame_top - frame_h + 8

    pdf.setStrokeColor(colors_module.black)
    pdf.setFillColor(colors_module.white)
    pdf.rect(x, y - 24, content_w, 22, stroke=1, fill=1)
    pdf.setFillColor(colors_module.black)
    profile = profile_map.get(student.ssid, {})
    student_name = profile.get('student_name', student.student_name or 'Student')
    grade = profile.get('grade', student.grade or '—')
    school = profile.get('school', '—')
    homeroom = homeroom_map.get(student.ssid, '—')

    left_header = f"{student_name}"
    left_header_line2 = f"School: {school}  |  Grade: {grade}  |  Homeroom: {homeroom}"
    pdf.setFont('Helvetica-Bold', 7)
    row_y = _draw_text_wrapped(pdf, left_header, x + 6, y - 9, int(content_w - 180), simple_split, font_name='Helvetica-Bold', font_size=7.4, leading=8)
    pdf.setFont('Helvetica', 6.5)
    _draw_text_wrapped(pdf, left_header_line2, x + 6, row_y + 1, int(content_w - 180), simple_split, font_name='Helvetica', font_size=6.4, leading=7)
    pdf.setFont('Helvetica', 7)
    right_x = x + content_w - 8
    short_year = _short_school_year(school_year)
    pdf.drawRightString(right_x, y - 8, f'School Year: {short_year}')
    pdf.drawRightString(right_x, y - 16, 'Next Year Classroom: ____________________')
    y -= 30

    y = _draw_profile_three_columns(pdf, profile, x, y, content_w, colors_module, simple_split)
    remaining_space = y - bottom_y
    is_portrait_frame = frame_h > frame_w
    assessment_ratio = 0.16 if is_portrait_frame else 0.26
    assessment_min_h = 44
    assessment_max_h = max(assessment_min_h, remaining_space * assessment_ratio)
    y = _draw_assessment_three_columns(pdf, student.ssid, x, y, content_w, colors_module, simple_split, assessment_map, assessment_max_h)

    _draw_questions_columns(pdf, questions, x, y, content_w, bottom_y, colors_module, simple_split)

    footer_text = f'Generated for · {student.student_name or student.ssid}'
    footer_offset = 4
    _draw_text_wrapped(pdf, footer_text, x, bottom_y - footer_offset, content_w, simple_split=simple_split, font_size=5.5, leading=6)


def _render_students_layout(
    pdf,
    students,
    school_year,
    questions,
    colors_module,
    simple_split,
    assessment_map,
    profile_map,
    homeroom_map,
    page_size,
    layout_mode: str,
):
    page_w, page_h = page_size
    outer = 16

    if layout_mode == 'single':
        panel_w = page_w - (outer * 2)
        panel_h = page_h - (outer * 2)
        for student in students:
            _draw_student_page(
                pdf,
                student,
                school_year,
                questions,
                colors_module,
                simple_split,
                assessment_map,
                profile_map,
                homeroom_map,
                outer,
                page_h - outer,
                panel_w,
                panel_h,
            )
            pdf.showPage()
        return

    panel_gap = 12
    panel_w = (page_w - (outer * 2) - panel_gap) / 2
    panel_h = page_h - (outer * 2)

    for idx, student in enumerate(students):
        panel_x = outer if idx % 2 == 0 else outer + panel_w + panel_gap
        panel_top = page_h - outer
        _draw_student_page(
            pdf,
            student,
            school_year,
            questions,
            colors_module,
            simple_split,
            assessment_map,
            profile_map,
            homeroom_map,
            panel_x,
            panel_top,
            panel_w,
            panel_h,
        )
        if idx % 2 == 1 or idx == len(students) - 1:
            pdf.showPage()


def _pdf_response(students, school_year: str, questions, filename: str, layout_mode: str = 'half', break_by_teacher: bool = False):
    reportlab_modules = _load_reportlab()
    if reportlab_modules is None:
        return HttpResponse('PDF export unavailable: install reportlab in the application environment.', status=503)
    colors_module, portrait_letter, landscape_letter, simple_split, canvas_module = reportlab_modules

    page_size = portrait_letter if layout_mode == 'single' else landscape_letter

    buffer = BytesIO()
    pdf = canvas_module.Canvas(buffer, pagesize=page_size)
    pdf.setTitle('Placement Cards Offline Form')
    assessment_map = _assessment_snapshot_map([student.ssid for student in students], school_year)
    profile_map, homeroom_map = _student_profile_map(students, school_year)

    if break_by_teacher:
        grouped: dict[str, list] = defaultdict(list)
        for student in students:
            grouped[homeroom_map.get(student.ssid, 'Unassigned') or 'Unassigned'].append(student)

        teacher_names = sorted(grouped.keys())
        for teacher_name in teacher_names:
            teacher_students = sorted(grouped[teacher_name], key=lambda s: (s.student_name or '', s.ssid or ''))
            _render_students_layout(
                pdf,
                teacher_students,
                school_year,
                questions,
                colors_module,
                simple_split,
                assessment_map,
                profile_map,
                homeroom_map,
                page_size,
                layout_mode,
            )
    else:
        sorted_students = sorted(students, key=lambda s: (homeroom_map.get(s.ssid, ''), s.student_name or '', s.ssid or ''))
        _render_students_layout(
            pdf,
            sorted_students,
            school_year,
            questions,
            colors_module,
            simple_split,
            assessment_map,
            profile_map,
            homeroom_map,
            page_size,
            layout_mode,
        )

    pdf.save()
    pdf_data = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def print_placement_cards(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_place_students(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = request.GET.get('school_year', '').strip()
    cards = PlacementCard.objects.filter(school_year=school_year).prefetch_related('tags').order_by('ssid')
    students = {
        student.ssid: student
        for student in CurrentStudent.objects.filter(ssid__in=cards.values('ssid'))
    }
    placements = {
        placement.student_ssid: placement
        for placement in StudentPlacement.objects.filter(school_year=school_year).select_related('classroom')
    }

    rows = []
    for card in cards:
        student = students.get(card.ssid)
        rows.append(
            {
                'card': card,
                'student': student,
                'placement': placements.get(card.ssid),
            }
        )

    return render(request, 'placementcards/print_cards.html', {'rows': rows, 'school_year': school_year})


@login_required
def export_placements_csv(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_place_students(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = request.GET.get('school_year', '').strip()
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="placement_cards_{school_year}.csv"'

    writer = csv.writer(response)
    writer.writerow(['ssid', 'student_name', 'current_grade', 'next_classroom', 'placement_date', 'completed_by', 'tags'])

    cards = PlacementCard.objects.filter(school_year=school_year).prefetch_related('tags').select_related('completed_by')
    students = {
        student.ssid: student
        for student in CurrentStudent.objects.filter(ssid__in=cards.values('ssid')).only('ssid', 'student_name', 'grade')
    }
    placements = {
        placement.student_ssid: placement
        for placement in StudentPlacement.objects.filter(school_year=school_year).select_related('classroom')
    }

    for card in cards:
        student = students.get(card.ssid)
        placement = placements.get(card.ssid)
        writer.writerow(
            [
                card.ssid,
                student.student_name if student else '',
                student.grade if student else '',
                placement.classroom.classroom_name if placement else '',
                placement.placed_at.isoformat() if placement else '',
                card.completed_by.email if card.completed_by else '',
                ','.join(card.tags.values_list('name', flat=True)),
            ]
        )

    return response


@login_required
def export_teacher_notes_pdf_half(request: HttpRequest):
    school = get_request_school(request)
    if not school:
        return HttpResponse('No active school context', status=400)
    if not can_user_access_teacher_notes(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = _resolve_school_year(request)
    students = _students_for_notes_export(request, school_year=school_year)
    if not students:
        return HttpResponse('No students found for the current view', status=404)

    questions = _questions_for_school(school.pk)
    break_by_teacher = request.GET.get('sort') == 'homeroom' and _is_school_admin(request.user)
    filename = f'teacher_notes_forms_half_{school_year or "current"}_{len(students)}.pdf'
    return _pdf_response(students, school_year, questions, filename, layout_mode='half', break_by_teacher=break_by_teacher)


@login_required
def export_student_notes_pdf(request: HttpRequest, ssid: str):
    school = get_request_school(request)
    if not school:
        return HttpResponse('No active school context', status=400)
    if not can_user_access_teacher_notes(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = _resolve_school_year(request)
    students = _students_for_notes_export(request, school_year=school_year, ssid=ssid)
    if not students:
        return HttpResponse('Student not found for current access scope', status=404)

    questions = _questions_for_school(school.pk)
    filename = f'teacher_notes_form_{ssid}_{school_year or "current"}.pdf'
    return _pdf_response(students[:1], school_year, questions, filename)
