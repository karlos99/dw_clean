"""
Designated support configuration importer.

Reads a spreadsheet (CSV) of the CAASPP "Designated Supports" documentation
(see ref/CAASPP_Upload.csv for the reference layout) and rebuilds the DESI
configuration from it. Files may also come from other assessment programs
(e.g. ref/ELPAC_Upload_Stu.csv) using the same layout but naming the assessment
list column "Assessment" instead of "Program".

Column layout expected (header row names):
    Column           -> Upload column letter that groups rows into a single
                        designated support (e.g. "B", "AB").
    Display Name     -> Human-readable support name; the accommodation display
                        name is everything before the first "(".
    Value            -> Option reporting code (e.g. "NEA_Abacus"). Rows whose
                        value is "Blank (default)" represent the "not selected"
                        state and are skipped.
    Value Definition -> Display text for the option (e.g. "Abacus is selected.").
    Program / Assessment -> Bullet ("•") separated list of assessments this
                        option applies to. Assessments are created from this
                        list (a bullet is used as the separator).
    Content Area     -> Optional; redundant with Program/Assessment so it is
                        not used.
    Setting Definition -> Description of the support.
    Type             -> Category for the accommodation
                        (e.g. "non-embedded designated support").

Import behaviour is intentionally *destructive by default* (clear & rebuild)
so the CSV file is the single source of truth for the configuration. Pass
``clear=False`` to update existing rows only.

The loader is generic: point it at any CSV with this layout to build a
different set of assessments (e.g. future files in the ref/ folder).
"""

import csv
import re
from collections import OrderedDict

from .models import Accommodation, AccommodationOption, Assessment

BLANK_VALUE = "Blank (default)"
NOT_APPLICABLE = "NA"
BULLET_RE = re.compile(r"[•*]")

REQUIRED_HEADERS = {
    "Column",
    "Display Name",
    "Value",
    "Value Definition",
    "Setting Definition",
    "Type",
}

# The assessment list column may be named "Program" (CAASPP) or "Assessment".
PROGRAM_HEADER_CANDIDATES = ("program", "assessment", "assessments")

_SUBJECT_BY_KEYWORD = [
    (("ela",), "ELA"),
    (("math",), "Math"),
    (("science",), "Science"),
    (("cast",), "Science"),
    (("csa",), "Science"),
]


def derive_subject(program_name):
    """Best-effort subject derivation from a program name (cosmetic only)."""
    lowered = (program_name or "").lower()
    for keywords, subject in _SUBJECT_BY_KEYWORD:
        if any(keyword in lowered for keyword in keywords):
            return subject
    return ""


def split_programs(value):
    """Split a Program/Assessment cell on the bullet indicator and return clean names."""
    names = []
    for token in BULLET_RE.split(value or ""):
        token = token.strip()
        if token:
            names.append(token)
    return names


def normalize_program_name(name):
    """Return a canonical (first-seen, case-insensitive) program identity."""
    return name.strip()


def display_name_from_cell(raw_display_name):
    """Return the support name: everything before the first '(' (and the '*' marker)."""
    text = (raw_display_name or "").split("\n")[0]
    idx = text.find("(")
    if idx != -1:
        text = text[:idx]
    return text.replace("*", "").strip()


def clean_setting_definition(raw):
    return (raw or "").strip()


def program_cell(row):
    """
    Return the assessment-list cell for a CSV row, tolerating the column being
    named "Program" (CAASPP) or "Assessment" (e.g. ELPAC files).
    """
    for key in row:
        if key and key.strip().lower() in PROGRAM_HEADER_CANDIDATES:
            return row[key] or ""
    return ""


def _read_rows(fileobj):
    reader = csv.DictReader(fileobj)
    headers = {h.strip() for h in (reader.fieldnames or [])}
    missing = REQUIRED_HEADERS - headers
    if missing:
        raise ValueError(
            "CSV is missing required column(s): {}. Expected headers: {}".format(
                ", ".join(sorted(missing)),
                ", ".join(sorted(REQUIRED_HEADERS)),
            )
        )
    return list(reader)


