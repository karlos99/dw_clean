from collections import defaultdict
from io import BytesIO
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse

from placementcards.view_modules.card_data_utils import get_request_school
from placementcards.view_modules.card_data_utils import can_user_access_placement_cards
from placementcards.view_modules.half_page_print_views import (
    _assessment_snapshot_map,
    _draw_section_title,
    _draw_text_wrapped,
    _is_school_admin,
    _load_reportlab,
    _question_prompt,
    _questions_for_school,
    _resolve_school_year,
    _short_school_year,
    _student_profile_map,
    _students_for_notes_export,
)


def _draw_profile_three_columns_full(pdf: Any, profile: dict[str, str], x: float, y: float, width: float, simple_split, colors_module) -> float:
    profile_fields = [
        ('Student ID', 'student_id'), ('SSID', 'ssid'), ('Grade', 'grade'), ('School Year', 'school_year'),
        ('Birth Date', 'birth_date'), ('Gender', 'gender'), ('Race', 'race'), ('Status', 'status'),
        ('Attendance Rate', 'atendace_rate'), ('School State ID', 'school_state_id'), ('ELL Status', 'ell_status'), ('LTEL', 'ltel'),
        ('FEP Date', 'fep_date'), ('US Enter Date', 'us_enter_date'), ('State Enter Date', 'state_enter_date'),
        ('Instructional Setting', 'instructional_setting'), ('Special Program', 'special_program_code'), ('SPED', 'sped'),
        ('504 Plan', 'in_504'), ('GATE', 'gate'), ('SED', 'sed'), ('Foster', 'foster'),
        ('Homeless', 'homeless'), ('Migrant', 'migrant'), ('DLI', 'dli'), ('Suspension', 'suspension_indicator'),
    ]

    col_count = 3
    rows_per_col = (len(profile_fields) + (col_count - 1)) // col_count
    estimated_h = 22 + (rows_per_col * 9)
    box_h = max(104, min(132, estimated_h))

    pdf.setFillColor(colors_module.white)
    pdf.setStrokeColor(colors_module.HexColor('#444444'))
    pdf.rect(x, y - box_h, width, box_h, stroke=1, fill=1)
    _draw_section_title(pdf, x, y, width, 'Student Profile', colors_module)

    inner_top = y - 22
    inner_x = x + 8
    inner_w = width - 16
    col_gap = 8
    col_w = (inner_w - ((col_count - 1) * col_gap)) / col_count
    col_x_values = [inner_x + (idx * (col_w + col_gap)) for idx in range(col_count)]
    split_fields = [profile_fields[idx * rows_per_col:(idx + 1) * rows_per_col] for idx in range(col_count)]

    for col_idx in range(col_count):
        row_y = inner_top
        label_w = 46
        value_indent = 12
        value_w = int(col_w - label_w - value_indent - 2)
        for label, key in split_fields[col_idx]:
            value = str(profile.get(key, '—') or '—')
            pdf.setFont('Helvetica', 6.4)
            pdf.setFillColor(colors_module.black)
            pdf.drawString(col_x_values[col_idx], row_y, f'{label}:')
            lines = simple_split(value, 'Helvetica-Bold', 6.4, value_w)[:2] or ['—']
            pdf.setFont('Helvetica-Bold', 6.4)
            line_y = row_y
            for line in lines:
                pdf.drawString(col_x_values[col_idx] + label_w + value_indent, line_y, line)
                line_y -= 6
            row_y -= max(9, 2 + (len(lines) * 6))

    return y - box_h - 8


