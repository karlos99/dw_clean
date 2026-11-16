"""
Column analyzer for College Board PSAT results CSV exports (PSAT 8/9 and PSAT/NMSQT).

Detects exam type, core score columns, and builds sample rows for upload preview.
Additional response fields are stored separately as JSON during import (see psat_response_loader).
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from dataLoader.utils.sat_column_analyzer import parse_int, parse_date, _to_str


def detect_psat_header_row(file_content: str, max_rows: int = 5) -> int:
    reader = csv.reader(io.StringIO(file_content))
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        row_text = ','.join(row).upper()
        if 'STATE_STUDENT_ID' in row_text or 'LATEST_PSAT_TOTAL' in row_text:
            return i
    return 0

PSAT_TYPE_89 = 'PSAT89'
PSAT_TYPE_NMSQT = 'PSATNM'

SCORE_COLUMNS = {
    'test_date': 'LATEST_PSAT_DATE',
    'test_grade': 'LATEST_PSAT_GRADE',
    'total_score': 'LATEST_PSAT_TOTAL',
    'ebrw_score': 'LATEST_PSAT_EBRW',
    'math_score': 'LATEST_PSAT_MATH_SECTION',
}

REQUIRED_COLUMNS = ['NAME_LAST', 'NAME_FIRST']

# Demographics / identity — excluded from response_data JSON
DEMOGRAPHIC_COLUMNS = frozenset({
    'AI_CODE', 'AI_NAME', 'COHORT_YEAR', 'DISTRICT_NAME',
    'NAME_LAST', 'NAME_FIRST', 'NAME_MI', 'GENDER', 'BIRTH_DATE',
    'CB_ID', 'SECONDARY_ID', 'DISTRICT_STUDENT_ID', 'STATE_STUDENT_ID',
    'GRAD_DATE', 'HS_STUDENT', 'YRS_9TO12', 'GPO',
    'ADDRESS_LINE1', 'ADDRESS_LINE2', 'ADDRESS_CITY', 'ADDRESS_STATE',
    'ADDRESS_ZIP', 'ADDRESS_COUNTY', 'ADDRESS_COUNTRY', 'ADDRESS_PROVINCE',
})

PROMOTED_COLUMNS = frozenset(SCORE_COLUMNS.values())


@dataclass
class PSATStudentPreview:
    last_name: str
    first_name: str
    state_student_id: str
    district_student_id: str
    cb_id: str
    dob: str
    school_code: str
    school_name: str
    test_date: str
    total_score: str
    ebrw_score: str
    math_score: str


@dataclass
class PSATAnalysisResult:
    psat_type: str = PSAT_TYPE_89
    psat_type_label: str = 'PSAT 8/9'
    student_columns: Dict[str, int] = field(default_factory=dict)
    score_columns: Dict[str, int] = field(default_factory=dict)
    total_students: int = 0
    total_with_scores: int = 0
    total_missing_state_id: int = 0
    sample_students: List[PSATStudentPreview] = field(default_factory=list)
    header_row_index: int = 0
    total_columns: int = 0
    inferred_school_year: str = ''
    warnings: List[str] = field(default_factory=list)


def school_year_for_date(value) -> str:
    """Return the school year (e.g. '2026-2027') that a test/report date belongs to.

    Fall administrations (July–December) belong to the school year starting
    that calendar year; spring administrations (January–June) belong to the
    school year that started the previous calendar year.
    """
    if not value:
        return ''
    if isinstance(value, str):
        value = parse_date(value)
    if not value:
        return ''
    if value.month >= 7:
        return f'{value.year}-{value.year + 1}'
    return f'{value.year - 1}-{value.year}'


def detect_psat_type(headers: List[str], filename: str = '') -> tuple[str, str]:
    """Return (type_code, display_label) for PSAT 8/9 vs PSAT/NMSQT."""
    header_set = {h.strip().upper() for h in headers}
    name_upper = (filename or '').upper()
    if 'NATIONAL_MERIT' in header_set or 'SELECTION_INDEX' in header_set:
        return PSAT_TYPE_NMSQT, 'PSAT/NMSQT'
    if 'PSATNM' in name_upper or 'NMSQT' in name_upper:
        return PSAT_TYPE_NMSQT, 'PSAT/NMSQT'
    if 'PSAT89' in name_upper or 'PSAT8' in name_upper:
        return PSAT_TYPE_89, 'PSAT 8/9'
    if 'LATEST_PSAT_TOTAL' in header_set:
        return PSAT_TYPE_89, 'PSAT 8/9'
    return PSAT_TYPE_89, 'PSAT 8/9'


def is_demographic_column(column: str) -> bool:
    col = (column or '').strip().upper()
    if not col:
        return True
    if col in DEMOGRAPHIC_COLUMNS:
        return True
    if col.startswith('FILLER_'):
        return True
    if col.startswith('ADDRESS_'):
        return True
    return False


def build_response_data(row: Dict[str, str]) -> Dict[str, str]:
    """Non-demographic response fields only (scores promoted to columns are omitted)."""
    payload: Dict[str, str] = {}
    for key, value in row.items():
        col = (key or '').strip()
        if not col:
            continue
        col_upper = col.upper()
        if is_demographic_column(col_upper):
            continue
        if col_upper in PROMOTED_COLUMNS:
            continue
        val = _to_str(value)
        if val == '':
            continue
        payload[col] = val
    return payload


def analyze_psat_csv(file_content: str, filename: str = '', sample_rows: int = 5) -> PSATAnalysisResult:
    result = PSATAnalysisResult()
    result.header_row_index = detect_psat_header_row(file_content)

    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)

    if len(rows) <= result.header_row_index:
        result.warnings.append('CSV file appears to be empty or too short')
        return result

    headers = [h.strip() for h in rows[result.header_row_index]]
    result.total_columns = len(headers)
    column_index = {h: i for i, h in enumerate(headers)}

    psat_type, psat_label = detect_psat_type(headers, filename)
    result.psat_type = psat_type
    result.psat_type_label = psat_label

    identity_cols = (
        'AI_CODE', 'AI_NAME', 'NAME_LAST', 'NAME_FIRST', 'BIRTH_DATE',
        'CB_ID', 'DISTRICT_STUDENT_ID', 'STATE_STUDENT_ID',
    )
    for col_name in identity_cols:
        if col_name in column_index:
            result.student_columns[col_name] = column_index[col_name]

    for field_name, col_name in SCORE_COLUMNS.items():
        if col_name in column_index:
            result.score_columns[field_name] = column_index[col_name]

    missing = [c for c in REQUIRED_COLUMNS if c not in result.student_columns]
    if missing:
        result.warnings.append(f"Missing required student columns: {', '.join(missing)}")

    if 'LATEST_PSAT_TOTAL' not in column_index:
        result.warnings.append('Missing LATEST_PSAT_TOTAL column — is this a PSAT results file?')

    if not any(c in result.student_columns for c in ('STATE_STUDENT_ID', 'DISTRICT_STUDENT_ID', 'CB_ID')):
        result.warnings.append(
            'Missing STATE_STUDENT_ID, DISTRICT_STUDENT_ID, and CB_ID; matching will rely on name + DOB'
        )

    data_rows = rows[result.header_row_index + 1:]
    data_rows = [r for r in data_rows if r and not all(c.strip() == '' for c in r)]
    result.total_students = len(data_rows)

    def cell(row, col_name):
        idx = column_index.get(col_name)
        if idx is None or idx >= len(row):
            return ''
        return row[idx]

    total_with_scores = 0
    total_missing_state_id = 0
    test_dates: List[date] = []
    for row in data_rows:
        if any(
            parse_int(cell(row, col)) is not None
            for col in (
                'LATEST_PSAT_TOTAL',
                'LATEST_PSAT_EBRW',
                'LATEST_PSAT_MATH_SECTION',
            )
        ):
            total_with_scores += 1
        if not _to_str(cell(row, 'STATE_STUDENT_ID')):
            total_missing_state_id += 1
        test_date = parse_date(cell(row, 'LATEST_PSAT_DATE'))
        if test_date:
            test_dates.append(test_date)

    inferred_date = max(test_dates) if test_dates else None
    if inferred_date is None and data_rows:
        inferred_date = parse_date(cell(data_rows[0], 'REPORT_DATE'))
    result.inferred_school_year = school_year_for_date(inferred_date)

    result.total_with_scores = total_with_scores
    result.total_missing_state_id = total_missing_state_id

    for row in data_rows[:sample_rows]:
        dob = parse_date(cell(row, 'BIRTH_DATE'))
        test_date = parse_date(cell(row, 'LATEST_PSAT_DATE'))
        result.sample_students.append(PSATStudentPreview(
            last_name=_to_str(cell(row, 'NAME_LAST')),
            first_name=_to_str(cell(row, 'NAME_FIRST')),
            state_student_id=_to_str(cell(row, 'STATE_STUDENT_ID')),
            district_student_id=_to_str(cell(row, 'DISTRICT_STUDENT_ID')),
            cb_id=_to_str(cell(row, 'CB_ID')),
            dob=dob.isoformat() if dob else '',
            school_code=_to_str(cell(row, 'AI_CODE')),
            school_name=_to_str(cell(row, 'AI_NAME')),
            test_date=test_date.isoformat() if test_date else '',
            total_score=_to_str(cell(row, 'LATEST_PSAT_TOTAL')),
            ebrw_score=_to_str(cell(row, 'LATEST_PSAT_EBRW')),
            math_score=_to_str(cell(row, 'LATEST_PSAT_MATH_SECTION')),
        ))

    return result


def parse_psat_csv_rows(file_content: str, header_row_index: int = 0):
    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)

    if len(rows) <= header_row_index:
        return

    headers = [h.strip() for h in rows[header_row_index]]

    for row in rows[header_row_index + 1:]:
        if not row or all(cell.strip() == '' for cell in row):
            continue

        row_dict = {}
        for i, header in enumerate(headers):
            row_dict[header] = row[i].strip() if i < len(row) else ''
        yield row_dict
