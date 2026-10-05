"""
Read-only SEIS (student assessment settings) data integration.

The ``desi_seis`` table is populated outside of Django and stores, per student
(``ssid``) and per test program (``test_group``: caaspp / elpac), the set of
assessment settings already configured in the SEIS system. Each row carries:

* ``assessments``: JSON array naming the DESI assessments the student is
  assigned to (the tests the settings apply to). ``"No_Test"`` placeholders
  and empty arrays mean the row names no specific test and therefore carries
  no per-test settings.
* ``settings``: JSON object mapping a setting label to an option code (or
  ``None`` when the setting is not enabled).

Settings are matched to the DESI configuration by *option code* inside the
program's tag: a setting value such as ``"TDS_TTS_ALL"`` maps to an
``AccommodationOption`` whose ``code`` equals that value and whose
``Accommodation.tag`` matches the SEIS test program.  SEIS selections are
display-only - they are never written to ``StudentAccommodation``.
"""

from django.db import transaction
from django.db import utils as db_utils

from .models import DesiSeis, DesiSeisDisplaySettings

TAG_BY_TEST_GROUP = {
    'caaspp': 'CAASPP',
    'elpac': 'ELPAC',
}

NO_TEST_MARKERS = {'no_test', '', 'none'}


def selected_seis_display_assessment_ids():
    """
    Return the set of Assessment ids the admin selected for SEIS display.

    Legacy hook for the DESI admin "SEIS Display Settings" selection. The
    ``desi_seis`` source table is now authoritative for the workspace, so this
    no longer gates SEIS data; it is kept for the admin dashboard.

    ``None`` means no settings row exists yet; otherwise the set of selected
    ids (possibly empty). Returns ``None`` if the settings table is not
    available (e.g. tests / early migrations).
    """
    try:
        with transaction.atomic():
            settings = DesiSeisDisplaySettings.objects.first()
            if settings is None:
                return None
            return set(settings.assessments.values_list('id', flat=True))
    except (db_utils.ProgrammingError, db_utils.OperationalError, db_utils.DatabaseError):
        return None


def fetch_seis_rows(ssid):
    """
    Return the raw SEIS rows for a student ssid.

    Each row is ``{"test_group", "assessments", "settings"}``. Returns an
    empty list when the ``desi_seis`` table is not available (e.g. tests or
    deployments where the table has not been loaded yet).
    """
    rows = []
    try:
        with transaction.atomic():
            qs = DesiSeis.objects.filter(ssid=ssid)
            rows = [
                {
                    'test_group': row.test_group,
                    'assessments': list(row.assessments or []),
                    'settings': dict(row.settings or {}),
                }
                for row in qs
            ]
    except (db_utils.ProgrammingError, db_utils.OperationalError, db_utils.DatabaseError):
        rows = []
    return rows


def _tag_for(row):
    group = (row.get('test_group') or '').strip().lower()
    return TAG_BY_TEST_GROUP.get(group)


def _row_assessment_names(row):
    """Lower-cased DESI assessment names this SEIS row applies to.

    An empty result means the row names no specific test (``No_Test`` or an
    empty list). Such a row carries no per-test SEIS settings.
    """
    names = set()
    for name in row.get('assessments') or []:
        clean = str(name).strip().lower()
        if clean not in NO_TEST_MARKERS:
            names.add(clean)
    return names


def _assigned_names_from_rows(rows):
    """Union of the specific assessment names named across ``rows``."""
    names = set()
    for row in rows:
        names |= _row_assessment_names(row)
    return names


def seis_assigned_assessments(ssid, assessments):
    """
    Assessment objects the SEIS source assigns to a student.

    The ``assessments`` JSON on each ``desi_seis`` row is the authoritative
    list of tests the student is taking. This matches every named test to an
    existing ``Assessment`` by name (case-insensitively), across all of the
    student's rows, **including inactive assessments**. ``No_Test``/empty
    entries are ignored.

    Returns the matching ``Assessment`` objects in the incoming ``assessments``
    order. An empty list means the student has no SEIS test assignment.
    """
    names = _assigned_names_from_rows(fetch_seis_rows(ssid))
    if not names:
        return []
    return [
        ast for ast in assessments
        if (ast.name or '').strip().lower() in names
    ]


def seis_assigned_assessments_by_ssid(ssids, assessments):
    """
    Bulk variant of :func:`seis_assigned_assessments` for the student list.

    Returns ``{ssid: [Assessment, ...]}`` for every ssid that has a SEIS test
    assignment, using a single query for the whole page. A missing ``desi_seis``
    table yields an empty mapping.
    """
    unique = [s for s in dict.fromkeys(ssids or []) if s]
    if not unique or not assessments:
        return {}

    names_by_ssid = {}
    try:
        with transaction.atomic():
            for row in DesiSeis.objects.filter(ssid__in=unique):
                row_names = _row_assessment_names(
                    {'assessments': list(row.assessments or [])}
                )
                if row_names:
                    names_by_ssid.setdefault(row.ssid, set()).update(row_names)
    except (db_utils.ProgrammingError, db_utils.OperationalError, db_utils.DatabaseError):
        return {}

    result = {}
    for ssid, names in names_by_ssid.items():
        matched = [
            ast for ast in assessments
            if (ast.name or '').strip().lower() in names
        ]
        if matched:
            result[ssid] = matched
    return result


