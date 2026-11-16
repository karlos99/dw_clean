"""
PSAT response loader: import College Board PSAT 8/9 and PSAT/NMSQT CSV exports.
"""
import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from django.db import connection
from django.utils import timezone

from dataLoader.models import PSATImportLog, PSATResult, PSATImportStatus
from dataLoader.utils.psat_column_analyzer import (
    PSATAnalysisResult,
    parse_psat_csv_rows,
    build_response_data,
    SCORE_COLUMNS,
)
from dataLoader.utils.sat_column_analyzer import parse_int, parse_date, _to_str
from dataLoader.utils.psat_student_matcher import (
    PSATStudentMatcher,
    _normalize_ssid,
    _normalize_student_id,
)
from dataLoader.utils.sat_school_resolver import resolve_school_short_name


logger = logging.getLogger(__name__)

_psat_sync_lock = threading.Lock()
_psat_sync_requested = threading.Event()


def run_psat_clickhouse_sync_async():
    """Rebuild the analytics_psat_results ClickHouse table in a background thread.

    Keeps Data Exploration in sync with published PSAT imports without requiring
    a manual ``manage.py sync_to_clickhouse``. Concurrent requests are coalesced:
    if a sync is already running, the request is folded into a follow-up pass so
    the latest published state is always picked up. Best-effort: failures are
    logged and never surface to the user.
    """
    from django.core.management import call_command

    _psat_sync_requested.set()
    if not _psat_sync_lock.acquire(blocking=False):
        # A worker is already running and will pick up this request.
        return None

    def _run():
        try:
            while _psat_sync_requested.is_set():
                _psat_sync_requested.clear()
                connection.close()
                try:
                    call_command(
                        'sync_to_clickhouse',
                        tables='analytics_psat_results',
                        full=True,
                    )
                    logger.info("PSAT ClickHouse sync completed.")
                except Exception:
                    logger.exception("PSAT ClickHouse sync failed.")
                finally:
                    connection.close()
        finally:
            _psat_sync_lock.release()
            # A request may have arrived between the loop check and the release.
            if _psat_sync_requested.is_set():
                run_psat_clickhouse_sync_async()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


@dataclass
class PSATLoaderConfig:
    batch_size: int = 500
    progress_update_interval: int = 50


def build_record_key(
    psat_type: str,
    cb_id,
    state_student_id,
    district_student_id,
    last_name,
    first_name,
    dob,
    test_date,
) -> str:
    identity = (
        _to_str(state_student_id)
        or _to_str(district_student_id)
        or _to_str(cb_id)
    )
    if not identity:
        identity = "|".join([
            _to_str(last_name).upper(),
            _to_str(first_name).upper(),
            _to_str(dob),
        ])
    date_part = _to_str(test_date) or 'nodate'
    type_part = _to_str(psat_type) or 'PSAT'
    key = f"{type_part}|{identity}|{date_part}"
    return key[:120]


