"""
Read-only SEIS (student assessment settings) data integration.

The ``desi_seis`` table is populated outside of Django and stores, per student
(``ssid``) and per test program (``test_group``: caaspp / elpac), the set of
assessment settings already configured in the SEIS system. Each row carries:

* ``assessments``: JSON array of DESI assessment names the settings apply to
  (``"No_Test"`` placeholders and empty arrays mean "no specific DESI test").
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

    ``None`` means no explicit selection has been saved yet - the default is to
    show SEIS settings wherever the source data references an assessment.  An
    empty set means the admin deliberately selected no assessments (SEIS
    settings are hidden).  Returns ``None`` if the settings table is not
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
    """Lower-cased DESI assessment names this SEIS row applies to (empty = all)."""
    names = set()
    for name in row.get('assessments') or []:
        clean = str(name).strip().lower()
        if clean not in NO_TEST_MARKERS:
            names.add(clean)
    return names


def build_seis_option_map(ssid, assessments):
    """
    Map SEIS settings onto the DESI configuration for one student.

    ``assessments`` is the list of ``Assessment`` objects that will actually be
    rendered on the workspace (active + eligible).  Returns a dict keyed by
    ``(assessment_id, accommodation_id)`` whose values are the
    ``AccommodationOption`` the SEIS system has selected for that
    assessment/support, and a set of locked accommodation ids.

    Matching is by option code within the program tag.  An option only locks a
    given assessment when that assessment both (a) appears in the SEIS row's
    ``assessments`` list (or the list is empty) and (b) supports the option.
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
        candidate_asts = [
            ast for ast in assessments
            if (not row_names or ast.name.strip().lower() in row_names)
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
