"""
Column analyzer for International Baccalaureate (IB) Candidate Report CSVs.

Analyzes candidate data, subject results, and diploma awards from IB CSV files.
Supports files from IB candidate reporting (e.g., CandidateReport2024.csv,
IBCandidateReport2025.csv, IBCandidateResults2026.csv).
"""
import csv
import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class IBSampleCandidate:
    personal_code: str
    registration_number: str
    name: str
    category: str
    school_code: str
    subject_count: int
    subjects_summary: str
    total_points: str = ''
    result: str = ''


@dataclass
class IBAnalysisResult:
    total_candidates: int = 0
    total_results: int = 0
    school_code: str = ''
    unique_subjects: List[str] = field(default_factory=list)
    unique_levels: List[str] = field(default_factory=list)
    unique_categories: List[str] = field(default_factory=list)
    sample_candidates: List[IBSampleCandidate] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    header_rows_count: int = 2


def _clean_str(val) -> str:
    if val is None:
        return ''
    return str(val).strip()


def normalize_ib_csv_content(file_content: str | bytes) -> str:
    """
    Normalizes IB CSV files:
    1. Merges split two-row headers into a single canonical 16-column header row:
       Row 0 contains summary headers (EE/TOK points:, Total points:, Result:, etc.)
       Row 1 contains subject headers (Subject, Level, Language, Predicted grade, Grade).
       These are merged into one comprehensive header line before analyzing and importing.
    2. Merges split candidate data rows:
       When a candidate's first line contains summary fields (Total points, Result, etc.)
       with empty subject columns, and the second line contains subject details with empty
       summary columns, they are merged into a single complete record.
    3. Propagates candidate summary fields to all subject rows for that candidate.
    """
    if isinstance(file_content, bytes):
        try:
            file_content = file_content.decode('utf-8-sig')
        except UnicodeDecodeError:
            file_content = file_content.decode('latin-1')

    reader = list(csv.reader(io.StringIO(file_content)))
    if not reader:
        return file_content if isinstance(file_content, str) else ''

    merged_rows = []
    data_start = 0

    # Header check: merge if first two rows form the split IB header
    if len(reader) >= 2:
        r0 = [_clean_str(c).lower() for c in reader[0]]
        r1 = [_clean_str(c).lower() for c in reader[1]]
        is_r0_header = any('year' in c or 'personal code' in c or 'ee/tok' in c for c in r0)
        is_r1_header = any('subject' in c or 'level' in c or 'grade' in c for c in r1)
        if is_r0_header and is_r1_header:
            max_cols = max(len(reader[0]), len(reader[1]), 16)
            merged_hdr = []
            for col_idx in range(max_cols):
                v1 = _clean_str(reader[1][col_idx]) if col_idx < len(reader[1]) else ''
                v0 = _clean_str(reader[0][col_idx]) if col_idx < len(reader[0]) else ''
                merged_hdr.append(v1 or v0)
            merged_rows.append(merged_hdr)
            data_start = 2
        elif is_r0_header:
            merged_rows.append(reader[0])
            data_start = 1
    elif len(reader) == 1:
        merged_rows.append(reader[0])
        data_start = 1

    # Data rows: merge split candidate lines (summary row + first subject row)
    i = data_start
    cand_summaries: Dict[str, dict] = {}

    while i < len(reader):
        row = reader[i]
        if not row or not any(_clean_str(c) for c in row):
            i += 1
            continue

        p_code = _clean_str(row[4]) if len(row) > 4 else ''
        name = _clean_str(row[5]) if len(row) > 5 else ''
        cand_key = p_code or name

        subj = _clean_str(row[7]) if len(row) > 7 else ''
        ee_tok = _clean_str(row[12]) if len(row) > 12 else ''
        total_pts = _clean_str(row[13]) if len(row) > 13 else ''
        res_code = _clean_str(row[14]) if len(row) > 14 else ''
        dip_req = _clean_str(row[15]) if len(row) > 15 else ''

        # Summary line with empty subject
        if not subj and (ee_tok or total_pts or res_code or dip_req):
            cand_summaries[cand_key] = {
                'ee_tok': ee_tok,
                'total_pts': total_pts,
                'res_code': res_code,
                'dip_req': dip_req,
            }
            # Check if next line is the same candidate's first subject line
            if i + 1 < len(reader):
                next_row = reader[i + 1]
                next_code = _clean_str(next_row[4]) if len(next_row) > 4 else ''
                next_name = _clean_str(next_row[5]) if len(next_row) > 5 else ''
                next_subj = _clean_str(next_row[7]) if len(next_row) > 7 else ''
                if (next_code == p_code or next_name == name) and next_subj:
                    # Merge row and next_row into single unified line
                    merged_line = list(next_row)
                    while len(merged_line) < 16:
                        merged_line.append('')
                    if not merged_line[12]:
                        merged_line[12] = ee_tok
                    if not merged_line[13]:
                        merged_line[13] = total_pts
                    if not merged_line[14]:
                        merged_line[14] = res_code
                    if not merged_line[15]:
                        merged_line[15] = dip_req
                    merged_rows.append(merged_line)
                    i += 2
                    continue
            # If not followed by a subject row, keep summary row
            merged_rows.append(row)
            i += 1
            continue

        # Subject line: ensure summary columns are populated from candidate summary
        if subj and cand_key in cand_summaries:
            row_copy = list(row)
            while len(row_copy) < 16:
                row_copy.append('')
            if not row_copy[12]:
                row_copy[12] = cand_summaries[cand_key]['ee_tok']
            if not row_copy[13]:
                row_copy[13] = cand_summaries[cand_key]['total_pts']
            if not row_copy[14]:
                row_copy[14] = cand_summaries[cand_key]['res_code']
            if not row_copy[15]:
                row_copy[15] = cand_summaries[cand_key]['dip_req']
            merged_rows.append(row_copy)
            i += 1
            continue

        merged_rows.append(row)
        i += 1

    out = io.StringIO()
    writer = csv.writer(out)
    for r in merged_rows:
        writer.writerow(r)
    return out.getvalue()


