"""
AP response loader for importing AP Student Datafile CSV into the database.

Pivots wide-format AP data (one row per student, up to 30 exam blocks)
into long-format records (one row per exam result per student).

Handles deduplication against existing records and student SSID matching.
"""
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from functools import reduce
from operator import or_

from django.db import transaction, connection
from django.db.models import Q
from django.utils import timezone

from dataLoader.models import (
    APImportLog, APExamCode, APAwardType, APStudentAward, APExamResult,
    APImportStatus,
)
from dataLoader.utils.ap_column_analyzer import (
    APAnalysisResult, parse_ap_csv_rows, EXAM_CODES, AWARD_TYPES,
    GRADE_LEVELS, GENDER_MAP, format_date_of_birth, SCHOOL_MAP,
)
from dataLoader.utils.ap_student_matcher import (
    APStudentMatcher, APMatchResult, _to_str, _parse_birth_date,
)


@dataclass
class APLoaderConfig:
    batch_size: int = 500
    progress_update_interval: int = 50


class APResponseLoader:
    def __init__(
        self,
        import_log: APImportLog,
        analysis: APAnalysisResult,
        config: Optional[APLoaderConfig] = None,
    ):
        self.import_log = import_log
        self.analysis = analysis
        self.config = config or APLoaderConfig()
        self.matcher: Optional[APStudentMatcher] = None
        self._stop_flag = False

    def load_ap_data(self, file_content: str, progress_callback=None) -> APImportLog:
        self.matcher = APStudentMatcher(self.import_log.school_year)

        self.import_log.status = APImportStatus.PROCESSING
        self.import_log.save(update_fields=['status'])

        rows = list(parse_ap_csv_rows(file_content, self.analysis.header_row_index))
        total_students = len(rows)
        self.import_log.total_students = total_students
        self.import_log.save(update_fields=['total_students'])

        manual_overrides = self.import_log.manual_overrides or {}

        exam_candidates: List[dict] = []
        award_candidates: List[dict] = []
        matched_students: Set[str] = set()
        unmatched_students: Set[str] = set()
        unmatched_info: List[dict] = []
        errors: List[dict] = []

        column_index = {}
        if rows:
            first_row = rows[0]
            for key in first_row:
                column_index[key] = key

        batch_size = self.config.batch_size

        for batch_start in range(0, total_students, batch_size):
            if self._stop_flag:
                break

            batch_end = min(batch_start + batch_size, total_students)
            batch_rows = rows[batch_start:batch_end]

            for row_idx, row in enumerate(batch_rows):
                row_num = batch_start + row_idx + 1
                try:
                    ap_number = _to_str(row.get("AP Number / AP ID", ""))
                    if not ap_number:
                        continue

                    last_name = _to_str(row.get("Last Name", ""))
                    first_name = _to_str(row.get("First Name", ""))
                    dob = _parse_birth_date(row.get("Date of Birth", ""))
                    ap_school = _to_str(row.get("AI Institution Name", ""))
                    grade_level_code = _to_str(row.get("Grade Level", ""))
                    student_grade_level = GRADE_LEVELS.get(grade_level_code, grade_level_code)
                    gender = GENDER_MAP.get(_to_str(row.get("Gender", "")), _to_str(row.get("Gender", "")))
                    ai_code = _to_str(row.get("AI Code", ""))

                    match_result = self.matcher.match_student(
                        ap_number=ap_number,
                        first_name=first_name,
                        last_name=last_name,
                        dob=dob,
                        ap_school=ap_school,
                    )

                    ssid = ""
                    if ap_number in manual_overrides:
                        ssid = _to_str(manual_overrides[ap_number])
                        matched_students.add(ap_number)
                    elif match_result.matched:
                        ssid = match_result.ssid
                        matched_students.add(ap_number)
                    else:
                        unmatched_students.add(ap_number)
                        if len(unmatched_info) < 500:
                            unmatched_info.append({
                                'ap_number': ap_number,
                                'first_name': first_name,
                                'last_name': last_name,
                                'school': ap_school,
                                'reason': match_result.reason or 'No matching student found',
                            })

                    for i in self.analysis.active_exam_blocks:
                        code = _to_str(row.get(f"Exam Code {i:02d}", ""))
                        if not code or code == "00":
                            continue

                        admin_year = _to_str(row.get(f"Admin Year {i:02d}", ""))
                        exam_grade = _to_str(row.get(f"Exam Grade {i:02d}", ""))

                        school_year_str = self._admin_year_to_school_year(admin_year)

                        exam_candidates.append({
                            'ap_number': ap_number,
                            'ssid': ssid,
                            'exam_code': code.zfill(2),
                            'admin_year': school_year_str,
                            'exam_grade': exam_grade,
                            'school_code': ai_code,
                            'ai_institution_name': ap_school,
                            'student_grade_level': student_grade_level,
                        })

                    for i in self.analysis.active_award_blocks:
                        award_code = _to_str(row.get(f"Award Type {i}", ""))
                        if not award_code or award_code == "00":
                            continue

                        award_year_raw = _to_str(row.get(f"Award Year {i}", ""))
                        award_year_str = self._award_year_to_school_year(award_year_raw)

                        award_candidates.append({
                            'ap_number': ap_number,
                            'ssid': ssid,
                            'award_type_code': award_code.zfill(2),
                            'award_year': award_year_str,
                        })

                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'error': str(e),
                        'ap_number': row.get('AP Number / AP ID', 'unknown'),
                    })

            self.import_log.records_processed = batch_end
            self._update_stats(exam_candidates, award_candidates, matched_students, unmatched_students, errors)
            if progress_callback:
                progress_callback(batch_end, total_students)

        existing_exam_keys = self._get_existing_exam_keys(exam_candidates)
        existing_award_keys = self._get_existing_award_keys(award_candidates)

        new_exams = [
            r for r in exam_candidates
            if (r['ap_number'], r['exam_code'], r['admin_year']) not in existing_exam_keys
        ]
        skipped_exams = len(exam_candidates) - len(new_exams)

        new_awards = [
            a for a in award_candidates
            if (a['ap_number'], a['award_type_code'], a['award_year']) not in existing_award_keys
        ]
        skipped_awards = len(award_candidates) - len(new_awards)

        exam_objects = []
        exam_code_cache = {}
        for r in new_exams:
            if r['exam_code'] not in exam_code_cache:
                exam_code_cache[r['exam_code']] = APExamCode.objects.get(code=r['exam_code'])
            exam_objects.append(APExamResult(
                import_log=self.import_log,
                ap_number=r['ap_number'],
                ssid=r['ssid'],
                exam=exam_code_cache[r['exam_code']],
                admin_year=r['admin_year'],
                exam_grade=r['exam_grade'],
                school_code=r['school_code'],
                ai_institution_name=r['ai_institution_name'],
                student_grade_level=r['student_grade_level'],
            ))

        award_objects = []
        award_type_cache = {}
        for a in new_awards:
            if a['award_type_code'] not in award_type_cache:
                award_type_cache[a['award_type_code']] = APAwardType.objects.get(code=a['award_type_code'])
            award_objects.append(APStudentAward(
                import_log=self.import_log,
                ap_number=a['ap_number'],
                ssid=a['ssid'],
                award_type=award_type_cache[a['award_type_code']],
                award_year=a['award_year'],
            ))

        if exam_objects:
            APExamResult.objects.bulk_create(
                exam_objects,
                update_conflicts=True,
                unique_fields=['ap_number', 'exam', 'admin_year'],
                update_fields=['ssid', 'exam_grade', 'school_code', 'ai_institution_name',
                               'student_grade_level', 'import_log'],
                batch_size=1000,
            )
        if award_objects:
            APStudentAward.objects.bulk_create(
                award_objects,
                update_conflicts=True,
                unique_fields=['ap_number', 'award_type', 'award_year'],
                update_fields=['ssid', 'import_log'],
                batch_size=1000,
            )

        self.import_log.exams_detected = len(exam_candidates)
        self.import_log.exams_created = len(new_exams)
        self.import_log.exams_skipped = skipped_exams
        self.import_log.awards_detected = len(award_candidates)
        self.import_log.awards_created = len(new_awards)
        self.import_log.awards_skipped = skipped_awards
        self.import_log.students_matched = len(matched_students)
        self.import_log.students_unmatched = len(unmatched_students)
        self.import_log.error_log = errors[:100]
        self.import_log.warnings = unmatched_info[:500]
        self.import_log.status = APImportStatus.COMPLETED if not errors else APImportStatus.PARTIAL
        self.import_log.completed_at = timezone.now()
        self.import_log.save()

        return self.import_log

    def _get_existing_exam_keys(self, candidates: List[dict]) -> Set[Tuple[str, str, str]]:
        if not candidates:
            return set()

        filter_conditions = []
        for r in candidates:
            filter_conditions.append(
                Q(ap_number=r['ap_number']) & Q(exam__code=r['exam_code']) & Q(admin_year=r['admin_year'])
            )

        existing = APExamResult.objects.filter(
            reduce(or_, filter_conditions)
        ).values_list('ap_number', 'exam__code', 'admin_year')

        return {(ap, code, year) for ap, code, year in existing}

    def _get_existing_award_keys(self, candidates: List[dict]) -> Set[Tuple[str, str, str]]:
        if not candidates:
            return set()

        filter_conditions = []
        for a in candidates:
            filter_conditions.append(
                Q(ap_number=a['ap_number']) & Q(award_type__code=a['award_type_code']) & Q(award_year=a['award_year'])
            )

        existing = APStudentAward.objects.filter(
            reduce(or_, filter_conditions)
        ).values_list('ap_number', 'award_type__code', 'award_year')

        return {(ap, code, year) for ap, code, year in existing}

    def _update_stats(self, exam_candidates, award_candidates, matched_students, unmatched_students, errors):
        self.import_log.exams_detected = len(exam_candidates)
        self.import_log.awards_detected = len(award_candidates)
        self.import_log.students_matched = len(matched_students)
        self.import_log.students_unmatched = len(unmatched_students)
        self.import_log.error_count = len(errors)
        self.import_log.save(update_fields=[
            'records_processed', 'exams_detected', 'awards_detected',
            'students_matched', 'students_unmatched', 'error_count',
        ])

    @staticmethod
    def _admin_year_to_school_year(admin_year: str) -> str:
        """Convert College Board 2-digit admin year (e.g., '26') to school year (e.g., '2025-2026')."""
        if not admin_year:
            return admin_year
        try:
            val = int(admin_year)
            if val < 100:
                val = 2000 + val
            return f"{val - 1}-{val}"
        except ValueError:
            return admin_year

    @staticmethod
    def _award_year_to_school_year(award_year: str) -> str:
        """Convert College Board 2-digit award year (e.g., '26') to school year (e.g., '2025-2026')."""
        if not award_year:
            return ""
        try:
            val = int(award_year)
            if val < 100:
                val = 2000 + val
            return f"{val - 1}-{val}"
        except ValueError:
            return award_year

    def stop(self):
        self._stop_flag = True