def _draw_assessments_full(pdf: Any, ssid: str, x: float, y: float, width: float, assessment_map, simple_split, colors_module, max_height: float) -> float:
    snapshot = assessment_map.get(ssid, [])
    inner_x = x + 5
    inner_w = width - 10

    blocks: list[tuple[str, list[str]]] = []
    if snapshot:
        iready_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        dibels_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
        state_groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)

        for row in snapshot:
            row_type = row.get('type', '')
            if row_type == 'iReady':
                iready_groups[(row.get('year', '—'), row.get('subject', '—'))].append(row)
            elif row_type == 'DIBELS':
                dibels_groups[row.get('year', '—')].append(row)
            elif row_type in {'ELPAC Summative', 'ELPAC Summative (Regular)', 'CAASPP'}:
                state_groups[(row.get('year', '—'), row.get('test_name', row_type))].append(row)

        for (year, subject) in sorted(iready_groups.keys(), reverse=True):
            rows = sorted(iready_groups[(year, subject)], key=lambda item: item.get('window', ''))
            lines = [f"{(item.get('window', '—') or '—')}: {item.get('pl', '—')}" for item in rows]
            blocks.append((f"iReady {year} · {subject}", lines or ['—']))

        for year in sorted(dibels_groups.keys(), reverse=True):
            rows = sorted(dibels_groups[year], key=lambda item: item.get('window', ''))
            lines = [f"{(item.get('window', '—') or '—')}: {item.get('pl', '—')}" for item in rows]
            blocks.append((f"DIBELS {year}", lines or ['—']))

        for (year, test_name) in sorted(state_groups.keys(), reverse=True):
            rows = state_groups[(year, test_name)]
            seen_pairs: set[str] = set()
            pairs: list[str] = []
            for item in rows:
                pair = f"{str(item.get('subject', '—') or '—').strip()} {str(item.get('pl', '—') or '—').strip()} / {str(item.get('ss', '—') or '—').strip()}"
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                pairs.append(pair)
            blocks.append((f"{year} {test_name} · Subject PL / SS:", pairs or ['— — / —']))

    estimated_lines = sum(1 + max(1, len(lines)) for _, lines in blocks) if blocks else 1
    estimated_h = 24 + (estimated_lines * 4)
    min_h = 58 if snapshot else 48
    box_h = max(min_h, min(int(max_height), estimated_h))
    pdf.setFillColor(colors_module.white)
    pdf.setStrokeColor(colors_module.HexColor('#444444'))
    pdf.rect(x, y - box_h, width, box_h, stroke=1, fill=1)
    _draw_section_title(pdf, x, y, width, 'Assessments', colors_module)

    if not snapshot:
        pdf.setFillColor(colors_module.black)
        pdf.setFont('Helvetica-Oblique', 6.2)
        pdf.drawString(inner_x, y - 25, 'No assessments found')
        return y - box_h - 8

    col_count = 3
    col_gap = 6
    col_w = (inner_w - ((col_count - 1) * col_gap)) / col_count
    col_x = [inner_x + (idx * (col_w + col_gap)) for idx in range(col_count)]
    col_y = [y - 23 for _ in range(col_count)]
    bottom_limit = y - box_h + 6

    for header, lines in blocks:
        target = max(range(col_count), key=lambda idx: col_y[idx])
        if col_y[target] - 10 < bottom_limit:
            continue

        pdf.setFont('Helvetica-Bold', 6.1)
        pdf.setFillColor(colors_module.black)
        pdf.drawString(col_x[target], col_y[target], header[:34])
        row_y = col_y[target] - 6

        pdf.setFont('Helvetica', 5.8)
        for line in lines:
            if row_y < bottom_limit:
                break
            compact = str(line)
            wrapped = simple_split(compact, 'Helvetica', 5.8, int(col_w - 2))[:1] or ['—']
            pdf.drawString(col_x[target], row_y, wrapped[0])
            row_y -= 5

        col_y[target] = row_y - 2

    return y - box_h - 8


