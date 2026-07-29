"""
Column analyzer for College Board AP Student Datafile CSV exports.

Detects exam blocks, award blocks, student demographics, and extracts
sample data for preview display. Handles the wide-format AP data layout.
"""
import csv
import io
from dataclasses import dataclass, field
from typing import List, Dict, Optional


EXAM_CODES = {
    "07": "United States History",
    "10": "African American Studies",
    "13": "Art History",
    "14": "Drawing",
    "15": "2-D Art and Design",
    "16": "3-D Art and Design",
    "20": "Biology",
    "22": "Seminar",
    "23": "Research",
    "25": "Chemistry",
    "28": "Chinese Language and Culture",
    "31": "Computer Science A",
    "32": "Computer Science Principles",
    "33": "Computer Science AB",
    "34": "Microeconomics",
    "35": "Macroeconomics",
    "36": "English Language and Composition",
    "37": "English Literature and Composition",
    "40": "Environmental Science",
    "43": "European History",
    "48": "French Language and Culture",
    "51": "French Literature",
    "53": "Human Geography",
    "55": "German Language and Culture",
    "57": "United States Government and Politics",
    "58": "Comparative Government and Politics",
    "60": "Latin",
    "61": "Latin Literature",
    "62": "Italian Language and Culture",
    "64": "Japanese Language and Culture",
    "65": "Precalculus",
    "66": "Calculus AB",
    "68": "Calculus BC",
    "69": "Calculus BC: AB Subscore",
    "70": "Networking",
    "71": "Cybersecurity",
    "75": "Music Theory",
    "76": "Music Aural Subscore",
    "77": "Music Non-Aural Subscore",
    "78": "Physics B",
    "80": "Physics C: Mechanics",
    "82": "Physics C: Electricity and Magnetism",
    "83": "Physics 1",
    "84": "Physics 2",
    "85": "Psychology",
    "87": "Spanish Language and Culture",
    "89": "Spanish Literature and Culture",
    "90": "Statistics",
    "93": "World History: Modern",
}

AWARD_TYPES = {
    "01": "AP Scholar",
    "02": "AP Scholar with Honor",
    "03": "AP Scholar with Distinction",
    "04": "State AP Scholar",
    "05": "National AP Scholar",
    "06": "National AP Scholar (Canada)",
    "07": "AP International Diploma",
    "08": "DoDEA AP Scholar",
    "09": "International AP Scholar",
    "12": "National AP Scholar (Bermuda)",
    "13": "AP Capstone Diploma",
    "14": "AP Seminar and Research Certificate",
}

GRADE_LEVELS = {
    "3": "< 9th Grade",
    "4": "9th Grade",
    "5": "10th Grade",
    "6": "11th Grade",
    "7": "12th Grade",
    "8": "No longer in high school",
    "11": "Unknown",
}

GENDER_MAP = {
    "A": "Not Specified",
    "F": "Female",
    "M": "Male",
}

STUDENT_COLUMNS = [
    "AP Number / AP ID",
    "Last Name",
    "First Name",
    "Middle Initial",
    "Gender",
    "Date of Birth",
    "Grade Level",
    "Student State",
    "Student Zip Code",
    "AI Code",
    "AI Institution Name",
]

SCHOOL_MAP = {
    "Indio High School": "Indio HS",
    "La Quinta High School": "La Quinta HS",
    "Palm Desert High School": "Palm Desert HS",
    "Shadow Hills High School": "Shadow Hills HS",
    "John Glenn Middle School of In": "Glenn",
    "La Quinta Middle School": "La Quinta MS",
}


@dataclass
class APExamBlock:
    index: int
    exam_code: str
    exam_name: str
    admin_year: str
    exam_grade: str


@dataclass
class APAwardBlock:
    index: int
    award_type: str
    award_description: str
    award_year: str


@dataclass
class APStudentPreview:
    ap_number: str
    last_name: str
    first_name: str
    dob: str
    grade_level: str
    school_name: str
    gender: str
    exam_count: int
    award_count: int
    exam_names: List[str] = field(default_factory=list)


@dataclass
class APAnalysisResult:
    student_columns: Dict[str, int] = field(default_factory=dict)
    active_exam_blocks: List[int] = field(default_factory=list)
    active_award_blocks: List[int] = field(default_factory=list)
    total_students: int = 0
    total_exams: int = 0
    total_awards: int = 0
    unique_exam_codes: set = field(default_factory=set)
    sample_students: List[APStudentPreview] = field(default_factory=list)
    header_row_index: int = 0
    total_columns: int = 0
    warnings: List[str] = field(default_factory=list)


def _to_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _format_yy_year(value, prefix: str = "20") -> str:
    s = _to_str(value)
    if not s:
        return ""
    try:
        return f"{prefix}{int(s):02d}"
    except ValueError:
        return s


def format_date_of_birth(value) -> str:
    s = _to_str(value)
    if not s or len(s) < 5:
        return ""
    s = s.zfill(6)
    mm, dd, yy = s[0:2], s[2:4], s[4:6]
    year_int = int(yy)
    full_year = 1900 + year_int if year_int >= 50 else 2000 + year_int
    return f"{mm}/{dd}/{full_year}"