def run_ap_import_async(import_log_id: int, file_content: str, analysis_data: dict, user_id: int):
    from django.contrib.auth import get_user_model
    from dataLoader.utils.ap_column_analyzer import APAnalysisResult

    def _run():
        connection.close()

        try:
            import_log = APImportLog.objects.get(pk=import_log_id)
            User = get_user_model()
            user = User.objects.get(pk=user_id)

            analysis = APAnalysisResult(
                student_columns=analysis_data.get('student_columns', {}),
                active_exam_blocks=analysis_data.get('active_exam_blocks', []),
                active_award_blocks=analysis_data.get('active_award_blocks', []),
                total_students=analysis_data.get('total_students', 0),
                total_exams=analysis_data.get('total_exams', 0),
                total_awards=analysis_data.get('total_awards', 0),
                header_row_index=analysis_data.get('header_row_index', 0),
                total_columns=analysis_data.get('total_columns', 0),
                warnings=analysis_data.get('warnings', []),
            )

            loader = APResponseLoader(import_log, analysis)
            loader.load_ap_data(file_content)

        except Exception as e:
            import_log = APImportLog.objects.get(pk=import_log_id)
            import_log.status = APImportStatus.FAILED
            import_log.error_log = [{'error': str(e)}]
            import_log.completed_at = timezone.now()
            import_log.save()
        finally:
            connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
