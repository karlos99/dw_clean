"""
Column analyzer for College Board SAT results CSV exports.

Detects student demographics, the LATEST SAT score block, the administration
date, and the four percentile regions (state, country, national, national user).
Also extracts sample rows for the upload preview.
"""
import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Dict, Optional


# Header names used by the College Board SAT results export.
STUDENT_COLUMNS = [
    "AI_CODE",
    "AI_NAME",
    "COHORT_YEAR",
    "NAME_LAST",
    "NAME_FIRST",
    "NAME_MI",
    "GENDER",
    "BIRTH_DATE",
    "CB_ID",
    "DISTRICT_STUDENT_ID",
    "STATE_STUDENT_ID",
    "GRAD_DATE",
]

SCORE_COLUMNS = [
    "LATEST_SAT_DATE",
    "LATEST_SAT_GRADE",
    "LATEST_SAT_TOTAL",
    "LATEST_SAT_EBRW",
    "LATEST_SAT_MATH_SECTION",
]

PERCENTILE_REGIONS = {
    "state": {
        "total": "PERCENTILE_STATE_TOTAL",
        "rw": "PERCENTILE_STATE_RW",
        "math": "PERCENTILE_STATE_MATH",
    },
    "country": {
        "total": "PERCENTILE_COUNTRY_TOTAL",
        "rw": "PERCENTILE_COUNTRY_RW",
        "math": "PERCENTILE_COUNTRY_MATH",
    },
    "natrep": {
        "total": "PERCENTILE_NATREP_SAT_TOTAL",
        "rw": "PERCENTILE_NATREP_SAT_EBRW",
        "math": "PERCENTILE_NATREP_SAT_MATH_SECTION",
    },
    "natuser": {
        "total": "PERCENTILE_NATUSER_SAT_TOTAL",
        "rw": "PERCENTILE_NATUSER_SAT_EBRW",
        "math": "PERCENTILE_NATUSER_SAT_MATH_SECTION",
    },
}

GENDER_MAP = {
    "A": "Not Specified",
    "F": "Female",
    "M": "Male",
}

REQUIRED_COLUMNS = ["NAME_LAST", "NAME_FIRST"]


@dataclass
class SATStudentPreview:
    last_name: str
    first_name: str
    state_student_id: str
    cb_id: str
    dob: str
    school_name: str
    test_date: str
    total_score: str
    ebrw_score: str
    math_score: str


@dataclass
class SATAnalysisResult:
    student_columns: Dict[str, int] = field(default_factory=dict)
    score_columns: Dict[str, int] = field(default_factory=dict)
    percentile_columns: Dict[str, Dict[str, int]] = field(default_factory=dict)
    total_students: int = 0
    total_with_scores: int = 0
    total_missing_state_id: int = 0
    available_percentile_regions: List[str] = field(default_factory=list)
    sample_students: List[SATStudentPreview] = field(default_factory=list)
    header_row_index: int = 0
    total_columns: int = 0
    warnings: List[str] = field(default_factory=list)


