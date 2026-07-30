"""
Student matcher for AP test data.

Matches students by name + date of birth using the same four-tier strategy
as apTest/transform_ap_data.py, querying the local StudentEnroll table.

The matching algorithm is a direct port from:
  apTest/transform_ap_data.py : find_ssid() + load_sis_students()

Additional tiers beyond the original for robustness:
  5. Swapped name-order exact match (AP data sometimes has first/last reversed)
  6. Token-based fuzzy match (handles compound/split name differences)
"""
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from django.db.models import Q
from studata.models import StudentEnroll


SCHOOL_MAP = {
    "Indio High School": "Indio HS",
    "La Quinta High School": "La Quinta HS",
    "Palm Desert High School": "Palm Desert HS",
    "Shadow Hills High School": "Shadow Hills HS",
    "John Glenn Middle School of In": "Glenn",
    "La Quinta Middle School": "La Quinta MS",
}

_FUZZY_THRESHOLD = 0.80


@dataclass
class APMatchResult:
    ap_number: str
    first_name: str
    last_name: str
    dob: str
    ap_school: str
    matched: bool = False
    ssid: str = ''
    match_method: str = ''
    confidence: float = 0.0
    reason: str = ''


def _to_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_name(value) -> str:
    s = _to_str(value)
    return "".join(ch.lower() for ch in s if ch.isalnum())


def _name_tokens(value) -> List[str]:
    """Tokenize a name into normalized parts, preserving spaces."""
    parts = _to_str(value).lower().split()
    return ["".join(ch for ch in p if ch.isalnum()) for p in parts if "".join(ch for ch in p if ch.isalnum())]


def _parse_birth_date(value) -> str:
    """Parse birth date from MMDDYY (AP format), ISO string, or datetime object to YYYY-MM-DD."""
    s = _to_str(value)
    if not s:
        return ""
    if s[:4].isdigit() and s[4:5] == "-":
        return s[:10]
    s = s.zfill(6)
    mm, dd, yy = s[0:2], s[2:4], s[4:6]
    year_int = int(yy)
    full_year = 1900 + year_int if year_int >= 50 else 2000 + year_int
    return f"{full_year}-{mm}-{dd}"


def _name_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _token_ratio(ap_tokens: List[str], sis_tokens: List[str]) -> float:
    """Score how well AP name tokens match SIS name tokens.
    
    For each AP token, find the best-matching SIS token.
    Returns the average of best-match scores weighted by token count.
    """
    if not ap_tokens or not sis_tokens:
        return 0.0
    total = 0.0
    for apt in ap_tokens:
        best = max((_name_ratio(apt, st) for st in sis_tokens), default=0.0)
        total += best
    return total / len(ap_tokens)


def _dob_ratio(dob_a: str, dob_b: str) -> float:
    if dob_a == dob_b:
        return 1.0
    if not dob_a or not dob_b:
        return 0.0
    parts_a = dob_a.split("-")
    parts_b = dob_b.split("-")
    if len(parts_a) != 3 or len(parts_b) != 3:
        return 0.0
    year_a, month_a, day_a = parts_a[0], parts_a[1], parts_a[2]
    year_b, month_b, day_b = parts_b[0], parts_b[1], parts_b[2]
    year_matches = year_a == year_b
    month_day_match = month_a == month_b and day_a == day_b
    if year_matches and month_day_match:
        return 1.0
    if month_day_match:
        return 0.85
    if year_matches:
        return 0.50
    matches = sum(1 for a, b in zip(parts_a, parts_b) if a == b)
    return matches / 3.0