def _draw_questions_columns_full(pdf: Any, questions, x: float, y: float, width: float, bottom_y: float, simple_split, colors_module):
    _draw_section_title(pdf, x, y, width, 'Teacher Notes', colors_module)
    y -= 24

    grouped = defaultdict(list)
    for question in questions:
        grouped[question.question_category].append(question)

    main_categories = ['academic', 'behavior', 'physical', 'attendance', 'parent']
    additional_questions = grouped.get('additional', [])

    def category_label(category_key: str) -> str:
        return {
            'academic': 'Academic',
            'behavior': 'Behavior & Study Habits',
            'physical': 'Physical & Health',
            'attendance': 'Behavior & Attendance',
            'parent': 'Parent Involvement',
            'additional': 'Additional Information',
        }.get(category_key, category_key.title())

    def question_height(question) -> int:
        base = 13
        if question.question_type in {'text', 'student_list', 'choice'}:
            base += 14
        if question.question_type == 'textarea':
            base += 26
        if question.requires_detail:
            base += 11
        return base

    additional_header_h = 12
    additional_gap_h = 5
    additional_h = 0
    available_notes_h = max(0, y - (bottom_y + 10))
    active_main_categories = [key for key in main_categories if grouped.get(key, [])]
    reserved_for_main = len(active_main_categories) * 24
    additional_available_h = max(0, available_notes_h - reserved_for_main)
    if additional_questions:
        desired_additional_h = max(72, additional_header_h + additional_gap_h + sum(question_height(q) for q in additional_questions) + 10)
        max_additional_h = max(40, int(additional_available_h * 0.45))
        additional_h = max_additional_h if max_additional_h > 0 else min(desired_additional_h, max_additional_h)

    additional_bottom_padding = 8
    additional_base_y = bottom_y + additional_bottom_padding
    column_bottom = max(bottom_y + 6, additional_base_y + additional_h + 8)

    col_count = 2
    col_gap = 10
    col_w = (width - ((col_count - 1) * col_gap)) / col_count
    col_x = [x + (idx * (col_w + col_gap)) for idx in range(col_count)]
    col_idx = 0
    current_y = y

    for category_key in main_categories:
        category_questions = grouped.get(category_key, [])
        if not category_questions:
            continue

        needed_height = 18 + sum(question_height(q) for q in category_questions)
        if current_y - needed_height < column_bottom and col_idx < (col_count - 1):
            col_idx += 1
            current_y = y

        if current_y - 14 < column_bottom and additional_h > 0:
            min_additional_h = 34
            reclaim_needed = (column_bottom - (current_y - 14)) + 5
            reclaimable = max(0, additional_h - min_additional_h)
            reclaim_h = min(reclaimable, reclaim_needed)
            if reclaim_h > 0:
                additional_h -= reclaim_h
                column_bottom = max(bottom_y + 6, additional_base_y + additional_h + 8)

        if current_y - 14 < column_bottom and col_idx < (col_count - 1):
            col_idx += 1
            current_y = y

        if current_y - 14 < column_bottom:
            continue

        pdf.setFont('Helvetica-Bold', 8.3)
        pdf.setFillColor(colors_module.black)
        pdf.drawString(col_x[col_idx] + 3, current_y - 6, category_label(category_key))
        current_y -= 15

        for question in category_questions:
            if current_y - question_height(question) < column_bottom:
                break

            prompt = _question_prompt(question)
            current_y = _draw_text_wrapped(
                pdf,
                prompt,
                int(col_x[col_idx] + 3),
                int(current_y),
                int(col_w - 6),
                simple_split,
                font_name='Helvetica',
                font_size=7.1,
                leading=9,
            )

            if question.question_type in {'text', 'textarea', 'student_list', 'choice'}:
                pdf.line(col_x[col_idx] + 3, current_y + 2, col_x[col_idx] + col_w - 3, current_y + 2)
                current_y -= 10
            if question.question_type == 'textarea':
                pdf.line(col_x[col_idx] + 3, current_y + 2, col_x[col_idx] + col_w - 3, current_y + 2)
                current_y -= 10
                pdf.line(col_x[col_idx] + 3, current_y + 2, col_x[col_idx] + col_w - 3, current_y + 2)
                current_y -= 10
            if question.requires_detail:
                pdf.setFont('Helvetica', 7.1)
                pdf.drawString(col_x[col_idx] + 7, current_y, 'Details: ______________________')
                current_y -= 10
            current_y -= 3

    if not additional_questions:
        return

    additional_top = additional_base_y + additional_h if additional_h > 0 else additional_base_y
    pdf.setFillColor(colors_module.white)
    pdf.setStrokeColor(colors_module.HexColor('#777777'))
    pdf.rect(x, additional_base_y, width, additional_h, stroke=1, fill=0)

    add_y = additional_top
    pdf.setFillColor(colors_module.black)
    pdf.setFont('Helvetica-Bold', 8.1)
    pdf.drawString(x + 4, add_y - 6, category_label('additional'))
    add_y -= 14

    additional_content_floor = additional_base_y + 7

    for question in additional_questions:
        if add_y - question_height(question) < additional_content_floor:
            break

        prompt = _question_prompt(question)
        add_y = _draw_text_wrapped(
            pdf,
            prompt,
            int(x + 4),
            int(add_y),
            int(width - 8),
            simple_split,
            font_name='Helvetica',
            font_size=7.1,
            leading=9,
        )

        normalized_question_text = str(getattr(question, 'question_text', '') or '').strip().lower().rstrip(':')
        is_additional_comments = normalized_question_text in {
            'additional comments',
            'additional comment',
            'other information',
            'other info',
            'other informations',
        }

        if question.question_type in {'text', 'textarea', 'student_list', 'choice'} and not is_additional_comments:
            pdf.line(x + 4, add_y + 2, x + width - 4, add_y + 2)
            add_y -= 10
        if question.question_type == 'textarea' and not is_additional_comments:
            pdf.line(x + 4, add_y + 2, x + width - 4, add_y + 2)
            add_y -= 10
            pdf.line(x + 4, add_y + 2, x + width - 4, add_y + 2)
            add_y -= 10
        if question.requires_detail:
            pdf.setFont('Helvetica', 7.1)
            pdf.drawString(x + 8, add_y, 'Details: _________________________________________________')
            add_y -= 10
        add_y -= 3