def _to_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_int(value) -> Optional[int]:
    """Parse a score/percentile integer, returning None for blanks/sentinels."""
    s = _to_str(value)
    if not s:
        return None
    s = s.replace(",", "").strip()
    if s.upper() in {"N/A", "NA", "NS", "NULL", "NONE", "NAN", "-", "--", "."}:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def parse_date(value) -> Optional[date]:
    """Parse a date from ISO (YYYY-MM-DD), MMDDYYYY, MMDDYY, or datetime."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = _to_str(value)
    if not s:
        return None
    # ISO prefix (e.g. 2026-08-22 or 2026-08-22T00:00:00)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    # Slash formats
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # Compact MMDDYYYY / MMDDYY
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%m%d%Y").date()
        except ValueError:
            return None
    if len(digits) == 6:
        try:
            return datetime.strptime(digits, "%m%d%y").date()
        except ValueError:
            return None
    return None


def detect_header_row(file_content: str, max_rows: int = 5) -> int:
    reader = csv.reader(io.StringIO(file_content))
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        row_text = ",".join(row).upper()
        if "STATE_STUDENT_ID" in row_text or "LATEST_SAT_TOTAL" in row_text:
            return i
    return 0


def analyze_sat_csv(file_content: str, filename: str = '', sample_rows: int = 5) -> SATAnalysisResult:
    result = SATAnalysisResult()
    result.header_row_index = detect_header_row(file_content)

    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)

    if len(rows) <= result.header_row_index:
        result.warnings.append("CSV file appears to be empty or too short")
        return result

    headers = [h.strip() for h in rows[result.header_row_index]]
    result.total_columns = len(headers)
    column_index = {h: i for i, h in enumerate(headers)}

    for col_name in STUDENT_COLUMNS:
        if col_name in column_index:
            result.student_columns[col_name] = column_index[col_name]

    for col_name in SCORE_COLUMNS:
        if col_name in column_index:
            result.score_columns[col_name] = column_index[col_name]

    for region, metrics in PERCENTILE_REGIONS.items():
        found = {}
        for metric, col_name in metrics.items():
            if col_name in column_index:
                found[metric] = column_index[col_name]
        if found:
            result.percentile_columns[region] = found
            result.available_percentile_regions.append(region)

    missing = [c for c in REQUIRED_COLUMNS if c not in result.student_columns]
    if missing:
        result.warnings.append(f"Missing required student columns: {', '.join(missing)}")

    if not any(c in result.student_columns for c in ("STATE_STUDENT_ID", "CB_ID")):
        result.warnings.append("Missing both STATE_STUDENT_ID and CB_ID; matching will rely on name + DOB")

    if "LATEST_SAT_TOTAL" not in result.score_columns:
        result.warnings.append("Missing LATEST_SAT_TOTAL column")

    if not result.percentile_columns:
        result.warnings.append("No percentile columns detected")

    data_rows = rows[result.header_row_index + 1:]
    data_rows = [r for r in data_rows if r and not all(c.strip() == '' for c in r)]
    result.total_students = len(data_rows)

    def cell(row, col_name):
        idx = column_index.get(col_name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    total_with_scores = 0
    total_missing_state_id = 0
    for row in data_rows:
        if parse_int(cell(row, "LATEST_SAT_TOTAL")) is not None or \
           parse_int(cell(row, "LATEST_SAT_EBRW")) is not None or \
           parse_int(cell(row, "LATEST_SAT_MATH_SECTION")) is not None:
            total_with_scores += 1
        if not _to_str(cell(row, "STATE_STUDENT_ID")):
            total_missing_state_id += 1

    result.total_with_scores = total_with_scores
    result.total_missing_state_id = total_missing_state_id

    for row in data_rows[:sample_rows]:
        dob = parse_date(cell(row, "BIRTH_DATE"))
        test_date = parse_date(cell(row, "LATEST_SAT_DATE"))
        result.sample_students.append(SATStudentPreview(
            last_name=_to_str(cell(row, "NAME_LAST")),
            first_name=_to_str(cell(row, "NAME_FIRST")),
            state_student_id=_to_str(cell(row, "STATE_STUDENT_ID")),
            cb_id=_to_str(cell(row, "CB_ID")),
            dob=dob.isoformat() if dob else "",
            school_name=_to_str(cell(row, "AI_NAME")),
            test_date=test_date.isoformat() if test_date else "",
            total_score=_to_str(cell(row, "LATEST_SAT_TOTAL")),
            ebrw_score=_to_str(cell(row, "LATEST_SAT_EBRW")),
            math_score=_to_str(cell(row, "LATEST_SAT_MATH_SECTION")),
        ))

    return result


def parse_sat_csv_rows(file_content: str, header_row_index: int = 0):
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
