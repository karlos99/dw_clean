"""
Student matcher for International Baccalaureate (IB) test results.

Matches IB candidates to district SSIDs using the StudentEnroll and CurrentStudent
tables, strictly restricting matches to students who were enrolled in La Quinta High School
during the selected school year.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Dict, List, Optional, Tuple, Any

from django.db.models import Q
from studata.models import StudentEnroll, CurrentStudent


LA_QUINTA_HS_NAMES = ['La Quinta HS', 'La Quinta High School', 'LQHS']
LA_QUINTA_STATE_CODE = '3330669'
SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x'}
_FUZZY_THRESHOLD = 0.80

COMPOUND_PREFIXES_2 = {'de la', 'van der', 'von der'}
COMPOUND_PREFIXES_1 = {'de', 'del', 'van', 'von', 'san', 'santa', 'da', 'di', 'dos', 'la', 'le'}

NICKNAMES = {
    'alex': {'alexander', 'alexandra', 'alejandro', 'alejandra'},
    'chris': {'christopher', 'christian', 'christina', 'christine'},
    'matt': {'matthew', 'mateo'},
    'nick': {'nicholas', 'nicolas', 'nicole', 'nikki'},
    'gabe': {'gabriel', 'gabriella', 'gabrielle'},
    'sam': {'samuel', 'samantha'},
    'zach': {'zachary', 'zackary'},
    'dan': {'daniel', 'danielle', 'danny'},
    'tom': {'thomas', 'tommy'},
    'mike': {'michael', 'miguel'},
    'will': {'william', 'liam', 'bill', 'billy'},
    'ben': {'benjamin', 'benny'},
    'nate': {'nathan', 'nathaniel'},
    'josh': {'joshua'},
    'bella': {'isabella', 'isabelle', 'annabella'},
    'maddie': {'madeline', 'madelyn', 'madison'},
    'katie': {'katelyn', 'katherine', 'kathryn', 'kaitlyn', 'kate'},
    'tony': {'anthony', 'antonio'},
    'liz': {'elizabeth', 'eliza', 'beth'},
    'abby': {'abigail', 'abygail'},
    'leo': {'leonardo', 'leon'},
}
# Bidirectional lookup
NICK_LOOKUP: Dict[str, Set[str]] = {}
for k, v in NICKNAMES.items():
    NICK_LOOKUP.setdefault(k, set()).update(v)
    for name in v:
        NICK_LOOKUP.setdefault(name, set()).add(k)
        NICK_LOOKUP[name].update(v - {name})


@dataclass
class IBMatchResult:
    personal_code: str
    candidate_name: str
    matched: bool = False
    ssid: str = ''
    student_id: str = ''
    match_method: str = ''
    confidence: float = 0.0
    reason: str = ''
    student_name: str = ''


def _to_str(val) -> str:
    if val is None:
        return ''
    return str(val).strip()


def _norm(val) -> str:
    s = _to_str(val)
    return ''.join(ch.lower() for ch in s if ch.isalnum())


def _name_tokens(val) -> List[str]:
    parts = re.split(r'[^a-zA-Z0-9]+', _to_str(val).lower())
    return [p for p in parts if p and p not in SUFFIXES]


def _strip_suffix(tokens: List[str]) -> List[str]:
    return [t for t in tokens if t not in SUFFIXES]


def _parse_candidate_name(raw_name: str) -> Tuple[str, str, str, List[str], List[str]]:
    """
    Parse candidate name into:
      (clean_last, clean_first, clean_mid, last_tokens, first_tokens)

    CRITICAL RULE: The first part of the candidate name is the last name, then the first name.
    - If a comma is present (e.g. 'Last, First Middle'):
      Everything before the comma is the last name. Suffixes like 'Jr' at the end of the last
      name (e.g. 'Jones Jr, Zakee Asin') are stripped so clean_last is 'Jones'.
    - If NO comma is present (e.g. 'De La Torre Brandon' or 'Baily Gabriel Scott'):
      Checks for compound last name prefixes ('de la', 'san', etc.), otherwise takes the first
      token as the last name and subsequent tokens as first/middle.
    """
    clean_raw = _to_str(raw_name)
    if ',' in clean_raw:
        parts = clean_raw.split(',', 1)
        raw_last = parts[0].strip()
        raw_first_middle = parts[1].strip()
    else:
        # No comma: first part is last name, subsequent part is first name
        parts = clean_raw.split()
        if len(parts) >= 3 and f"{parts[0].lower()} {parts[1].lower()}" in COMPOUND_PREFIXES_2:
            raw_last = f"{parts[0]} {parts[1]} {parts[2]}"
            raw_first_middle = ' '.join(parts[3:])
        elif len(parts) >= 2 and parts[0].lower() in COMPOUND_PREFIXES_1:
            raw_last = f"{parts[0]} {parts[1]}"
            raw_first_middle = ' '.join(parts[2:])
        elif len(parts) >= 2:
            raw_last = parts[0]
            raw_first_middle = ' '.join(parts[1:])
        else:
            raw_last = clean_raw
            raw_first_middle = ''

    last_toks = _name_tokens(raw_last)
    fm_toks = _name_tokens(raw_first_middle)

    clean_last = ' '.join(last_toks)
    clean_first = fm_toks[0] if fm_toks else ''
    clean_mid = ' '.join(fm_toks[1:]) if len(fm_toks) > 1 else ''

    return clean_last, clean_first, clean_mid, last_toks, fm_toks


class IBStudentMatcher:
    """
    Matches IB candidates to district students enrolled at La Quinta High School
    for the given school year.

    Strictly restricts matches to students who were enrolled in La Quinta High School
    during the selected school year. Recognizes that IB candidate names place the last name
    first, handles suffix stripping, compound surnames, and preferred middle-name usage.
    """

    def __init__(self, school_year: str, manual_overrides: Optional[Dict[str, str]] = None):
        self.school_year = school_year
        self.manual_overrides = manual_overrides or {}
        self._exact_full: Dict[str, dict] = {}
        self._exact_last_first: Dict[Tuple[str, str], dict] = {}
        self._by_last_token: Dict[str, List[dict]] = {}
        self._by_middle_token: Dict[Tuple[str, str], dict] = {}
        self._students_list: List[dict] = []
        self._loaded = False

    def _load_cache(self):
        if self._loaded:
            return

        # Query students enrolled in La Quinta HS for the target school year
        enrollments = StudentEnroll.objects.filter(
            school_year=self.school_year,
        ).filter(
            Q(school_name__icontains='La Quinta HS') |
            Q(school_name__icontains='La Quinta High School') |
            Q(state_school_code=LA_QUINTA_STATE_CODE)
        ).exclude(
            Q(ssid='') | Q(ssid__isnull=True)
        ).values('ssid', 'student_id', 'first_name', 'last_name', 'middle_name', 'grade')

        seen_ssids = set()

        for enr in enrollments:
            ssid = _to_str(enr['ssid'])
            if not ssid or ssid in seen_ssids:
                continue
            seen_ssids.add(ssid)

            ln_toks = _name_tokens(enr['last_name'])
            fn_toks = _name_tokens(enr['first_name'])
            mn_toks = _name_tokens(enr['middle_name'])

            clean_last = ' '.join(ln_toks)
            clean_first = fn_toks[0] if fn_toks else ''
            clean_mid = ' '.join(mn_toks)

            norm_ln = _norm(clean_last)
            norm_fn = _norm(clean_first)
            norm_mn = _norm(clean_mid)

            rec = {
                'ssid': ssid,
                'student_id': _to_str(enr['student_id']),
                'first_name': enr['first_name'] or '',
                'last_name': enr['last_name'] or '',
                'middle_name': enr['middle_name'] or '',
                'grade': enr['grade'] or '',
                'full_name': f"{enr['last_name']}, {enr['first_name']}",
                'clean_last': clean_last,
                'clean_first': clean_first,
                'clean_mid': clean_mid,
                'last_toks': ln_toks,
                'first_toks': fn_toks,
                'mid_toks': mn_toks,
                'norm_last': norm_ln,
                'norm_first': norm_fn,
            }
            self._students_list.append(rec)

            # Indexes
            if norm_ln and norm_fn:
                self._exact_full[f"{norm_ln}{norm_fn}{norm_mn}"] = rec
                self._exact_full[f"{norm_ln}{norm_fn}"] = rec
                self._exact_last_first[(norm_ln, norm_fn)] = rec

            # Index by each token of last name
            for lnt in ln_toks:
                self._by_last_token.setdefault(lnt, []).append(rec)
                # Middle name as preferred first name lookup
                for mnt in mn_toks:
                    self._by_middle_token.setdefault((lnt, mnt), rec)

        self._loaded = True

    @property
    def total_students_loaded(self) -> int:
        self._load_cache()
        return len(self._students_list)

    def match(self, personal_code: str, candidate_name: str) -> IBMatchResult:
        """Match an IB candidate against LQHS enrolled students."""
        self._load_cache()

        p_code = _to_str(personal_code)
        c_name = _to_str(candidate_name)

        # Tier 0: Manual override by personal code or candidate name
        override_val = self.manual_overrides.get(p_code) or self.manual_overrides.get(c_name)
        if override_val:
            if isinstance(override_val, dict):
                override_ssid = str(override_val.get('ssid') or '').strip()
                override_name = str(override_val.get('student_name') or '').strip()
            else:
                override_ssid = str(override_val).strip()
                override_name = ''

            # If override_ssid is not numeric and looks like a name, try global search
            if override_ssid and not override_ssid.isdigit():
                found = self.search_students_global(query=override_ssid, limit=1)
                if found:
                    override_name = f"{found[0]['last_name']}, {found[0]['first_name']}"
                    override_ssid = found[0]['ssid']

            if override_ssid:
                return IBMatchResult(
                    personal_code=p_code,
                    candidate_name=c_name,
                    matched=True,
                    ssid=override_ssid,
                    student_name=override_name or c_name,
                    match_method='manual_override',
                    confidence=1.0,
                    reason='Manual override',
                )

        c_last, c_first, c_mid, c_last_toks, c_first_toks = _parse_candidate_name(c_name)
        c_ln_norm = _norm(c_last)
        c_fn_norm = _norm(c_first)
        c_mn_norm = _norm(c_mid)
        full_norm = f"{c_ln_norm}{c_fn_norm}{c_mn_norm}"

        # Tier 1: Exact full match
        if full_norm and full_norm in self._exact_full:
            rec = self._exact_full[full_norm]
            return IBMatchResult(
                personal_code=p_code,
                candidate_name=c_name,
                matched=True,
                ssid=rec['ssid'],
                student_id=rec['student_id'],
                student_name=rec['full_name'],
                match_method='exact_full',
                confidence=1.0,
                reason='Exact full name match',
            )

        # Tier 2: Exact last + first match (with suffixes stripped)
        if (c_ln_norm, c_fn_norm) in self._exact_last_first:
            rec = self._exact_last_first[(c_ln_norm, c_fn_norm)]
            return IBMatchResult(
                personal_code=p_code,
                candidate_name=c_name,
                matched=True,
                ssid=rec['ssid'],
                student_id=rec['student_id'],
                student_name=rec['full_name'],
                match_method='exact_last_first',
                confidence=0.98,
                reason='Exact last and first name match',
            )

        # Pool candidate students sharing any surname token
        pool: List[dict] = []
        for lnt in c_last_toks:
            if lnt in self._by_last_token:
                pool.extend(self._by_last_token[lnt])

        # Deduplicate pool by ssid
        seen_pool_ssids = set()
        unique_pool = []
        for s in pool:
            if s['ssid'] not in seen_pool_ssids:
                seen_pool_ssids.add(s['ssid'])
                unique_pool.append(s)

        # Tier 3: Compound last name match + exact first name
        c_last_set = set(c_last_toks)
        for rec in unique_pool:
            s_last_set = set(rec['last_toks'])
            if c_last_set and s_last_set and (c_last_set.issubset(s_last_set) or s_last_set.issubset(c_last_set)):
                if rec['norm_first'] == c_fn_norm:
                    return IBMatchResult(
                        personal_code=p_code,
                        candidate_name=c_name,
                        matched=True,
                        ssid=rec['ssid'],
                        student_id=rec['student_id'],
                        student_name=rec['full_name'],
                        match_method='compound_last_match',
                        confidence=0.95,
                        reason='Compound last name match with exact first name',
                    )

        # Tier 4: Preferred First Name / Middle Name Match
        # E.g., candidate uses middle name as first name: 'Valdovinos, Arela' -> 'Valdovinos, Liliana Arela'
        for lnt in c_last_toks:
            if (lnt, c_fn_norm) in self._by_middle_token:
                rec = self._by_middle_token[(lnt, c_fn_norm)]
                return IBMatchResult(
                    personal_code=p_code,
                    candidate_name=c_name,
                    matched=True,
                    ssid=rec['ssid'],
                    student_id=rec['student_id'],
                    student_name=rec['full_name'],
                    match_method='middle_as_first_name',
                    confidence=0.93,
                    reason=f"Matched middle name '{c_first}' as preferred first name",
                )

        # Tier 5: Common Nickname / Diminutive Match
        possible_firsts = NICK_LOOKUP.get(c_fn_norm, set())
        if possible_firsts:
            for rec in unique_pool:
                if rec['norm_first'] in possible_firsts or any(m in possible_firsts for m in rec['mid_toks']):
                    return IBMatchResult(
                        personal_code=p_code,
                        candidate_name=c_name,
                        matched=True,
                        ssid=rec['ssid'],
                        student_id=rec['student_id'],
                        student_name=rec['full_name'],
                        match_method='nickname_match',
                        confidence=0.90,
                        reason=f"Matched nickname '{c_first}' with '{rec['first_name']}'",
                    )

        # Tier 6: Segment-Aware Fuzzy Matching
        # If pool is empty, expand pool using last name similarity >= 0.75
        if not unique_pool and c_last:
            for s in self._students_list:
                sim = SequenceMatcher(None, c_last, s['clean_last']).ratio()
                if sim >= 0.75:
                    unique_pool.append(s)

        best_score = 0.0
        best_rec = None

        for rec in unique_pool:
            s_clean_last = rec['clean_last']
            s_clean_first = rec['clean_first']

            # Compare last name
            last_sim = SequenceMatcher(None, c_last, s_clean_last).ratio()
            if set(c_last_toks) & set(rec['last_toks']):
                last_sim = max(last_sim, 0.92)

            if last_sim < 0.75:
                continue

            # Compare first name and middle name
            first_sim = SequenceMatcher(None, c_first, s_clean_first).ratio()
            mid_sim = max([SequenceMatcher(None, c_first, m).ratio() for m in rec['mid_toks']], default=0.0)
            best_first_sim = max(first_sim, mid_sim * 0.95)

            # Combined weighted score (55% last name, 45% first name)
            composite = (0.55 * last_sim) + (0.45 * best_first_sim)
            if composite > best_score:
                best_score = composite
                best_rec = rec

        if best_score >= _FUZZY_THRESHOLD and best_rec:
            return IBMatchResult(
                personal_code=p_code,
                candidate_name=c_name,
                matched=True,
                ssid=best_rec['ssid'],
                student_id=best_rec['student_id'],
                student_name=best_rec['full_name'],
                match_method='fuzzy_name',
                confidence=round(best_score, 2),
                reason=f"Fuzzy name match (score: {best_score:.2f})",
            )

        return IBMatchResult(
            personal_code=p_code,
            candidate_name=c_name,
            matched=False,
            ssid='',
            student_id='',
            student_name=best_rec['full_name'] if best_rec else '',
            match_method='',
            confidence=round(best_score, 2) if best_rec else 0.0,
            reason=f"No matching LQHS student found (best match score: {best_score:.2f})" if best_rec else "No candidate students found in LQHS enrollment",
        )

    def search_students(self, first_name: str = '', last_name: str = '', query: str = '') -> List[dict]:
        """Search students enrolled at La Quinta HS for manual override autocomplete."""
        self._load_cache()

        results = []
        q_first = _norm(first_name)
        q_last = _norm(last_name)
        q_gen = _norm(query)

        for s in self._students_list:
            fn = _norm(s['first_name'])
            ln = _norm(s['last_name'])
            full = f"{ln} {fn}"

            match = False
            if q_first and q_last:
                match = (q_first in fn or fn in q_first) and (q_last in ln or ln in q_last)
            elif q_last:
                match = q_last in ln
            elif q_first:
                match = q_first in fn
            elif q_gen:
                match = q_gen in full or q_gen in s['ssid'] or q_gen in s['student_id']

            if match:
                results.append({
                    'ssid': s['ssid'],
                    'student_id': s['student_id'],
                    'first_name': s['first_name'],
                    'last_name': s['last_name'],
                    'middle_name': s['middle_name'],
                    'grade': s['grade'],
                    'school_name': 'La Quinta HS',
                    'display': f"{s['last_name']}, {s['first_name']} (SSID: {s['ssid']}, Grade: {s['grade']})",
                })
                if len(results) >= 20:
                    break

        return results

    def search_students_global(
        self,
        first_name: str = '',
        last_name: str = '',
        query: str = '',
        limit: int = 30,
    ) -> List[dict]:
        """
        Global student search across ALL schools and school years in the district
        via StudentEnroll. Used when an IB candidate is not found in the local LQHS
        cohort (e.g. transfers, previous school years, or enrollment anomalies).
        """
        q_first = first_name.strip()
        q_last = last_name.strip()
        q_gen = query.strip()

        # If general query provided, parse it if it looks like "Last, First"
        if q_gen and not (q_first and q_last):
            if ',' in q_gen:
                parts = [p.strip() for p in q_gen.split(',', 1)]
                q_last = parts[0]
                q_first = parts[1]
            else:
                tokens = q_gen.split()
                if len(tokens) >= 2:
                    q_last = tokens[0]
                    q_first = " ".join(tokens[1:])

        q_obj = Q()
        if q_first and q_last:
            q_obj = (
                (Q(last_name__icontains=q_last) | Q(last_name__icontains=q_first)) &
                (Q(first_name__icontains=q_first) | Q(first_name__icontains=q_last) | Q(middle_name__icontains=q_first))
            )
        elif q_last:
            q_obj = Q(last_name__icontains=q_last) | Q(first_name__icontains=q_last)
        elif q_first:
            q_obj = Q(first_name__icontains=q_first) | Q(last_name__icontains=q_first)
        elif q_gen:
            if q_gen.isdigit():
                q_obj = Q(ssid__icontains=q_gen) | Q(student_id__icontains=q_gen)
            else:
                q_obj = (
                    Q(last_name__icontains=q_gen) |
                    Q(first_name__icontains=q_gen) |
                    Q(student_id__icontains=q_gen) |
                    Q(ssid__icontains=q_gen)
                )

        if not q_obj:
            return []

        qs = (
            StudentEnroll.objects.filter(q_obj)
            .exclude(ssid__isnull=True)
            .exclude(ssid='')
            .values(
                'ssid', 'student_id', 'first_name', 'last_name', 'middle_name',
                'grade', 'school_name', 'school_year', 'state_school_code'
            )
            .order_by('-school_year', 'last_name', 'first_name')
        )

        seen_ssids = set()
        results = []

        raw_rows = list(qs[: limit * 3])

        # Priority 1: LQHS records first
        lqhs_rows = [
            r for r in raw_rows
            if (r.get('school_name') or '') in LA_QUINTA_HS_NAMES or r.get('state_school_code') == LA_QUINTA_STATE_CODE
        ]
        other_rows = [r for r in raw_rows if r not in lqhs_rows]

        for r in (lqhs_rows + other_rows):
            ssid = str(r.get('ssid') or '').strip()
            if not ssid or ssid in seen_ssids:
                continue
            seen_ssids.add(ssid)

            ln = r.get('last_name') or ''
            fn = r.get('first_name') or ''
            mn = r.get('middle_name') or ''
            sch = r.get('school_name') or 'Unknown School'
            sy = r.get('school_year') or ''
            grd = r.get('grade') or ''

            disp = f"{ln}, {fn}"
            if mn:
                disp += f" {mn}"
            disp += f" (SSID: {ssid} | {sch} | {sy} | Gr {grd})"

            results.append({
                'ssid': ssid,
                'student_id': r.get('student_id') or '',
                'first_name': fn,
                'last_name': ln,
                'middle_name': mn,
                'grade': grd,
                'school_name': sch,
                'school_year': sy,
                'display': disp,
                'is_lqhs': sch in LA_QUINTA_HS_NAMES or r.get('state_school_code') == LA_QUINTA_STATE_CODE,
            })
            if len(results) >= limit:
                break

        return results

