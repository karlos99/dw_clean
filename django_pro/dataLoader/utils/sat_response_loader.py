"""
SAT response loader for importing College Board SAT results CSV into the database.

Reads the LATEST_SAT_* score block plus the four percentile regions, matches
students (STATE_STUDENT_ID first, then fuzzy name + DOB), applies manual SSID
overrides, and dedupes on a stable record key (CB_ID/STATE_STUDENT_ID + test date).
"""
import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from django.db import connection
from django.utils import timezone

from dataLoader.models import (
    SATImportLog, SATResult, SATImportStatus,
)
from dataLoader.utils.sat_column_analyzer import (
    SATAnalysisResult, parse_sat_csv_rows, PERCENTILE_REGIONS,
    parse_int, parse_date, _to_str,
)
from dataLoader.utils.sat_student_matcher import SATStudentMatcher
from dataLoader.utils.sat_school_resolver import resolve_school_short_name


logger = logging.getLogger(__name__)

_sat_sync_lock = threading.Lock()
_sat_sync_requested = threading.Event()


def run_sat_clickhouse_sync_async():
    """Rebuild the analytics_sat_results ClickHouse table in a background thread.

    Keeps Data Exploration in sync with published SAT imports without requiring
    a manual `manage.py sync_to_clickhouse`. Concurrent requests are coalesced:
    if a sync is already running, the request is folded into a follow-up pass so
    the latest published state is always picked up. Best-effort: failures are
    logged and never surface to the user.
    """
    from django.core.management import call_command

    _sat_sync_requested.set()
    if not _sat_sync_lock.acquire(blocking=False):
        # A worker is already running and will pick up this request.
        return None

    def _run():
        try:
            while _sat_sync_requested.is_set():
                _sat_sync_requested.clear()
                connection.close()
                try:
                    call_command('sync_to_clickhouse', tables='analytics_sat_results', full=True)
                    logger.info("SAT ClickHouse sync completed.")
                except Exception:
                    logger.exception("SAT ClickHouse sync failed.")
                finally:
                    connection.close()
        finally:
            _sat_sync_lock.release()
            # A request may have arrived between the loop check and the release.
            if _sat_sync_requested.is_set():
                run_sat_clickhouse_sync_async()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread



@dataclass
class SATLoaderConfig:
    batch_size: int = 500
    progress_update_interval: int = 50


def build_record_key(cb_id, state_student_id, last_name, first_name, dob, test_date) -> str:
    """Stable key used to dedupe a student's SAT administration across imports."""
    identity = _to_str(cb_id) or _to_str(state_student_id)
    if not identity:
        identity = "|".join([
            _to_str(last_name).upper(),
            _to_str(first_name).upper(),
            _to_str(dob),
        ])
    date_part = _to_str(test_date) or "nodate"
    key = f"{identity}|{date_part}"
    return key[:120]


