"""
Student matcher for College Board PSAT results.

Identifier mapping (College Board file → studata):
  STATE_STUDENT_ID    → ssid
  DISTRICT_STUDENT_ID → student_id (local SIS id)

Matching is resilient to formatting differences and to a school year that has
not been loaded into the SIS yet:

Tier 0: STATE_STUDENT_ID → StudentEnroll.ssid / CurrentStudent.ssid for the
        selected school year.
Tier 1: same lookup ignoring leading zeros / Excel formatting differences.
Tier 2: STATE_STUDENT_ID → ssid in any school year (state IDs are stable and
        identify the same student across years).
Tier 3: DISTRICT_STUDENT_ID → student_id → that row's ssid, same fallback.
Tier 4+: name + DOB fuzzy match via StudentEnroll (AP matcher).
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Set

from django.db.models import Q

from studata.models import CurrentStudent, StudentEnroll

from dataLoader.utils.ap_student_matcher import (
    APStudentMatcher,
    _normalize_name,
    _parse_birth_date,
    _to_str,
)

# Excel sometimes exports long numeric IDs in scientific notation (7.2474E+09).
_SCI_NOTATION_RE = re.compile(r'^[+-]?\d+(?:\.\d+)?[eE][+-]?\d+$')


def _normalize_identifier(value) -> str:
    """Normalize a state/local student identifier for comparison.

    Handles Excel text markers ('123), trailing .0 (123.0), scientific
    notation (1.23E+09) and surrounding whitespace.
    """
    s = _to_str(value)
    if not s:
        return ''
    s = s.lstrip("'").strip()
    if _SCI_NOTATION_RE.match(s):
        try:
            dec = Decimal(s)
            if dec == dec.to_integral_value():
                s = format(dec.quantize(Decimal(1)), 'f')
        except (InvalidOperation, ValueError):
            pass
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    return s


def _identifier_key(value) -> str:
    """Comparison key that tolerates leading zeros and formatting noise."""
    s = _normalize_identifier(value)
    if not s:
        return ''
    if s.isdigit():
        return s.lstrip('0') or '0'
    return s.upper()


def _normalize_ssid(value) -> str:
    """Normalize SSID / STATE_STUDENT_ID for comparison."""
    return _normalize_identifier(value)


def _normalize_student_id(value) -> str:
    """Normalize DISTRICT_STUDENT_ID / SIS student_id for comparison."""
    return _normalize_identifier(value)


@dataclass
class PSATMatchResult:
    record_key: str
    state_student_id: str
    district_student_id: str
    first_name: str
    last_name: str
    dob: str
    school: str
    cb_id: str = ''
    matched: bool = False
    ssid: str = ''
    match_method: str = ''
    confidence: float = 0.0
    reason: str = ''


class PSATStudentMatcher(APStudentMatcher):
    """Match PSAT file columns to CurrentStudent / StudentEnroll identities."""

    def __init__(self, school_year: str):
        super().__init__(school_year)
        # Selected school year indexes.
        self._ssid_index: Dict[str, str] = {}
        self._ssid_key_index: Dict[str, str] = {}
        self._student_id_index: Dict[str, str] = {}
        self._student_id_key_index: Dict[str, str] = {}
        self._year_loaded = False

        # Cross-year fallback indexes (loaded only when a lookup misses).
        self._global_ssid_index: Dict[str, str] = {}
        self._global_ssid_key_index: Dict[str, str] = {}
        self._global_student_id_index: Dict[str, str] = {}
        self._global_student_id_key_index: Dict[str, str] = {}
        self._global_name_dob: Dict[tuple, str] = {}
        self._global_loaded = False

        self._current_student_count = 0
        self._enrollment_count = 0

    # ── index construction ────────────────────────────────────────────────
    @staticmethod
    def _index_row(
        row: dict,
        ssid_index: Dict[str, str],
        ssid_key_index: Dict[str, str],
        student_id_index: Dict[str, str],
        student_id_key_index: Dict[str, str],
    ) -> None:
        ssid = _normalize_ssid(row.get('ssid'))
        if not ssid:
            return
        ssid_index.setdefault(ssid, ssid)
        key = _identifier_key(ssid)
        if key:
            ssid_key_index.setdefault(key, ssid)

        student_id = _normalize_student_id(row.get('student_id'))
        if student_id:
            student_id_index.setdefault(student_id, ssid)
            id_key = _identifier_key(student_id)
            if id_key:
                student_id_key_index.setdefault(id_key, ssid)

    def _index_global_name_dob(self, row: dict) -> None:
        ssid = _normalize_ssid(row.get('ssid'))
        if not ssid:
            return
        last = _normalize_name(row.get('last_name'))
        first = _normalize_name(row.get('first_name'))
        dob = _parse_birth_date(row.get('birth_date'))
        if not (last and first and dob):
            return
        self._global_name_dob.setdefault((last, first, dob), ssid)
        # Some sources reverse first/last; index the swapped form too.
        self._global_name_dob.setdefault((first, last, dob), ssid)

    def _load_year_maps(self) -> None:
        if self._year_loaded:
            return

        enrollments = StudentEnroll.objects.filter(
            school_year=self.school_year,
        ).values('ssid', 'student_id')
        self._enrollment_count = len(enrollments)
        for row in enrollments:
            self._index_row(
                row,
                self._ssid_index,
                self._ssid_key_index,
                self._student_id_index,
                self._student_id_key_index,
            )

        current_students = CurrentStudent.objects.filter(
            school_year=self.school_year,
        ).values('ssid', 'student_id')
        self._current_student_count = len(current_students)
        for row in current_students:
            self._index_row(
                row,
                self._ssid_index,
                self._ssid_key_index,
                self._student_id_index,
                self._student_id_key_index,
            )

        self._year_loaded = True

    def _load_global_maps(self) -> None:
        """Index identifiers from every school year.

        Used as a fallback so an SSID still resolves when the selected school
        year has not been synced yet (e.g. the newest year is still empty) or
        when the file is loaded against the wrong year.
        """
        if self._global_loaded:
            return

        enrollments = (
            StudentEnroll.objects.exclude(Q(ssid='') | Q(ssid__isnull=True))
            .values(
                'ssid', 'student_id', 'first_name', 'last_name', 'birth_date',
            )
            .iterator(chunk_size=2000)
        )
        for row in enrollments:
            self._index_row(
                row,
                self._global_ssid_index,
                self._global_ssid_key_index,
                self._global_student_id_index,
                self._global_student_id_key_index,
            )
            self._index_global_name_dob(row)

        current_students = (
            CurrentStudent.objects.exclude(Q(ssid='') | Q(ssid__isnull=True))
            .values('ssid', 'student_id')
            .iterator(chunk_size=2000)
        )
        for row in current_students:
            self._index_row(
                row,
                self._global_ssid_index,
                self._global_ssid_key_index,
                self._global_student_id_index,
                self._global_student_id_key_index,
            )

        self._global_loaded = True

    # ── lookups ───────────────────────────────────────────────────────────
    def _lookup_state_id(self, state_id: str) -> tuple[str, str]:
        ssid = self._ssid_index.get(state_id)
        if ssid:
            return ssid, 'ssid'

        key = _identifier_key(state_id)
        if key:
            ssid = self._ssid_key_index.get(key)
            if ssid:
                return ssid, 'ssid'

        # The selected year did not contain the ID; try every year before
        # falling back to name/DOB matching.
        self._load_global_maps()
        ssid = self._global_ssid_index.get(state_id)
        if ssid:
            return ssid, 'ssid_all_years'

        if key:
            ssid = self._global_ssid_key_index.get(key)
            if ssid:
                return ssid, 'ssid_all_years'

        return '', ''

    def _lookup_student_id(self, student_id: str) -> tuple[str, str]:
        ssid = self._student_id_index.get(student_id)
        if ssid:
            return ssid, 'sis_student_id'

        key = _identifier_key(student_id)
        if key:
            ssid = self._student_id_key_index.get(key)
            if ssid:
                return ssid, 'sis_student_id'

        self._load_global_maps()
        ssid = self._global_student_id_index.get(student_id)
        if ssid:
            return ssid, 'sis_student_id_all_years'

        if key:
            ssid = self._global_student_id_key_index.get(key)
            if ssid:
                return ssid, 'sis_student_id_all_years'

        return '', ''

    @property
    def total_current_students_loaded(self) -> int:
        self._load_year_maps()
        return self._current_student_count

    @property
    def total_enrollment_loaded(self) -> int:
        self._load_year_maps()
        return self._enrollment_count

    def match_student(
        self,
        record_key: str,
        state_student_id: str,
        district_student_id: str,
        first_name: str,
        last_name: str,
        dob: str,
        school: str,
        cb_id: str = '',
    ) -> PSATMatchResult:
        self._load_cache()
        self._load_year_maps()

        result = PSATMatchResult(
            record_key=record_key,
            state_student_id=_normalize_ssid(state_student_id),
            district_student_id=_normalize_student_id(district_student_id),
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            school=school,
            cb_id=cb_id,
        )

        state_id = _normalize_ssid(state_student_id)
        if state_id:
            ssid, method = self._lookup_state_id(state_id)
            if ssid:
                result.matched = True
                result.ssid = ssid
                result.match_method = method
                result.confidence = 1.0
                return result

        district_id = _normalize_student_id(district_student_id)
        if district_id:
            ssid, method = self._lookup_student_id(district_id)
            if ssid:
                result.matched = True
                result.ssid = ssid
                result.match_method = method
                result.confidence = 1.0
                return result

        # Last resort: name + DOB fuzzy match (AP matcher tiers).
        ap_result = super().match_student(
            ap_number=record_key,
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            ap_school=school,
        )
        result.matched = ap_result.matched
        result.ssid = ap_result.ssid
        result.match_method = ap_result.match_method
        result.confidence = ap_result.confidence
        result.reason = ap_result.reason

        if not result.matched:
            # The selected year may have no enrollment rows at all. Try an
            # exact name + DOB lookup across every school year before giving up.
            self._load_global_maps()
            norm_last = _normalize_name(last_name)
            norm_first = _normalize_name(first_name)
            parsed_dob = _parse_birth_date(dob)
            if norm_last and norm_first and parsed_dob:
                ssid = self._global_name_dob.get((norm_last, norm_first, parsed_dob))
                if ssid:
                    result.matched = True
                    result.ssid = ssid
                    result.match_method = 'exact_name_dob_all_years'
                    result.confidence = 0.9
                    return result

        if not result.matched:
            parts = []
            if state_id:
                parts.append(
                    f'STATE_STUDENT_ID {state_id} not found in student_enroll/current_student'
                )
            if district_id:
                parts.append(
                    f'DISTRICT_STUDENT_ID {district_id} not found in student_enroll/current_student'
                )
            if parts:
                result.reason = '; '.join(parts)
            elif not result.reason:
                result.reason = 'No matching student found'

        return result

    # ── manual lookup helper ──────────────────────────────────────────────
    def search_students(
        self, first_name: str = '', last_name: str = '', school: str = ''
    ) -> List[dict]:
        """Search students by name for manual SSID overrides.

        Searches the selected school year first, then every other year so a
        student can still be found when the newest year has not been synced.
        """
        self._load_cache()
        results = super().search_students(first_name, last_name, school)

        seen: Set[str] = {r.get('ssid') for r in results if r.get('ssid')}

        qs = StudentEnroll.objects.exclude(Q(ssid='') | Q(ssid__isnull=True))
        if (first_name or '').strip():
            qs = qs.filter(first_name__icontains=first_name.strip())
        if (last_name or '').strip():
            qs = qs.filter(last_name__icontains=last_name.strip())

        cross_year = qs.order_by('-school_year').values(
            'ssid', 'student_id', 'first_name', 'last_name',
            'grade', 'school_name', 'school_year',
        )[:200]

        for row in cross_year:
            ssid = _normalize_ssid(row.get('ssid'))
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            results.append({
                'ssid': ssid,
                'student_id': _to_str(row.get('student_id', '')),
                'first_name': _to_str(row.get('first_name', '')),
                'last_name': _to_str(row.get('last_name', '')),
                'grade': _to_str(row.get('grade', '')),
                'school': _to_str(row.get('school_name', '')),
                'school_year': _to_str(row.get('school_year', '')),
            })
            if len(results) >= 20:
                break

        return results
