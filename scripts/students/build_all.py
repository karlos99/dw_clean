#!/usr/bin/env python
"""Build the full Students section: events, Enrollment, English Learners, Behavior.

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
    parser.add_argument('--skip-events', action='store_true',
                        help='Keep the existing weekly event table (do not rebuild).')
    args = parser.parse_args()

    owner_args = ['--owner', args.owner] if args.owner else []

    if not args.skip_events:
        _run([sys.executable, str(HERE / 'build_student_weekly_events.py')])

    skip_args = ['--skip-events']
    _run([sys.executable, str(HERE / 'create_enrollment_dataviz.py'), *owner_args, *skip_args])
    _run([sys.executable, str(HERE / 'create_el_dataviz.py'), *owner_args, *skip_args])
    _run([sys.executable, str(HERE / 'create_behavior_dataviz.py'), *owner_args])
    print('\nStudents section build complete.')


if __name__ == '__main__':
    main()