class APStudentMatcher:
    """
    Matches AP students to district SSIDs using the StudentEnroll table.

    Matching tiers (mirrors find_ssid from apTest/transform_ap_data.py):
      1. Exact (norm_last, norm_first, parsed_dob)
      2. Fuzzy name (avg ratio >= 0.80) + same school + exact DOB
      3. Fuzzy name + same school + fuzzy DOB (allows off-by-year)
      4. First name + DOB unique fallback

    Additional tiers for robustness:
      5. Swapped name-order exact match
      6. Token-based fuzzy (handles compound names like 'Luisa' vs 'Luisa Fernanda')
    """

    def __init__(self, school_year: str):
        self.school_year = school_year
        self._exact_lookup: Dict[Tuple[str, str, str], str] = {}
        self._fuzzy_candidates: List[dict] = []
        self._first_dob_lookup: Dict[Tuple[str, str], str] = {}
        self._raw_students: List[dict] = []
        self._loaded = False

    def _load_cache(self):
        if self._loaded:
            return

        enrollments = StudentEnroll.objects.filter(
            school_year=self.school_year,
        ).exclude(
            Q(ssid='') | Q(ssid__isnull=True)
        ).values('ssid', 'student_id', 'first_name', 'last_name', 'birth_date', 'school_name', 'grade')

        seen_ssids: set = set()
        first_dob_counts: Dict[Tuple[str, str], int] = {}
        first_dob_ssid: Dict[Tuple[str, str], str] = {}

        for enr in enrollments:
            ssid = _to_str(enr['ssid'])
            first = _normalize_name(enr['first_name'])
            last = _normalize_name(enr['last_name'])
            dob = _parse_birth_date(enr['birth_date'])
            school = _to_str(enr['school_name'])
            first_tokens = _name_tokens(enr.get('first_name', ''))
            last_tokens = _name_tokens(enr.get('last_name', ''))

            if not first or not last or not dob:
                continue

            exact_key = (last, first, dob)
            if exact_key not in self._exact_lookup:
                self._exact_lookup[exact_key] = ssid

            if ssid not in seen_ssids:
                seen_ssids.add(ssid)
                self._fuzzy_candidates.append({
                    'ssid': ssid,
                    'norm_last': last,
                    'norm_first': first,
                    'dob': dob,
                    'school': school,
                    'first_tokens': first_tokens,
                    'last_tokens': last_tokens,
                    'first_name': _to_str(enr.get('first_name', '')),
                    'last_name': _to_str(enr.get('last_name', '')),
                })
                self._raw_students.append({
                    'ssid': ssid,
                    'student_id': _to_str(enr.get('student_id', '')),
                    'first_name': _to_str(enr.get('first_name', '')),
                    'last_name': _to_str(enr.get('last_name', '')),
                    'grade': _to_str(enr.get('grade', '')),
                    'school': school,
                })

            first_dob_key = (first, dob)
            if ssid not in seen_ssids:
                first_dob_counts[first_dob_key] = first_dob_counts.get(first_dob_key, 0) + 1
                first_dob_ssid[first_dob_key] = ssid
            else:
                if first_dob_key in first_dob_counts:
                    if first_dob_counts[first_dob_key] == 1:
                        del first_dob_ssid[first_dob_key]
                    first_dob_counts[first_dob_key] += 1

        self._first_dob_lookup = {
            key: ssid
            for key, ssid in first_dob_ssid.items()
            if first_dob_counts.get(key, 0) == 1
        }

        self._loaded = True

    def match_student(
        self,
        ap_number: str,
        first_name: str,
        last_name: str,
        dob: str,
        ap_school: str,
    ) -> APMatchResult:
        self._load_cache()

        result = APMatchResult(
            ap_number=ap_number,
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            ap_school=ap_school,
        )

        norm_first = _normalize_name(first_name)
        norm_last = _normalize_name(last_name)
        parsed_dob = _parse_birth_date(dob)

        if not parsed_dob:
            result.reason = "Could not parse date of birth"
            return result

        # Tier 1: Exact match (norm_last, norm_first, dob)
        exact_key = (norm_last, norm_first, parsed_dob)
        ssid = self._exact_lookup.get(exact_key)
        if ssid:
            result.matched = True
            result.ssid = ssid
            result.match_method = 'exact_name_dob'
            result.confidence = 1.0
            return result

        # Tier 2: Fuzzy same-school + same DOB
        sis_school = SCHOOL_MAP.get(ap_school, ap_school)
        best_ssid, best_score = self._fuzzy_search(norm_last, norm_first, parsed_dob, sis_school, fuzzy_dob=False)
        if best_score >= _FUZZY_THRESHOLD:
            result.matched = True
            result.ssid = best_ssid
            result.match_method = 'fuzzy_same_school'
            result.confidence = round(best_score, 2)
            return result

        # Tier 3: Fuzzy + fuzzy DOB
        best_ssid, best_score = self._fuzzy_search(norm_last, norm_first, parsed_dob, sis_school, fuzzy_dob=True)
        if best_score >= _FUZZY_THRESHOLD:
            result.matched = True
            result.ssid = best_ssid
            result.match_method = 'fuzzy_fuzzy_dob'
            result.confidence = round(best_score, 2)
            return result

        # Tier 4: First name + DOB unique
        first_dob_key = (norm_first, parsed_dob)
        ssid = self._first_dob_lookup.get(first_dob_key)
        if ssid:
            result.matched = True
            result.ssid = ssid
            result.match_method = 'first_name_dob'
            result.confidence = 0.75
            return result

        # Tier 5: Swapped name-order exact match (AP data sometimes has first/last reversed)
        swapped_key = (norm_first, norm_last, parsed_dob)
        ssid = self._exact_lookup.get(swapped_key)
        if ssid:
            result.matched = True
            result.ssid = ssid
            result.match_method = 'swapped_name_dob'
            result.confidence = 0.95
            return result

        # Tier 6: Token-based fuzzy search (handles compound names)
        ap_first_tokens = _name_tokens(first_name)
        ap_last_tokens = _name_tokens(last_name)
        best_ssid, best_score = self._token_fuzzy_search(
            ap_first_tokens, ap_last_tokens, parsed_dob, sis_school
        )
        if best_score >= _FUZZY_THRESHOLD:
            result.matched = True
            result.ssid = best_ssid
            result.match_method = 'token_fuzzy'
            result.confidence = round(best_score, 2)
            return result

        result.reason = "No matching student found in StudentEnroll"
        return result

    def _fuzzy_search(
        self,
        norm_last: str,
        norm_first: str,
        parsed_dob: str,
        sis_school: str,
        fuzzy_dob: bool = False,
    ) -> Tuple[str, float]:
        best_ssid = ""
        best_score = 0.0
        for c in self._fuzzy_candidates:
            if c['school'] != sis_school:
                if fuzzy_dob:
                    continue
                if c['dob'] != parsed_dob:
                    continue
            if not fuzzy_dob and c['dob'] != parsed_dob:
                continue
            last_score = _name_ratio(norm_last, c['norm_last'])
            first_score = _name_ratio(norm_first, c['norm_first'])
            if fuzzy_dob:
                dob_score = _dob_ratio(parsed_dob, c['dob'])
                score = (last_score + first_score + dob_score) / 3.0
            else:
                score = (last_score + first_score) / 2.0
            if score > best_score:
                best_score = score
                best_ssid = c['ssid']
        return best_ssid, best_score

    def _token_fuzzy_search(
        self,
        ap_first_tokens: List[str],
        ap_last_tokens: List[str],
        parsed_dob: str,
        sis_school: str,
    ) -> Tuple[str, float]:
        best_ssid = ""
        best_score = 0.0
        for c in self._fuzzy_candidates:
            if c['school'] != sis_school:
                continue
            first_score = _token_ratio(ap_first_tokens, c['first_tokens'])
            last_score = _token_ratio(ap_last_tokens, c['last_tokens'])
            dob_score = _dob_ratio(parsed_dob, c['dob'])
            score = (first_score + last_score + dob_score) / 3.0
            if score > best_score:
                best_score = score
                best_ssid = c['ssid']
        return best_ssid, best_score

    @property
    def total_students_loaded(self) -> int:
        self._load_cache()
        return len(self._raw_students)

    def search_students(
        self, first_name: str, last_name: str, school: str = ''
    ) -> List[dict]:
        """Search for students by name for manual SSID override lookups."""
        self._load_cache()

        results = []
        first_lower = first_name.lower() if first_name else ''
        last_lower = last_name.lower() if last_name else ''

        for student in self._raw_students:
            if first_lower and first_lower not in student['first_name'].lower():
                continue
            if last_lower and last_lower not in student['last_name'].lower():
                continue
            results.append(student)
            if len(results) >= 20:
                break

        return results
