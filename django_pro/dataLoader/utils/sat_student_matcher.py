"""
Student matcher for College Board SAT results data.

Primary strategy: match by the file's STATE_STUDENT_ID, validated against the
local StudentEnroll table for the selected school year.

Fallback (when the state ID is blank or not found): the same fuzzy
name + date-of-birth tiers used by the AP loader (APStudentMatcher), plus a
manual SSID override entered by the user during preview.
"""
from dataclasses import dataclass
from typing import Optional

from django.db.models import Q

from studata.models import StudentEnroll

from dataLoader.utils.ap_student_matcher import (
    APStudentMatcher, _to_str,
)


@dataclass
class SATMatchResult:
    record_key: str
    state_student_id: str
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


class SATStudentMatcher(APStudentMatcher):
    """Matches SAT students to district SSIDs.

    Tier 0: exact STATE_STUDENT_ID present in StudentEnroll for the school year.
    Tiers 1-6: the AP fuzzy name + DOB strategy (inherited).
    """

    def __init__(self, school_year: str):
        super().__init__(school_year)
        self._ssid_set = set()
        self._ssid_loaded = False

    def _load_ssid_set(self):
        if self._ssid_loaded:
            return
        ssids = (
            StudentEnroll.objects.filter(school_year=self.school_year)
            .exclude(Q(ssid='') | Q(ssid__isnull=True))
            .values_list('ssid', flat=True)
        )
        self._ssid_set = {_to_str(s) for s in ssids if _to_str(s)}
        self._ssid_loaded = True

    def match_student(
        self,
        record_key: str,
        state_student_id: str,
        first_name: str,
        last_name: str,
        dob: str,
        school: str,
        cb_id: str = '',
    ) -> SATMatchResult:
        self._load_cache()
        self._load_ssid_set()

        result = SATMatchResult(
            record_key=record_key,
            state_student_id=_to_str(state_student_id),
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            school=school,
            cb_id=cb_id,
        )

        # Tier 0: validated state student id
        sid = _to_str(state_student_id)
        if sid and sid in self._ssid_set:
            result.matched = True
            result.ssid = sid
            result.match_method = 'state_student_id'
            result.confidence = 1.0
            return result

        # Tiers 1-6: delegate to the AP fuzzy name + DOB matcher
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
        return result