class SATResponseLoader:
    def __init__(
        self,
        import_log: SATImportLog,
        analysis: SATAnalysisResult,
        config: Optional[SATLoaderConfig] = None,
    ):
        self.import_log = import_log
        self.analysis = analysis
        self.config = config or SATLoaderConfig()
        self.matcher: Optional[SATStudentMatcher] = None
        self._stop_flag = False

    def load_sat_data(self, file_content: str, progress_callback=None) -> SATImportLog:
        self.matcher = SATStudentMatcher(self.import_log.school_year)

        self.import_log.status = SATImportStatus.PROCESSING
        self.import_log.save(update_fields=['status'])

        rows = list(parse_sat_csv_rows(file_content, self.analysis.header_row_index))
        total_students = len(rows)
        self.import_log.total_students = total_students
        self.import_log.save(update_fields=['total_students'])

        manual_overrides = self.import_log.manual_overrides or {}

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
                    state_student_id = _to_str(row.get("STATE_STUDENT_ID", ""))
                    cb_id = _to_str(row.get("CB_ID", ""))
                    last_name = _to_str(row.get("NAME_LAST", ""))
                    first_name = _to_str(row.get("NAME_FIRST", ""))
                    middle_initial = _to_str(row.get("NAME_MI", ""))
                    gender = _to_str(row.get("GENDER", ""))
                    school_code = _to_str(row.get("AI_CODE", ""))
                    school_name_raw = _to_str(row.get("AI_NAME", ""))
                    school_key = (school_name_raw, school_code)
                    school_name = school_cache.get(school_key)
                    if school_name is None:
                        school_name = resolve_school_short_name(school_name_raw, school_code)
                        school_cache[school_key] = school_name

                    dob_date = parse_date(row.get("BIRTH_DATE", ""))
                    test_date = parse_date(row.get("LATEST_SAT_DATE", ""))

                    record_key = build_record_key(
                        cb_id, state_student_id, last_name, first_name,
                        dob_date.isoformat() if dob_date else "",
                        test_date.isoformat() if test_date else "",
                    )

                    # Skip blank rows (no identity and no scores)
                    total_score = parse_int(row.get("LATEST_SAT_TOTAL", ""))
                    ebrw_score = parse_int(row.get("LATEST_SAT_EBRW", ""))
                    math_score = parse_int(row.get("LATEST_SAT_MATH_SECTION", ""))
                    if not state_student_id and not cb_id and not last_name and not first_name:
                        continue

                    match_result = self.matcher.match_student(
                        record_key=record_key,
                        state_student_id=state_student_id,
                        first_name=first_name,
                        last_name=last_name,
                        dob=dob_date.isoformat() if dob_date else "",
                        school=school_name,
                        cb_id=cb_id,
                    )

                    ssid = ""
                    match_method = ""
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
                                'cb_id': cb_id,
                                'first_name': first_name,
                                'last_name': last_name,
                                'dob': dob_date.isoformat() if dob_date else "",
                                'school': school_name,
                                'reason': match_result.reason or 'No matching student found',
                            })

                    percentiles = {}
                    for region, metrics in PERCENTILE_REGIONS.items():
                        for metric, col_name in metrics.items():
                            percentiles[(region, metric)] = parse_int(row.get(col_name, ""))

                    candidates.append({
                        'record_key': record_key,
                        'ssid': ssid,
                        'state_student_id': state_student_id,
                        'cb_id': cb_id,
                        'last_name': last_name,
                        'first_name': first_name,
                        'middle_initial': middle_initial,
                        'birth_date': dob_date,
                        'gender': gender,
                        'school_code': school_code,
                        'school_name': school_name,
                        'test_date': test_date,
                        'total_score': total_score,
                        'ebrw_score': ebrw_score,
                        'math_score': math_score,
                        'percentiles': percentiles,
                        'match_method': match_method,
                        'match_confidence': match_confidence,
                    })

                except Exception as e:
                    errors.append({
                        'row': row_num,
                        'error': str(e),
                        'record_key': row.get('CB_ID', '') or row.get('STATE_STUDENT_ID', 'unknown'),
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
            p = c['percentiles']
            objects.append(SATResult(
                import_log=self.import_log,
                record_key=c['record_key'],
                ssid=c['ssid'],
                state_student_id=c['state_student_id'],
                cb_id=c['cb_id'],
                last_name=c['last_name'],
                first_name=c['first_name'],
                middle_initial=c['middle_initial'],
                birth_date=c['birth_date'],
                gender=c['gender'],
                school_code=c['school_code'],
                school_name=c['school_name'],
                test_date=c['test_date'],
                school_year=self.import_log.school_year,
                total_score=c['total_score'],
                ebrw_score=c['ebrw_score'],
                math_score=c['math_score'],
                percentile_state_total=p.get(('state', 'total')),
                percentile_state_rw=p.get(('state', 'rw')),
                percentile_state_math=p.get(('state', 'math')),
                percentile_country_total=p.get(('country', 'total')),
                percentile_country_rw=p.get(('country', 'rw')),
                percentile_country_math=p.get(('country', 'math')),
                percentile_natrep_total=p.get(('natrep', 'total')),
                percentile_natrep_rw=p.get(('natrep', 'rw')),
                percentile_natrep_math=p.get(('natrep', 'math')),
                percentile_natuser_total=p.get(('natuser', 'total')),
                percentile_natuser_rw=p.get(('natuser', 'rw')),
                percentile_natuser_math=p.get(('natuser', 'math')),
                match_method=c['match_method'],
                match_confidence=c['match_confidence'],
            ))

        if objects:
            SATResult.objects.bulk_create(
                objects,
                update_conflicts=True,
                unique_fields=['record_key'],
                update_fields=[
                    'ssid', 'state_student_id', 'cb_id', 'last_name', 'first_name',
                    'middle_initial', 'birth_date', 'gender', 'school_code',
                    'school_name', 'test_date', 'school_year', 'total_score',
                    'ebrw_score', 'math_score',
                    'percentile_state_total', 'percentile_state_rw', 'percentile_state_math',
                    'percentile_country_total', 'percentile_country_rw', 'percentile_country_math',
                    'percentile_natrep_total', 'percentile_natrep_rw', 'percentile_natrep_math',
                    'percentile_natuser_total', 'percentile_natuser_rw', 'percentile_natuser_math',
                    'match_method', 'match_confidence', 'import_log',
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
        self.import_log.status = SATImportStatus.COMPLETED if not errors else SATImportStatus.PARTIAL
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
                SATResult.objects.filter(record_key__in=key_list[i:i + chunk])
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


def run_sat_import_async(import_log_id: int, file_content: str, analysis_data: dict, user_id: int):
    from django.contrib.auth import get_user_model
    from dataLoader.utils.sat_column_analyzer import SATAnalysisResult

    def _run():
        connection.close()

        try:
            import_log = SATImportLog.objects.get(pk=import_log_id)
            User = get_user_model()
            user = User.objects.get(pk=user_id)

            analysis = SATAnalysisResult(
                student_columns=analysis_data.get('student_columns', {}),
                score_columns=analysis_data.get('score_columns', {}),
                percentile_columns=analysis_data.get('percentile_columns', {}),
                total_students=analysis_data.get('total_students', 0),
                total_with_scores=analysis_data.get('total_with_scores', 0),
                total_missing_state_id=analysis_data.get('total_missing_state_id', 0),
                available_percentile_regions=analysis_data.get('available_percentile_regions', []),
                header_row_index=analysis_data.get('header_row_index', 0),
                total_columns=analysis_data.get('total_columns', 0),
                warnings=analysis_data.get('warnings', []),
            )

            loader = SATResponseLoader(import_log, analysis)
            loader.load_sat_data(file_content)

            # Keep Data Exploration current without a manual sync. The sync
            # only publishes rows from imports flagged is_published, so this is
            # a no-op until the import is published (and runs again on publish).
            run_sat_clickhouse_sync_async()

        except Exception as e:
            import_log = SATImportLog.objects.get(pk=import_log_id)
            import_log.status = SATImportStatus.FAILED
            import_log.error_log = [{'error': str(e)}]
            import_log.completed_at = timezone.now()
            import_log.save()
        finally:
            connection.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
