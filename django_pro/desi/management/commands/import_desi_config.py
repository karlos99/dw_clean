"""
Import a designated-support configuration CSV (CAASPP / ELPAC style) into DESI.

Imports are ADDITIVE: current assessments and designated supports are kept and
the rows are merged/updated, so several files can live side by side. Every
import is stamped with a tag (--tag, default: CSV filename) which is shown in
the assessments and designated-supports lists.

The reference file layout lives at ref/ (repo root). Pass any CSV with the
supported columns to import a different set of assessments/supports.

Assessments are imported as INACTIVE by default so they can be enabled per
district need; pass --active to activate every imported assessment.

Usage:
    python manage.py import_desi_config --csv ref/ELPAC_Upload_Stu.csv --tag ELPAC
    python manage.py import_desi_config --csv ref/CAASPP_Upload.csv --tag CAASPP --active
    python manage.py import_desi_config path/to/file.csv
    python manage.py import_desi_config --csv some.csv --clear --active
"""

import csv
import os
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from desi import importers
from desi.importers import parse_rows

DEFAULT_CSV = "CAASPP_Upload.csv"
ENV_VAR = "DESI_CSV"


class Command(BaseCommand):
    help = (
        "Import DESI assessments/designated supports from a CAASPP/ELPAC CSV "
        "layout (additive by default). Reads ref/CAASPP_Upload.csv if no file "
        "is given. Each import is tagged (--tag, default: the CSV filename) "
        "and assessments are imported inactive unless --active is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default=None,
            help="Path to the CSV file (alternative to --csv).",
        )
        parser.add_argument(
            "--csv",
            dest="csv_option",
            default=None,
            help=(
                "CSV file to import. Accepts a path or a bare filename which is "
                "also searched for inside the ref/ folder. Takes precedence over "
                "the positional argument and the DESI_CSV environment variable."
            ),
        )
        parser.add_argument(
            "--tag",
            default=None,
            help=(
                "Source tag stamped on every imported assessment and support "
                "(shown in the lists). Defaults to the CSV file name."
            ),
        )
        parser.add_argument(
            "--active",
            action="store_true",
            help="Import assessments as active (default is inactive).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "Delete the existing DESI configuration before importing "
                "(default is to keep/merge existing records)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse the file and report what would be created without touching the DB.",
        )

    def _ref_roots(self):
        """Directories that may hold a ref/ folder with the reference CSVs."""
        base = settings.BASE_DIR
        return (base, Path(base).parent, Path(base).parent.parent)

    def _resolve_path(self, raw):
        """Resolve user input (path or bare filename) to an existing CSV file."""
        if not raw:
            raw = os.environ.get(ENV_VAR, "").strip()

        if not raw:
            raw = DEFAULT_CSV

        candidate = Path(raw)

        # A bare filename (no directory part) is looked up inside every ref/ folder.
        if candidate.name == raw and candidate.parent == Path("."):
            for root in self._ref_roots():
                ref_file = root / "ref" / raw
                if ref_file.exists():
                    return ref_file
        elif candidate.exists():
            return candidate
        elif candidate.is_absolute():
            raise CommandError(
                f"CSV file not found: '{raw}'. Pass a valid path or --csv <file>."
            )

        # Relative path (e.g. ref/MyFile.csv) - the ref/ folder is bind-mounted
        # into the container, so also resolve it against each ref root.
        parts = candidate.parts
        if parts and parts[0] == "ref":
            parts = parts[1:]
        elif not parts:
            parts = (DEFAULT_CSV,)
        for root in self._ref_roots():
            ref_file = root / "ref" / Path(*parts)
            if ref_file.exists():
                return ref_file

        raise CommandError(
            f"CSV file not found: '{raw}'. Pass a valid path or --csv <file>."
        )

    def handle(self, *args, **options):
        raw = options.get("csv_option") or options.get("csv_path")
        path = self._resolve_path(raw)
        dry_run = options.get("dry_run")
        clear = bool(options.get("clear", False))
        active = bool(options.get("active", False))
        tag = (options.get("tag") or "").strip() or path.stem

        self.stdout.write(f"Reading {path} (tag: {tag})")

        with path.open("r", encoding="utf-8-sig", newline="") as fileobj:
            content = fileobj.read()
            parsed = parse_rows(csv.DictReader(StringIO(content)))

        self.stdout.write(
            f"Parsed {len(parsed['assessments'])} assessment(s), "
            f"{len(parsed['supports'])} designated support(s), "
            f"{sum(len(s['options']) for s in parsed['supports'])} option(s)."
        )
        self.stdout.write("Assessments: " + ", ".join(a["name"] for a in parsed["assessments"]))
        self.stdout.write("Designated supports: " + ", ".join(s["name"] for s in parsed["supports"]))

        if dry_run:
            self.stdout.write("Dry run complete - no changes made.")
            return

        self.stdout.write("Deleting existing DESI configuration..." if clear else "Merging into existing configuration...")

        summary = importers.import_configuration(parsed, tag=tag, clear=clear, active=active)
        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete (tag '{tag}'): {summary['assessments']} assessments, "
                f"{summary['supports']} designated supports, "
                f"{summary['options']} options."
            )
        )