def parse_ib_csv_rows(file_content: str) -> Tuple[List[dict], List[dict], int]:
    """
    Parse IB CSV content into (candidates_list, results_list, header_rows_count).

    Candidates dict has:
      personal_code, registration_number, name, category, school_code,
      ee_tok_points, total_points, result, diploma_requirements_code

    Results dict has:
      personal_code, registration_number, candidate_name, category, school_code,
      session_year, session_month, subject, level, language, predicted_grade, grade,
      ee_tok_points, total_points, result_code, diploma_requirements_code
    """
    normalized_content = normalize_ib_csv_content(file_content)

    reader = list(csv.reader(io.StringIO(normalized_content)))
    if not reader:
        return [], [], 0

    header_rows_count = 1
    data_rows = reader[header_rows_count:]

    candidates_map: Dict[str, dict] = {}
    results_list: List[dict] = []

    for row in data_rows:
        if not row or not any(_clean_str(c) for c in row):
            continue

        year = _clean_str(row[0]) if len(row) > 0 else ''
        month = _clean_str(row[1]) if len(row) > 1 else ''
        school = _clean_str(row[2]) if len(row) > 2 else ''
        reg_num = _clean_str(row[3]) if len(row) > 3 else ''
        personal_code = _clean_str(row[4]) if len(row) > 4 else ''
        name = _clean_str(row[5]) if len(row) > 5 else ''
        category = _clean_str(row[6]) if len(row) > 6 else ''
        subject = _clean_str(row[7]) if len(row) > 7 else ''
        level = _clean_str(row[8]) if len(row) > 8 else ''
        language = _clean_str(row[9]) if len(row) > 9 else ''
        pred_grade = _clean_str(row[10]) if len(row) > 10 else ''
        grade = _clean_str(row[11]) if len(row) > 11 else ''

        # Summary columns (in cols 12-15)
        ee_tok = _clean_str(row[12]) if len(row) > 12 else ''
        total_pts = _clean_str(row[13]) if len(row) > 13 else ''
        result = _clean_str(row[14]) if len(row) > 14 else ''
        diploma_req = _clean_str(row[15]) if len(row) > 15 else ''

        if not personal_code and not name:
            continue

        cand_key = personal_code or name
        if cand_key not in candidates_map:
            candidates_map[cand_key] = {
                'personal_code': personal_code,
                'registration_number': reg_num,
                'name': name,
                'category': category,
                'school_code': school,
                'ee_tok_points': ee_tok,
                'total_points': total_pts,
                'result': result,
                'diploma_requirements_code': diploma_req,
                'subjects': [],
            }
        else:
            cand = candidates_map[cand_key]
            if ee_tok and not cand['ee_tok_points']:
                cand['ee_tok_points'] = ee_tok
            if total_pts and not cand['total_points']:
                cand['total_points'] = total_pts
            if result and not cand['result']:
                cand['result'] = result
            if diploma_req and not cand['diploma_requirements_code']:
                cand['diploma_requirements_code'] = diploma_req
            if category and not cand['category']:
                cand['category'] = category

        if subject:
            cand = candidates_map[cand_key]
            res_item = {
                'personal_code': personal_code,
                'registration_number': reg_num,
                'candidate_name': name,
                'category': category or cand.get('category', ''),
                'school_code': school,
                'session_year': year,
                'session_month': month,
                'subject': subject,
                'level': level,
                'language': language,
                'predicted_grade': pred_grade,
                'grade': grade,
                'ee_tok_points': ee_tok or cand.get('ee_tok_points', ''),
                'total_points': total_pts or cand.get('total_points', ''),
                'result_code': result or cand.get('result', ''),
                'diploma_requirements_code': diploma_req or cand.get('diploma_requirements_code', ''),
            }
            results_list.append(res_item)
            candidates_map[cand_key]['subjects'].append(res_item)

    return list(candidates_map.values()), results_list, header_rows_count