def _draw_student_page_full(
    pdf: Any,
    student,
    school_year: str,
    questions,
    assessment_map,
    profile_map,
    homeroom_map,
    page_w: float,
    page_h: float,
    colors_module,
    simple_split,
):
    x = 16
    y = page_h - 16
    width = page_w - 32
    bottom_y = 18

    pdf.setStrokeColor(colors_module.black)
    pdf.setFillColor(colors_module.white)
    pdf.rect(x, y - 24, width, 22, stroke=1, fill=1)

    profile = profile_map.get(student.ssid, {})
    student_name = profile.get('student_name', student.student_name or 'Student')
    grade = profile.get('grade', student.grade or '—')
    school = profile.get('school', '—')
    homeroom = homeroom_map.get(student.ssid, '—')

    pdf.setFillColor(colors_module.black)
    row_y = _draw_text_wrapped(pdf, f"{student_name}", x + 6, y - 9, int(width - 190), simple_split, font_name='Helvetica-Bold', font_size=7.6, leading=8)
    _draw_text_wrapped(pdf, f"School: {school}  |  Grade: {grade}  |  Homeroom: {homeroom}", x + 6, row_y + 1, int(width - 190), simple_split, font_name='Helvetica', font_size=6.5, leading=7)

    right_x = x + width - 8
    pdf.setFont('Helvetica', 7)
    pdf.drawRightString(right_x, y - 8, f"School Year: {_short_school_year(school_year)}")
    pdf.drawRightString(right_x, y - 16, 'Next Year Classroom: ____________________')

    y -= 30
    y = _draw_profile_three_columns_full(pdf, profile, x, y, width, simple_split, colors_module)

    remaining = y - bottom_y
    y = _draw_assessments_full(pdf, student.ssid, x, y, width, assessment_map, simple_split, colors_module, remaining * 0.22)

    _draw_questions_columns_full(pdf, questions, x, y, width, bottom_y, simple_split, colors_module)
    _draw_text_wrapped(pdf, f'Generated for · {student.student_name or student.ssid}', x, bottom_y - 2, width, simple_split=simple_split, font_size=5.7, leading=6)


def _pdf_response_full(students, school_year: str, questions, filename: str, break_by_teacher: bool = False):
    reportlab_modules = _load_reportlab()
    if reportlab_modules is None:
        return HttpResponse('PDF export unavailable: install reportlab in the application environment.', status=503)

    colors_module, portrait_letter, _, simple_split, canvas_module = reportlab_modules
    page_w, page_h = portrait_letter

    buffer = BytesIO()
    pdf = canvas_module.Canvas(buffer, pagesize=portrait_letter)
    pdf.setTitle('Placement Cards Offline Form (Full Page)')

    assessment_map = _assessment_snapshot_map([student.ssid for student in students], school_year)
    profile_map, homeroom_map = _student_profile_map(students, school_year)

    if break_by_teacher:
        grouped: dict[str, list] = defaultdict(list)
        for student in students:
            grouped[homeroom_map.get(student.ssid, 'Unassigned') or 'Unassigned'].append(student)
        ordered_students: list = []
        for teacher_name in sorted(grouped.keys()):
            ordered_students.extend(sorted(grouped[teacher_name], key=lambda s: (s.student_name or '', s.ssid or '')))
    else:
        ordered_students = sorted(students, key=lambda s: (homeroom_map.get(s.ssid, ''), s.student_name or '', s.ssid or ''))

    for student in ordered_students:
        _draw_student_page_full(
            pdf,
            student,
            school_year,
            questions,
            assessment_map,
            profile_map,
            homeroom_map,
            page_w,
            page_h,
            colors_module,
            simple_split,
        )
        pdf.showPage()

    pdf.save()
    pdf_data = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_teacher_notes_pdf_full(request: HttpRequest):
    school = get_request_school(request)
    if not school:
        return HttpResponse('No active school context', status=400)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = _resolve_school_year(request)
    students = _students_for_notes_export(request, school_year=school_year)
    if not students:
        return HttpResponse('No students found for the current view', status=404)

    questions = _questions_for_school(school.pk)
    break_by_teacher = request.GET.get('sort') == 'homeroom' and _is_school_admin(request.user)
    filename = f'teacher_notes_forms_full_{school_year or "current"}_{len(students)}.pdf'
    return _pdf_response_full(students, school_year, questions, filename, break_by_teacher=break_by_teacher)


@login_required
def export_student_notes_pdf_full(request: HttpRequest, ssid: str):
    school = get_request_school(request)
    if not school:
        return HttpResponse('No active school context', status=400)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = _resolve_school_year(request)
    students = _students_for_notes_export(request, school_year=school_year, ssid=ssid)
    if not students:
        return HttpResponse('Student not found for current access scope', status=404)

    questions = _questions_for_school(school.pk)
    filename = f'teacher_notes_form_full_{ssid}_{school_year or "current"}.pdf'
    return _pdf_response_full(students[:1], school_year, questions, filename)