class PSATResponseLoader:
    def __init__(
        self,
        import_log: PSATImportLog,
        analysis: PSATAnalysisResult,
        config: Optional[PSATLoaderConfig] = None,
    ):
        self.import_log = import_log
        self.analysis = analysis
        self.config = config or PSATLoaderConfig()
        self.matcher: Optional[PSATStudentMatcher] = None
        self._stop_flag = False

    def load_psat_data(self, file_content: str, progress_callback=None) -> PSATImportLog:
        self.matcher = PSATStudentMatcher(self.import_log.school_year)

        self.import_log.status = PSATImportStatus.PROCESSING
        self.import_log.save(update_fields=['status'])

        rows = list(parse_psat_csv_rows(file_content, self.analysis.header_row_index))
        total_students = len(rows)
        self.import_log.total_students = total_students
        self.import_log.save(update_fields=['total_students'])

        manual_overrides = self.import_log.manual_overrides or {}
        psat_type = self.import_log.psat_type or self.analysis.psat_type

        candidates: List[dict] = []
        matched_students: Set[str] = set()
        unmatched_students: Set[str] = set()
        unmatched_info: List[dict] = []
        errors: List[dict] = []

        batch_size = self.config.batch_size
        school_cache: Dict[Tuple[str, str], str] = {}

        for batch_start in range(0, total_students, batch_size):
            if self._stop_flag:
                break

            batch_end = min(batch_start + batch_size, total_students)
            batch_rows = rows[batch_start:batch_end]

            for row_idx, row in enumerate(batch_rows):
                row_num = batch_start + row_idx + 1
                try:
                    state_student_id = _normalize_ssid(row.get('STATE_STUDENT_ID', ''))
                    district_student_id = _normalize_student_id(row.get('DISTRICT_STUDENT_ID', ''))
                    cb_id = _to_str(row.get('CB_ID', ''))
                    last_name = _to_str(row.get('NAME_LAST', ''))
                    first_name = _to_str(row.get('NAME_FIRST', ''))
                    middle_initial = _to_str(row.get('NAME_MI', ''))
                    gender = _to_str(row.get('GENDER', ''))
                    school_code = _to_str(row.get('AI_CODE', ''))
                    school_name_raw = _to_str(row.get('AI_NAME', ''))
                    school_key = (school_name_raw, school_code)
                    school_name = school_cache.get(school_key)
                    if school_name is None:
                        school_name = resolve_school_short_name(school_name_raw, school_code)
                        school_cache[school_key] = school_name

                    dob_date = parse_date(row.get('BIRTH_DATE', ''))
                    test_date = parse_date(row.get(SCORE_COLUMNS['test_date'], ''))
                    test_grade = parse_int(row.get(SCORE_COLUMNS['test_grade'], ''))

                    record_key = build_record_key(
                        psat_type,
                        cb_id,
                        state_student_id,
                        district_student_id,
                        last_name,
                        first_name,
                        dob_date.isoformat() if dob_date else '',
                        test_date.isoformat() if test_date else '',
                    )

                    total_score = parse_int(row.get(SCORE_COLUMNS['total_score'], ''))
                    ebrw_score = parse_int(row.get(SCORE_COLUMNS['ebrw_score'], ''))
                    math_score = parse_int(row.get(SCORE_COLUMNS['math_score'], ''))

                    if (
                        not state_student_id
                        and not district_student_id
                        and not cb_id
                        and not last_name
                        and not first_name
                    ):
                        continue

                    match_result = self.matcher.match_student(
                        record_key=record_key,
                        state_student_id=state_student_id,
                        district_student_id=district_student_id,
                        first_name=first_name,
                        last_name=last_name,
                        dob=dob_date.isoformat() if dob_date else '',
                        school=school_name,
                        cb_id=cb_id,
                    )

                    ssid = ''
                    match_method = ''
                    match_confidence = 0.0
                    if record_key in manual_overrides:
                        ssid = _to_str(manual_overrides[record_key])
                        match_method = 'manual_override'
                        match_confidence = 1.0
                        matched_students.add(record_key)
                    elif match_result.matched:
                        ssid = match_result.ssid
                        match_method = match_result.match_method
                        match_confidence = match_result.confidence
                        matched_students.add(record_key)
                    else:
                        unmatched_students.add(record_key)
                        if len(unmatched_info) < 500:
                            unmatched_info.append({
                                'record_key': record_key,
                                'state_student_id': state_student_id,
                                'district_student_id': district_student_id,
                                'cb_id': cb_id,
                                'first_name': first_name,
                                'last_name': last_name,
                                'dob': dob_date.isoformat() if dob_date else '',
                                'school': school_name,
                                'reason': match_result.reason or 'No matching student found',
                            })

                    candidates.append({
                        'record_key': record_key,
                        'ssid': ssid,
                        'state_student_id': state_student_id,
                        'district_student_id': district_student_id,
                        'cb_id': cb_id,
                        'last_name': last_name,
                        'first_name': first_name,
                        'middle_initial': middle_initial,
                        'birth_date': dob_date,
                        'gender': gender,
                        'school_code': school_code,
                        'school_name': school_name,
                        'test_date': test_date,
                        'test_grade': test_grade,
                        'total_score': total_score,
                        'ebrw_score': ebrw_score,
                        'math_score': math_score,
                        'response_data': build_response_data(row),
                        'match_method': match_method,
                        'match_confidence': match_confidence,
                        'psat_type': psat_type,
                    })

                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'error': str(e),
                        'record_key': (
                            row.get('CB_ID', '')
                            or row.get('STATE_STUDENT_ID', '')
                            or row.get('DISTRICT_STUDENT_ID', 'unknown')
                        ),
                    })

            self.import_log.records_processed = batch_end
            self._update_stats(candidates, matched_students, unmatched_students, errors)
            if progress_callback:
                progress_callback(batch_end, total_students)

        existing_keys = self._get_existing_keys(candidates)
        new_candidates = [c for c in candidates if c['record_key'] not in existing_keys]
        skipped = len(candidates) - len(new_candidates)

        objects = []
        for c in new_candidates:
            objects.append(PSATResult(
                import_log=self.import_log,
                record_key=c['record_key'],
                psat_type=c['psat_type'],
                ssid=c['ssid'],
                state_student_id=c['state_student_id'],
                district_student_id=c['district_student_id'],
                cb_id=c['cb_id'],
                last_name=c['last_name'],
                first_name=c['first_name'],
                middle_initial=c['middle_initial'],
                birth_date=c['birth_date'],
                gender=c['gender'],
                school_code=c['school_code'],
                school_name=c['school_name'],
                test_date=c['test_date'],
                test_grade=c['test_grade'],
                school_year=self.import_log.school_year,
                total_score=c['total_score'],
                ebrw_score=c['ebrw_score'],
                math_score=c['math_score'],
                response_data=c['response_data'],
                match_method=c['match_method'],
                match_confidence=c['match_confidence'],
            ))

        if objects:
            PSATResult.objects.bulk_create(
                objects,
                update_conflicts=True,
                unique_fields=['record_key'],
                update_fields=[
                    'import_log', 'psat_type', 'ssid', 'state_student_id',
                    'district_student_id', 'cb_id', 'last_name', 'first_name',
                    'middle_initial', 'birth_date', 'gender', 'school_code',
                    'school_name', 'test_date', 'test_grade', 'school_year',
                    'total_score', 'ebrw_score', 'math_score', 'response_data',
                    'match_method', 'match_confidence',
                ],
                batch_size=1000,
            )

        self.import_log.records_detected = len(candidates)
        self.import_log.records_created = len(new_candidates)
        self.import_log.records_skipped = skipped
        self.import_log.students_matched = len(matched_students)
        self.import_log.students_unmatched = len(unmatched_students)
        self.import_log.error_log = errors[:100]
        self.import_log.warnings = unmatched_info[:500]
        self.import_log.status = (
            PSATImportStatus.COMPLETED if not errors else PSATImportStatus.PARTIAL
        )
        self.import_log.completed_at = timezone.now()
        self.import_log.save()

        return self.import_log

    def _get_existing_keys(self, candidates: List[dict]) -> Set[str]:
        keys = {c['record_key'] for c in candidates}
        if not keys:
            return set()
        existing: Set[str] = set()
        key_list = list(keys)
        chunk = 1000
        for i in range(0, len(key_list), chunk):
            existing.update(
                PSATResult.objects.filter(record_key__in=key_list[i:i + chunk])
                .values_list('record_key', flat=True)
            )
        return existing

    def _update_stats(self, candidates, matched_students, unmatched_students, errors):
        self.import_log.records_detected = len(candidates)
        self.import_log.students_matched = len(matched_students)
        self.import_log.students_unmatched = len(unmatched_students)
        self.import_log.error_count = len(errors)
        self.import_log.save(update_fields=[
            'records_processed', 'records_detected',
            'students_matched', 'students_unmatched', 'error_count',
        ])

    def stop(self):
        self._stop_flag = True