def analyze_ib_csv(file_content: str, filename: str = '') -> IBAnalysisResult:
    """Analyze IB CSV content and extract candidate counts, subjects, and sample records."""
    candidates, results, header_rows_count = parse_ib_csv_rows(file_content)

    unique_subjects: Set[str] = set()
    unique_levels: Set[str] = set()
    unique_categories: Set[str] = set()
    schools: Set[str] = set()
    warnings: List[str] = []

    for c in candidates:
        if c.get('school_code'):
            schools.add(c['school_code'])
        if c.get('category'):
            unique_categories.add(c['category'])

    for r in results:
        if r.get('subject'):
            unique_subjects.add(r['subject'])
        if r.get('level'):
            unique_levels.add(r['level'])
        if r.get('category'):
            unique_categories.add(r['category'])
        if r.get('school_code'):
            schools.add(r['school_code'])

    # Sample candidates for preview
    sample_candidates = []
    for c in candidates[:10]:
        sub_list = [f"{s['subject']} ({s['level']})" for s in c.get('subjects', [])[:4]]
        sub_str = ', '.join(sub_list)
        if len(c.get('subjects', [])) > 4:
            sub_str += f", +{len(c['subjects']) - 4} more"
        sample_candidates.append(
            IBSampleCandidate(
                personal_code=c['personal_code'],
                registration_number=c['registration_number'],
                name=c['name'],
                category=c['category'],
                school_code=c['school_code'],
                subject_count=len(c.get('subjects', [])),
                subjects_summary=sub_str,
                total_points=c.get('total_points', ''),
                result=c.get('result', ''),
            )
        )

    school_code = next(iter(schools)) if schools else '001237'
    if not schools:
        warnings.append("No school code detected; defaulted to 001237 (La Quinta High School).")

    return IBAnalysisResult(
        total_candidates=len(candidates),
        total_results=len(results),
        school_code=school_code,
        unique_subjects=sorted(list(unique_subjects)),
        unique_levels=sorted(list(unique_levels)),
        unique_categories=sorted(list(unique_categories)),
        sample_candidates=sample_candidates,
        warnings=warnings,
        header_rows_count=header_rows_count,
    )

