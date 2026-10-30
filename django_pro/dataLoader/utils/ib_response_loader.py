"""
Response loader for International Baccalaureate (IB) test results.

Parses IB candidate CSVs, executes student matching restricted to La Quinta High School
enrollments for the selected school year, and bulk-inserts IB exam results.
"""
from dataclasses import dataclass
import logging
import threading
from typing import Dict, List, Optional, Set, Tuple

from django.db import connection, transaction
from django.utils import timezone

from dataLoader.models import (
    IBImportLog, IBExamResult, IBImportStatus,
)
from dataLoader.utils.ib_column_analyzer import (
    IBAnalysisResult, parse_ib_csv_rows,
)
from dataLoader.utils.ib_student_matcher import (
    IBStudentMatcher, IBMatchResult,
)

logger = logging.getLogger(__name__)


@dataclass
class IBLoaderConfig:
    batch_size: int = 500
    progress_update_interval: int = 50


class IBResponseLoader:
    def __init__(
        self,
        import_log: IBImportLog,
        analysis: Optional[IBAnalysisResult] = None,
        config: Optional[IBLoaderConfig] = None,
    ):
        self.import_log = import_log
        self.analysis = analysis
        self.config = config or IBLoaderConfig()
        self.matcher: Optional[IBStudentMatcher] = None
        self._stop_flag = False

    def load_ib_data(self, file_content: str, progress_callback=None) -> IBImportLog:
        """Process IB CSV content and persist candidate results."""
        self.matcher = IBStudentMatcher(
            self.import_log.school_year,
            manual_overrides=self.import_log.manual_overrides or {},
        )

        self.import_log.status = IBImportStatus.PROCESSING
        self.import_log.save(update_fields=['status'])

        candidates, results, _ = parse_ib_csv_rows(file_content)

        total_candidates = len(candidates)
        total_results = len(results)

        self.import_log.total_candidates = total_candidates
        self.import_log.results_detected = total_results
        self.import_log.save(update_fields=['total_candidates', 'results_detected'])

        # Pre-match all candidates
        candidate_matches: Dict[str, IBMatchResult] = {}
        matched_ssids: Set[str] = set()
        unmatched_count = 0
        matched_count = 0

        for idx, cand in enumerate(candidates):
            p_code = cand['personal_code']
            c_name = cand['name']
            match_res = self.matcher.match(p_code, c_name)
            candidate_matches[p_code] = match_res

            if match_res.matched and match_res.ssid:
                matched_count += 1
                matched_ssids.add(match_res.ssid)
            else:
                unmatched_count += 1

            if (idx + 1) % self.config.progress_update_interval == 0 or (idx + 1) == total_candidates:
                self.import_log.records_processed = idx + 1
                self.import_log.students_matched = matched_count
                self.import_log.students_unmatched = unmatched_count
                self.import_log.save(update_fields=['records_processed', 'students_matched', 'students_unmatched'])
                if progress_callback:
                    progress_callback(idx + 1, total_candidates)

        # Prepare candidate student record objects (1 record per student per school year)
        results_to_create: List[IBExamResult] = []
        skipped_count = 0
        errors: List[dict] = []

        # Find all record keys to check for existing records
        record_keys = []
        for cand in candidates:
            p_code = cand.get('personal_code', '').strip()
            c_name = cand.get('name', '').strip()
            cand_id = p_code or c_name
            r_key = f"{cand_id}|{self.import_log.school_year}"
            record_keys.append(r_key)

        existing_keys: Set[str] = set()
        if record_keys:
            # Query existing in chunks of 1000
            for i in range(0, len(record_keys), 1000):
                chunk = record_keys[i:i + 1000]
                found = IBExamResult.objects.filter(record_key__in=chunk).values_list('record_key', flat=True)
                existing_keys.update(found)

        seen_batch_keys: Set[str] = set()

        for cand in candidates:
            try:
                p_code = cand.get('personal_code', '').strip()
                c_name = cand.get('name', '').strip()
                cand_id = p_code or c_name

                if not cand_id:
                    continue

                r_key = f"{cand_id}|{self.import_log.school_year}"

                if r_key in existing_keys or r_key in seen_batch_keys:
                    skipped_count += 1
                    continue

                seen_batch_keys.add(r_key)
                cand_match = candidate_matches.get(p_code)

                matched_ssid = cand_match.ssid if cand_match else ''
                match_method = cand_match.match_method if cand_match else ''
                confidence = cand_match.confidence if cand_match else 0.0

                # Build exam_results JSON array
                candidate_subjects = cand.get('subjects', [])
                exam_results = []
                for s in candidate_subjects:
                    s_name = s.get('subject', '').strip()
                    lvl = s.get('level', '').strip()
                    if not s_name:
                        continue
                    exam_results.append({
                        'subject': s_name,
                        'exam': s_name,
                        'level': lvl,
                        'grade': s.get('grade', '').strip(),
                        'predicted_grade': s.get('predicted_grade', '').strip(),
                        'language': s.get('language', '').strip(),
                        'session_year': s.get('session_year', '').strip(),
                        'session_month': s.get('session_month', '').strip(),
                    })

                first_subj = exam_results[0] if exam_results else {}
                primary_subject = first_subj.get('subject', '')
                primary_level = first_subj.get('level', '')
                primary_grade = first_subj.get('grade', '')
                primary_pred = first_subj.get('predicted_grade', '')
                primary_lang = first_subj.get('language', '')
                session_year = first_subj.get('session_year', '') or cand.get('session_year', '')
                session_month = first_subj.get('session_month', '') or 'MAY'

                result_obj = IBExamResult(
                    import_log=self.import_log,
                    record_key=r_key,
                    ssid=matched_ssid,
                    personal_code=p_code,
                    registration_number=cand.get('registration_number', ''),
                    candidate_name=c_name,
                    school_code=cand.get('school_code') or '001237',
                    school_name='La Quinta HS',
                    school_year=self.import_log.school_year,
                    session_year=session_year,
                    session_month=session_month,
                    category=cand.get('category', ''),
                    subject=primary_subject,
                    level=primary_level,
                    language=primary_lang,
                    predicted_grade=primary_pred,
                    grade=primary_grade,
                    exam_results=exam_results,
                    ee_tok_points=cand.get('ee_tok_points', ''),
                    total_points=cand.get('total_points', ''),
                    result_code=cand.get('result', ''),
                    diploma_requirements_code=cand.get('diploma_requirements_code', ''),
                    match_method=match_method,
                    match_confidence=confidence,
                )
                results_to_create.append(result_obj)

            except Exception as e:
                errors.append({
                    'personal_code': cand.get('personal_code', ''),
                    'candidate_name': cand.get('name', ''),
                    'error': str(e),
                })

        # Bulk create results in transactions
        created_count = 0
        batch_size = self.config.batch_size

        for i in range(0, len(results_to_create), batch_size):
            batch = results_to_create[i:i + batch_size]
            with transaction.atomic():
                IBExamResult.objects.bulk_create(batch, ignore_conflicts=True)
            created_count += len(batch)

        # Finalize import log
        self.import_log.results_created = created_count
        self.import_log.results_skipped = skipped_count
        self.import_log.students_matched = matched_count
        self.import_log.students_unmatched = unmatched_count
        self.import_log.records_processed = total_candidates
        self.import_log.error_count = len(errors)
        self.import_log.error_log = errors
        self.import_log.completed_at = timezone.now()

        if errors and created_count == 0:
            self.import_log.status = IBImportStatus.FAILED
        elif errors:
            self.import_log.status = IBImportStatus.PARTIAL
        else:
            self.import_log.status = IBImportStatus.COMPLETED

        self.import_log.save()
        return self.import_log

    def stop(self):
        self._stop_flag = True


def run_ib_import_async(import_log_id: int, file_content: str, analysis_data: dict, user_id: int):
    """Run IB import in a background thread."""
    def _run():
        connection.close()
        try:
            import_log = IBImportLog.objects.get(pk=import_log_id)
            loader = IBResponseLoader(import_log)
            loader.load_ib_data(file_content)
        except Exception as e:
            logger.exception("Error in async IB import %s: %s", import_log_id, e)
            try:
                import_log = IBImportLog.objects.get(pk=import_log_id)
                import_log.status = IBImportStatus.FAILED
                import_log.error_log = [{'error': str(e)}]
                import_log.completed_at = timezone.now()
                import_log.save()
            except Exception:
                pass
        finally:
            connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