def parse_rows(rows):
    """
    Parse raw CSV dict rows into an ordered structure.

    Returns:
        assessments: OrderedDict {display name: {"name", "subject"}} in first
                     encountered order (deduplicated case-insensitively).
        supports:    list of dicts with one entry per Column letter group:
                     {column_letter, name, code, category, description, options}
    """
    assessments = OrderedDict()
    supports = OrderedDict()

    def get_assessment(program_name):
        normalized = normalize_program_name(program_name)
        key = normalized.casefold()
        if key not in assessments:
            assessments[key] = {
                "name": normalized,
                "subject": derive_subject(normalized),
            }
        return assessments[key]["name"]

    for row in rows:
        column_letter = (row.get("Column") or "").strip()
        row_type = (row.get("Type") or "").strip()
        value = (row.get("Value") or "").strip()
        if not column_letter or row_type == NOT_APPLICABLE:
            continue

        display_name = display_name_from_cell(row.get("Display Name"))
        if not display_name:
            continue

        setting_definition = clean_setting_definition(row.get("Setting Definition"))
        programs = split_programs(program_cell(row))
        program_names = [p for p in programs if p.casefold() != "all"]
        applicable_assessments = [get_assessment(name) for name in program_names]

        support = supports.setdefault(
            column_letter,
            {
                "column_letter": column_letter,
                "name": display_name,
                "code": column_letter,
                "category": row_type,
                "description_parts": [],
                "options": [],
            },
        )
        if support["name"] != display_name:
            # A column letter should only represent one support; if the CSV
            # groups multiple display names under one letter keep the first
            # but surface the discrepancy through the description.
            support["description_parts"].append(
                f'[Also listed as "{display_name}"]'
            )
        if setting_definition and setting_definition not in support["description_parts"]:
            support["description_parts"].append(setting_definition)

        # Keep the first occurrence of each "value" definition under a letter.
        if value == BLANK_VALUE:
            continue

        option_name = (row.get("Value Definition") or "").strip()
        if not option_name:
            option_name = value

        if any(opt["code"] == value for opt in support["options"]):
            continue

        support["options"].append(
            {
                "code": value,
                "name": option_name,
                "assessments": applicable_assessments,
            }
        )

    return {
        "assessments": [
            {"name": info["name"], "subject": info["subject"]}
            for info in assessments.values()
        ],
        "supports": [
            {
                "column_letter": s["column_letter"],
                "name": s["name"],
                "code": s["code"],
                "category": s["category"],
                "description": "\n\n".join(
                    dict.fromkeys(part for part in s["description_parts"] if part)
                ),
                "options": s["options"],
            }
            for s in supports.values()
        ],
    }


def clear_configuration():
    """Delete the current DESI configuration (accommodations + assessments)."""
    Accommodation.objects.all().delete()
    Assessment.objects.all().delete()


def import_configuration(parsed, tag="", clear=False, active=False):
    """
    Persist parsed CSV configuration into the DESI models.

    Imports are *additive*: existing assessments and designated supports are
    kept (records are reused/merged), so several files (CAASPP, ELPAC, ...) can
    be imported into the same configuration. Pass ``clear=True`` to wipe the
    whole DESI configuration before importing.

    Every imported assessment and support is stamped with ``tag`` (e.g.
    "CAASPP", "ELPAC") so its source file is visible in the lists.

    Assessments are imported as inactive by default so the district can enable
    the tests that apply; pass ``active=True`` to activate them on import.
    """
    tag = (tag or "").strip()
    if clear:
        clear_configuration()

    assessment_map = {}
    for item in parsed["assessments"]:
        assessment, created = Assessment.objects.get_or_create(
            name=item["name"],
            subject=item["subject"],
            defaults={"is_active": active, "tag": tag},
        )
        if created and assessment.is_active != active:
            assessment.is_active = active
            assessment.save(update_fields=["is_active"])
        assessment_map[(item["name"].casefold(), item["subject"].casefold())] = assessment

    total_options = 0
    for order, support in enumerate(parsed["supports"], start=1):
        accommodation, created = Accommodation.objects.get_or_create(
            column_letter=support["column_letter"],
            tag=tag,
            defaults={
                "name": support["name"],
                "code": support["code"],
                "category": support["category"],
                "description": support["description"],
                "order": order,
            },
        )
        if not created:
            accommodation.name = support["name"]
            accommodation.code = support["code"]
            accommodation.category = support["category"]
            accommodation.description = support["description"]
            accommodation.order = order
            accommodation.save()

        for option_data in support["options"]:
            option, _ = AccommodationOption.objects.get_or_create(
                accommodation=accommodation,
                name=option_data["name"],
                defaults={"code": option_data["code"]},
            )
            if option.code != option_data["code"]:
                option.code = option_data["code"]
                option.save(update_fields=["code"])

            assessments = [
                assessment_map[(name.casefold(), derive_subject(name).casefold())]
                for name in option_data["assessments"]
                if (name.casefold(), derive_subject(name).casefold()) in assessment_map
            ]
            for assessment in assessments:
                if not assessment.supported_options.filter(id=option.id).exists():
                    assessment.supported_options.add(option)
            total_options += 1

    return {
        "assessments": len(parsed["assessments"]),
        "supports": len(parsed["supports"]),
        "options": total_options,
    }


def import_csv(fileobj, tag="", clear=False, active=False):
    """Parse a CSV file object and import the configuration from it."""
    parsed = parse_rows(_read_rows(fileobj))
    return parsed, import_configuration(parsed, tag=tag, clear=clear, active=active)
