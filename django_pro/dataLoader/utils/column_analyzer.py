"""
Column analyzer for survey CSV files.

Detects question columns (VALUES/TEXT pairs), demographic columns,
and metadata from Panorama-style CSV exports.
"""
import csv
import re
import io
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional


@dataclass
class QuestionColumn:
    """Represents a detected question from CSV headers."""
    question_text: str
    value_column: str
    text_column: str
    value_column_index: int
    text_column_index: int
    domain: str = ''
    response_scale_max: int = 7  # Detected from data; min is always 1
    scale_labels: Dict[int, str] = field(default_factory=dict)  # value -> text label mapping


@dataclass
class AnalysisResult:
    """Result of analyzing CSV columns."""
    questions: List[QuestionColumn] = field(default_factory=list)
    demographic_columns: Dict[str, int] = field(default_factory=dict)
    metadata_columns: Dict[str, int] = field(default_factory=dict)
    header_row_index: int = 0
    total_columns: int = 0
    detected_survey_type: str = ''
    detected_grade_range: str = ''
    warnings: List[str] = field(default_factory=list)
    sample_data: List[Dict] = field(default_factory=list)


# Known demographic columns in Panorama exports
DEMOGRAPHIC_COLUMNS = {
    'Client Name': 'client_name',
    'District Name': 'district_name',
    'School Name': 'school_name',
    'School ID': 'school_id',
    'Student First Name': 'first_name',
    'Student Last Name': 'last_name',
    'Student Email': 'email',
    'Student ID': 'student_id',
    'Student Grade Level': 'grade_level',
    'Student Gender': 'gender',
    'Student Race': 'race',
    'Student ELL Status': 'ell_status',
    'Student Foster Status': 'foster_status',
    'Student Gifted Status': 'gifted_status',
    'Student Socioeconomically Disadvantaged Status': 'sed_status',
    'Student Special Education Status': 'sped_status',
    'Student Sped Cohort': 'sped_cohort',
    'Student Sped Cohort Y/N': 'sped_cohort_yn',
    'Student VAPA': 'vapa',
}

# Metadata columns
METADATA_COLUMNS = {
    'Unique Response ID': 'unique_response_id',
    'Survey Type': 'survey_type',
    'Response Language': 'response_language',
    'Response Start Time': 'response_start_time',
    'Response Duration (minutes)': 'response_duration',
}

# Survey type detection patterns
SURVEY_TYPE_PATTERNS = {
    r'skills?\s*(and|&)?\s*competenc': 'Skills and Competencies',
    r'support\s*(and|&)?\s*environment': 'Support and Environment',
    r'social.?emotional': 'Social-Emotional Learning',
    r'climate': 'School Climate',
}

# Grade range detection patterns
GRADE_RANGE_PATTERNS = {
    r'grades?\s*3[-_]5': 'Grades 3-5',
    r'grades?\s*6[-_]8': 'Grades 6-8',
    r'grades?\s*9[-_]12': 'Grades 9-12',
    r'grades?\s*k[-_]2': 'Grades K-2',
    r'elementary': 'Elementary',
    r'middle': 'Middle School',
    r'high\s*school': 'High School',
}


def detect_header_row(file_content: str, max_rows: int = 5) -> int:
    """
    Detect which row contains the actual headers.
    Panorama CSVs often have a description row first.
    """
    reader = csv.reader(io.StringIO(file_content))
    
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        # Look for characteristic column names
        row_text = ','.join(row).lower()
        if 'student id' in row_text and ('values' in row_text or 'text' in row_text):
            return i
    
    return 0  # Default to first row