def run_psat_import_async(import_log_id: int, file_content: str, analysis_data: dict, user_id: int):
    from django.contrib.auth import get_user_model

    def _run():
        connection.close()
        try:
            import_log = PSATImportLog.objects.get(pk=import_log_id)
            User = get_user_model()
            User.objects.get(pk=user_id)

            analysis = PSATAnalysisResult(
                psat_type=analysis_data.get('psat_type', 'PSAT89'),
                psat_type_label=analysis_data.get('psat_type_label', 'PSAT 8/9'),
                student_columns=analysis_data.get('student_columns', {}),
                score_columns=analysis_data.get('score_columns', {}),
                total_students=analysis_data.get('total_students', 0),
                total_with_scores=analysis_data.get('total_with_scores', 0),
                total_missing_state_id=analysis_data.get('total_missing_state_id', 0),
                header_row_index=analysis_data.get('header_row_index', 0),
                total_columns=analysis_data.get('total_columns', 0),
                inferred_school_year=analysis_data.get('inferred_school_year', ''),
                warnings=analysis_data.get('warnings', []),
            )

            loader = PSATResponseLoader(import_log, analysis)
            loader.load_psat_data(file_content)

            # Keep Data Exploration current without a manual sync. Only rows
            # from imports flagged is_published are published, so this is a
            # no-op until the import is published (and runs again on publish).
            try:
                from dataLoader.views import _invalidate_psat_caches
                _invalidate_psat_caches()
            except Exception:
                pass
            run_psat_clickhouse_sync_async()
        except Exception as e:
            import_log = PSATImportLog.objects.get(pk=import_log_id)
            import_log.status = PSATImportStatus.FAILED
            import_log.error_log = [{'error': str(e)}]
            import_log.completed_at = timezone.now()
            import_log.save()
        finally:
            connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
