"""
State (TOMS) upload export for DESI.

Builds the CAASPP and ELPAC student-accommodation upload workbooks using the
exact TOMS template headers. Each worksheet has an SSID column plus one column
per designated support (by its upload column letter); a student's cell contains
the option's reporting code.

Sources merged per student:
* DESI-entered options (``StudentAccommodation``) for every support tagged
  CAASPP/ELPAC, regardless of whether the assessment is active.
* SEIS settings (``desi_seis``) for the matching program, matched by option code
  (SEIS wins when both provide a code for the same support).
"""

import io
import zipfile

from django.db import transaction
from openpyxl import Workbook

from .models import Accommodation, AccommodationOption, DesiSeis, StudentAccommodation
from .state_upload_templates import (
    CAASPP_HEADERS,
    CAASPP_SHEET_NAME,
    ELPAC_HEADERS,
    ELPAC_SHEET_NAME,
)

PROGRAM_TAGS = ('CAASPP', 'ELPAC')
TAG_TO_TEST_GROUP = {'CAASPP': 'caaspp', 'ELPAC': 'elpac'}


def _excel_letters(count):
    """Return the first ``count`` Excel column letters: A, B, …, Z, AA, …"""
    letters = []
    for i in range(1, count + 1):
        text = ''
        n = i
        while n:
            n, rem = divmod(n - 1, 26)
            text = chr(65 + rem) + text
        letters.append(text)
    return letters


def _letter_index(letter):
    """0-based index for an Excel column letter ('A'->0, 'AA'->26)."""
    letter = (letter or '').strip().upper()
    if not letter:
        return None
    value = 0
    for ch in letter:
        if not ch.isalpha():
            return None
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def _supports_for_tag(tag):
    """All supports tagged ``tag`` ordered by their upload column letter."""
    supports = list(Accommodation.objects.filter(tag=tag))
    supports.sort(key=lambda s: (_letter_index(s.column_letter) is None,
                                 _letter_index(s.column_letter) or 0,
                                 s.name.casefold()))
    return supports


def _student_codes_for_tag(tag, students):
    """
    Map each student to ``{accommodation_id: option_code}`` for a program tag.

    DESI options are read in bulk; SEIS codes are matched by option code within
    the program tag and take precedence over DESI.
    """
    keys = [s.stu_annual_key for s in students]
    ssids = [s.ssid for s in students if s.ssid]

    desi_by_key = {}
    if keys:
        records = (
            StudentAccommodation.objects.filter(student_annual_key__in=keys)
            .prefetch_related('options__accommodation')
        )
        for record in records:
            bucket = desi_by_key.setdefault(record.student_annual_key, {})
            for option in record.options.all():
                accommodation = option.accommodation
                if accommodation and accommodation.tag == tag and option.code:
                    bucket[accommodation.id] = option.code

    seis_codes_by_ssid = {}
    group = TAG_TO_TEST_GROUP.get(tag)
    if group and ssids:
        try:
            with transaction.atomic():
                for ssid, settings in (
                    DesiSeis.objects.filter(ssid__in=ssids, test_group=group)
                    .values_list('ssid', 'settings')
                ):
                    codes = {
                        str(value).strip()
                        for value in (settings or {}).values()
                        if value and str(value).strip()
                    }
                    if codes:
                        seis_codes_by_ssid.setdefault(ssid, set()).update(codes)
        except Exception:
            seis_codes_by_ssid = {}

    option_by_code = {}
    all_codes = set()
    for codes in seis_codes_by_ssid.values():
        all_codes.update(codes)
    if all_codes:
        for option in (
            AccommodationOption.objects
            .filter(code__in=all_codes, accommodation__tag=tag)
            .select_related('accommodation')
        ):
            option_by_code.setdefault(option.code, option)

    results = []
    for student in students:
        codes = dict(desi_by_key.get(student.stu_annual_key, {}))
        if student.ssid and seis_codes_by_ssid.get(student.ssid):
            for code in seis_codes_by_ssid[student.ssid]:
                option = option_by_code.get(code)
                if option and option.accommodation_id:
                    codes[option.accommodation_id] = code
        results.append((student, codes))
    return results


def build_state_upload_workbook(tag, students):
    """Return an openpyxl Workbook for one program tag and the given students."""
    if tag == 'CAASPP':
        headers, sheet_name = CAASPP_HEADERS, CAASPP_SHEET_NAME
    elif tag == 'ELPAC':
        headers, sheet_name = ELPAC_HEADERS, ELPAC_SHEET_NAME
    else:
        raise ValueError(f"Unknown program tag: {tag}")

    letters = _excel_letters(len(headers))
    letter_to_index = {letter: idx for idx, letter in enumerate(letters)}

    column_by_index = {}
    extra_supports = []
    for support in _supports_for_tag(tag):
        idx = letter_to_index.get((support.column_letter or '').strip().upper())
        if idx is not None:
            column_by_index[idx] = support
        else:
            extra_supports.append(support)

    all_headers = list(headers) + [
        f"{s.name} ({s.column_letter})" if s.column_letter else s.name
        for s in extra_supports
    ]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(all_headers)

    for student, codes in _student_codes_for_tag(tag, students):
        row = [''] * len(all_headers)
        row[0] = student.ssid or ''
        for idx, support in column_by_index.items():
            row[idx] = codes.get(support.id, '')
        for offset, support in enumerate(extra_supports):
            row[len(headers) + offset] = codes.get(support.id, '')
        sheet.append(row)

    return workbook


def _workbook_bytes(workbook):
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_state_upload_zip(school_year, students, programs=PROGRAM_TAGS):
    """Return a ZIP file (bytes) containing one workbook per program tag."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for tag in programs:
            data = _workbook_bytes(build_state_upload_workbook(tag, students))
            archive.writestr(f'{tag}_Upload_{school_year}.xlsx', data)
    return buffer.getvalue()