def analyze_csv(file_content: str, filename: str = '', sample_rows: int = 5) -> AnalysisResult:
    """
    Analyze CSV content to detect questions, demographics, and structure.
    
    Args:
        file_content: Raw CSV content as string
        filename: Original filename (used for survey type detection)
        sample_rows: Number of data rows to include in sample
    
    Returns:
        AnalysisResult with detected structure
    """
    result = AnalysisResult()
    
    # Detect header row
    result.header_row_index = detect_header_row(file_content)
    
    # Parse CSV
    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)
    
    if len(rows) <= result.header_row_index:
        result.warnings.append("CSV file appears to be empty or too short")
        return result
    
    headers = rows[result.header_row_index]
    result.total_columns = len(headers)
    
    # Detect survey type and grade range from filename
    filename_lower = filename.lower()
    for pattern, survey_type in SURVEY_TYPE_PATTERNS.items():
        if re.search(pattern, filename_lower, re.IGNORECASE):
            result.detected_survey_type = survey_type
            break
    
    for pattern, grade_range in GRADE_RANGE_PATTERNS.items():
        if re.search(pattern, filename_lower, re.IGNORECASE):
            result.detected_grade_range = grade_range
            break
    
    # Build column index
    column_index = {header: i for i, header in enumerate(headers)}
    
    # Find demographic columns
    for col_name, field_name in DEMOGRAPHIC_COLUMNS.items():
        if col_name in column_index:
            result.demographic_columns[field_name] = column_index[col_name]
    
    # Find metadata columns
    for col_name, field_name in METADATA_COLUMNS.items():
        if col_name in column_index:
            result.metadata_columns[field_name] = column_index[col_name]
    
    # Find question columns (VALUES/TEXT pairs)
    result.questions = _find_question_columns(headers)
    
    # If no questions found, warn
    if not result.questions:
        result.warnings.append("No question columns (VALUES/TEXT pairs) detected")
    
    # Get sample data
    data_start = result.header_row_index + 1
    for row in rows[data_start:data_start + sample_rows]:
        if len(row) >= len(headers):
            sample = {}
            for field_name, col_idx in result.demographic_columns.items():
                if col_idx < len(row):
                    sample[field_name] = row[col_idx]
            for field_name, col_idx in result.metadata_columns.items():
                if col_idx < len(row):
                    sample[field_name] = row[col_idx]
            result.sample_data.append(sample)
    
    # Detect response scale from sample data
    _detect_response_scales(result, rows[data_start:data_start + 100])
    
    return result


def _find_question_columns(headers: List[str]) -> List[QuestionColumn]:
    """
    Find question columns by looking for matching VALUES/TEXT pairs.
    """
    questions = []
    values_pattern = re.compile(r'^(.+?)\s*VALUES$', re.IGNORECASE)
    text_pattern = re.compile(r'^(.+?)\s*TEXT$', re.IGNORECASE)
    
    # Build a map of question text -> column info
    values_cols = {}
    text_cols = {}
    
    for i, header in enumerate(headers):
        header = header.strip()
        
        values_match = values_pattern.match(header)
        if values_match:
            question_text = values_match.group(1).strip()
            values_cols[question_text.lower()] = (header, i, question_text)
            continue
        
        text_match = text_pattern.match(header)
        if text_match:
            question_text = text_match.group(1).strip()
            text_cols[question_text.lower()] = (header, i, question_text)
    
    # Match pairs
    for q_key, (value_header, value_idx, question_text) in values_cols.items():
        if q_key in text_cols:
            text_header, text_idx, _ = text_cols[q_key]
            questions.append(QuestionColumn(
                question_text=question_text,
                value_column=value_header,
                text_column=text_header,
                value_column_index=value_idx,
                text_column_index=text_idx,
            ))
    
    # Sort by column index
    questions.sort(key=lambda q: q.value_column_index)
    
    return questions


def _detect_response_scales(result: AnalysisResult, data_rows: List[List[str]]):
    """
    Detect response scale max and capture value->text label mappings for each question.
    Assumes all scales start at 1.
    """
    for question in result.questions:
        values = []
        labels = {}  # value -> text label
        
        for row in data_rows:
            if question.value_column_index < len(row) and question.text_column_index < len(row):
                val_str = row[question.value_column_index].strip()
                text_str = row[question.text_column_index].strip()
                
                if val_str and val_str.isdigit():
                    val = int(val_str)
                    values.append(val)
                    
                    # Capture the text label for this value
                    if val not in labels and text_str:
                        labels[val] = text_str
        
        if values:
            question.response_scale_max = max(values)
            question.scale_labels = labels


def parse_csv_rows(file_content: str, header_row_index: int = 0):
    """
    Generator that yields data rows as dictionaries.
    
    Args:
        file_content: Raw CSV content
        header_row_index: Which row contains headers (0-indexed)
    
    Yields:
        dict mapping column name -> value
    """
    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)
    
    if len(rows) <= header_row_index:
        return
    
    headers = rows[header_row_index]
    
    for row in rows[header_row_index + 1:]:
        if len(row) == 0 or all(cell.strip() == '' for cell in row):
            continue  # Skip empty rows
        
        row_dict = {}
        for i, header in enumerate(headers):
            if i < len(row):
                row_dict[header] = row[i].strip()
            else:
                row_dict[header] = ''
        
        yield row_dict
