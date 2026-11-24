#!/usr/bin/env python
"""Build the full Students section: ClickHouse enrollment analytics, pages.

Run inside the Django container:

    docker compose exec django python /app/scripts/students/build_all.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _run(args: list[str]) -> None:
    print('\n$ ' + ' '.join(args))
    result = subprocess.run(args)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description='Build all Students section pages.')
    parser.add_argument('--owner', default='', help='Owner username or email.')
    parser.add_argument(
        '--skip-sync',
        action='store_true',
        help='Skip enrollment analytics rebuild (sync_to_clickhouse).',
    )
    args = parser.parse_args()

    owner_args = ['--owner', args.owner] if args.owner else []

    from _common import bootstrap_django

    project_dir = bootstrap_django()
    manage = Path(project_dir) / 'manage.py'

    if not args.skip_sync:
        _run([
            sys.executable,
            str(manage),
            'sync_to_clickhouse',
            '--tables',
            'analytics_student_enrollment_events,analytics_student_enrollment_weekly',
        ])

    _run([sys.executable, str(HERE / 'create_enrollment_dataviz.py'), *owner_args])
    _run([sys.executable, str(HERE / 'create_el_dataviz.py'), *owner_args])
    _run([sys.executable, str(HERE / 'create_behavior_dataviz.py'), *owner_args])
    print('\nStudents section build complete.')


if __name__ == '__main__':
    main()