def build_seis_option_map(ssid, assessments):
    """
    Map SEIS settings onto the DESI configuration for one student.

    ``assessments`` is the list of ``Assessment`` objects that will actually be
    rendered on the workspace (the SEIS-assigned tests).  Returns a dict keyed
    by ``(assessment_id, accommodation_id)`` whose values are the
    ``AccommodationOption`` the SEIS system has selected for that
    assessment/support, and a set of locked accommodation ids.

    Matching is by option code within the program tag.  An option only locks a
    given assessment when that assessment both (a) is named in the SEIS row's
    ``assessments`` list and (b) supports the option. Rows that name no
    specific test (``No_Test``/empty) carry no per-test settings.
    """
    rows = fetch_seis_rows(ssid)
    if not rows or not assessments:
        return {}, set()

    from .models import AccommodationOption

    result = {}
    seen_cache = {}

    for row in rows:
        tag = _tag_for(row)
        if not tag:
            continue
        row_names = _row_assessment_names(row)
        if not row_names:
            # "No_Test"/empty rows name no specific test, so they carry no
            # per-test settings (they must not apply to every assessment).
            continue
        candidate_asts = [
            ast for ast in assessments
            if (ast.name or '').strip().lower() in row_names
        ]
        candidate_ids = {ast.id for ast in candidate_asts}
        if not candidate_ids:
            continue

        for code in row.get('settings', {}).values():
            if not code:
                continue
            cache_key = (tag, code)
            if cache_key not in seen_cache:
                seen_cache[cache_key] = list(
                    AccommodationOption.objects
                    .filter(code=code, accommodation__tag=tag)
                    .prefetch_related('assessments', 'accommodation')
                )
            for opt in seen_cache[cache_key]:
                allowed_ast_ids = {
                    a.id for a in opt.assessments.all()
                }.intersection(candidate_ids)
                for ast_id in allowed_ast_ids:
                    result[(ast_id, opt.accommodation_id)] = opt

    locked_accommodation_ids = {acc_id for _ast_id, acc_id in result}
    return result, locked_accommodation_ids


def _filtered_seis_option_map(student):
    """SEIS options for a student, driven by the SEIS source table only.

    The SEIS source is authoritative: a test shows SEIS settings only when it
    is assigned to the student in the ``desi_seis`` table. The admin SEIS
    display selection and demographic criteria no longer gate this.
    """
    ssid = getattr(student, 'ssid', None)
    if not ssid:
        return {}

    from .models import Assessment

    all_assessments = list(Assessment.objects.prefetch_related('criteria'))
    if not all_assessments:
        return {}

    assigned = seis_assigned_assessments(ssid, all_assessments)
    if not assigned:
        return {}

    # SEIS names the tests, but they must still satisfy the same grade rules
    # used for non-SEIS test assignments.
    from .views import filter_assessments_by_grade
    assigned = filter_assessments_by_grade(assigned, all_assessments, student)
    if not assigned:
        return {}

    raw_map, _locked = build_seis_option_map(ssid, assigned)
    return raw_map


def seis_support_detail_for_student(student):
    """
    SEIS supports for one student: labels plus option/accommodation ids and
    program tags for filtering (same rules as the student workspace).
    """
    filtered = _filtered_seis_option_map(student)
    labels = []
    labels_by_tag = {}
    label_subjects = {}
    option_ids = set()
    accommodation_ids = set()
    tags = set()
    seen = set()

    from .models import Assessment
    subject_by_assessment = dict(
        Assessment.objects.filter(id__in={ast_id for ast_id, _acc_id in filtered})
        .values_list('id', 'subject')
    )

    for (ast_id, acc_id), opt in filtered.items():
        option_ids.add(opt.id)
        accommodation_ids.add(acc_id)
        accommodation = opt.accommodation
        tag = (getattr(accommodation, 'tag', '') or '').strip().upper()
        if tag:
            tags.add(tag)
        prefix = f"{accommodation.name}: " if accommodation else ''
        label = f"{prefix}{opt.name}"
        subject = (subject_by_assessment.get(ast_id) or '').strip()
        if subject:
            label_subjects.setdefault(label, set()).add(subject)
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if tag:
            labels_by_tag.setdefault(tag, []).append(label)
    return {
        'labels': sorted(labels, key=str.casefold),
        'labels_by_tag': {
            tag: sorted(vals, key=str.casefold)
            for tag, vals in labels_by_tag.items()
        },
        'label_subjects': {
            label: sorted(subs) for label, subs in label_subjects.items()
        },
        'option_ids': option_ids,
        'accommodation_ids': accommodation_ids,
        'tags': tags,
    }


def seis_support_labels_for_student(student):
    """Human-readable SEIS supports for one student."""
    return seis_support_detail_for_student(student)['labels']