def _admin_year_to_school_year(admin_year: str) -> str:
    if not admin_year or len(admin_year) != 4:
        return admin_year
    try:
        year = int(admin_year)
        return f"{year - 1}-{year}"
    except ValueError:
        return admin_year


def detect_header_row(file_content: str, max_rows: int = 5) -> int:
    reader = csv.reader(io.StringIO(file_content))
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        row_text = ','.join(row).lower()
        if 'exam code 01' in row_text or 'ap number' in row_text:
            return i
    return 0


def analyze_ap_csv(file_content: str, filename: str = '', sample_rows: int = 5) -> APAnalysisResult:
    result = APAnalysisResult()

    result.header_row_index = detect_header_row(file_content)

    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)

    if len(rows) <= result.header_row_index:
        result.warnings.append("CSV file appears to be empty or too short")
        return result

    headers = rows[result.header_row_index]
    result.total_columns = len(headers)
    column_index = {h.strip(): i for i, h in enumerate(headers)}

    for col_name in STUDENT_COLUMNS:
        if col_name in column_index:
            result.student_columns[col_name] = column_index[col_name]

    required = ["AP Number / AP ID", "Last Name", "First Name", "AI Institution Name"]
    missing = [c for c in required if c not in result.student_columns]
    if missing:
        result.warnings.append(f"Missing required student columns: {', '.join(missing)}")

    for i in range(1, 31):
        col_name = f"Exam Code {i:02d}"
        if col_name in column_index:
            result.active_exam_blocks.append(i)

    if not result.active_exam_blocks:
        result.warnings.append("No exam blocks (Exam Code 01-30) detected")

    for i in range(1, 7):
        col_name = f"Award Type {i}"
        if col_name in column_index:
            result.active_award_blocks.append(i)

    data_start = result.header_row_index + 1
    data_rows = rows[data_start:]

    total_exams = 0
    total_awards = 0
    unique_codes = set()

    for row in data_rows[:sample_rows * 3]:
        for i in result.active_exam_blocks:
            code_col = f"Exam Code {i:02d}"
            if code_col in column_index:
                val = _to_str(row[column_index[code_col]]) if column_index[code_col] < len(row) else ""
                if val and val != "00":
                    unique_codes.add(val.zfill(2))

    for row in data_rows:
        for i in result.active_exam_blocks:
            code_col = f"Exam Code {i:02d}"
            if code_col in column_index:
                val = _to_str(row[column_index[code_col]]) if column_index[code_col] < len(row) else ""
                if val and val != "00":
                    total_exams += 1
                    unique_codes.add(val.zfill(2))

        for i in result.active_award_blocks:
            award_col = f"Award Type {i}"
            if award_col in column_index:
                val = _to_str(row[column_index[award_col]]) if column_index[award_col] < len(row) else ""
                if val and val != "00":
                    total_awards += 1

    result.total_students = len(data_rows)
    result.total_exams = total_exams
    result.total_awards = total_awards
    result.unique_exam_codes = unique_codes

    for row in data_rows[:sample_rows]:
        if "AP Number / AP ID" not in result.student_columns:
            continue

        ap_idx = result.student_columns.get("AP Number / AP ID", 0)
        ln_idx = result.student_columns.get("Last Name", 0)
        fn_idx = result.student_columns.get("First Name", 0)
        dob_idx = result.student_columns.get("Date of Birth", 0)
        gl_idx = result.student_columns.get("Grade Level", 0)
        sch_idx = result.student_columns.get("AI Institution Name", 0)
        gen_idx = result.student_columns.get("Gender", 0)

        ap_number = _to_str(row[ap_idx]) if ap_idx < len(row) else ""
        last_name = _to_str(row[ln_idx]) if ln_idx < len(row) else ""
        first_name = _to_str(row[fn_idx]) if fn_idx < len(row) else ""
        dob = format_date_of_birth(row[dob_idx]) if dob_idx < len(row) else ""
        grade = GRADE_LEVELS.get(_to_str(row[gl_idx]), _to_str(row[gl_idx])) if gl_idx < len(row) else ""
        school = _to_str(row[sch_idx]) if sch_idx < len(row) else ""
        gender = GENDER_MAP.get(_to_str(row[gen_idx]), _to_str(row[gen_idx])) if gen_idx < len(row) else ""

        student_exam_count = 0
        exam_names = []
        for i in result.active_exam_blocks:
            code_col = f"Exam Code {i:02d}"
            if code_col in column_index and column_index[code_col] < len(row):
                val = _to_str(row[column_index[code_col]])
                if val and val != "00":
                    student_exam_count += 1
                    ename = EXAM_CODES.get(val.zfill(2), f"Unknown ({val})")
                    exam_names.append(ename)

        student_award_count = 0
        for i in result.active_award_blocks:
            award_col = f"Award Type {i}"
            if award_col in column_index and column_index[award_col] < len(row):
                val = _to_str(row[column_index[award_col]])
                if val and val != "00":
                    student_award_count += 1

        result.sample_students.append(APStudentPreview(
            ap_number=ap_number,
            last_name=last_name,
            first_name=first_name,
            dob=dob,
            grade_level=grade,
            school_name=school,
            gender=gender,
            exam_count=student_exam_count,
            award_count=student_award_count,
            exam_names=exam_names,
        ))

    return result


def parse_ap_csv_rows(file_content: str, header_row_index: int = 0):
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
