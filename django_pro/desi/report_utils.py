"""
Aggregate DESI completion report for a school, used by the Testing Coordinator
page. Completion is recorded per assessment (StudentAssessmentCompletion); the
report shows, per assessment, how many eligible students are completed/pending.
"""


def build_school_report(school_state_id, school_year):
    """
    Return per-assessment completion summary and per-student status rows for a
    school/year.

    students: list of dicts {student, statuses: [{assessment, eligible, completed}]}
    pending_by_assessment: {assessment_id: [student, ...]} (eligible, not completed)
    """
    from desi.models import Assessment, StudentAssessmentCompletion
    from desi.views import check_criteria
    from studata.models import CurrentStudent

    students = list(
        CurrentStudent.objects
        .filter(school_year=school_year, school_state_id=school_state_id)
        .order_by('student_name', 'stu_annual_key')
    )
    assessments = list(Assessment.objects.filter(is_active=True).order_by('name'))

    student_keys = [s.stu_annual_key for s in students]
    completed_pairs = set()
    if student_keys and assessments:
        completed_pairs = {
            (rec.student_annual_key, rec.assessment_id)
            for rec in StudentAssessmentCompletion.objects.filter(
                student_annual_key__in=student_keys,
                assessment__in=assessments,
                is_completed=True,
            )
        }

    assessment_stats = {
        a.id: {'assessment': a, 'eligible': 0, 'completed': 0}
        for a in assessments
    }
    pending_by_assessment = {a.id: [] for a in assessments}

    student_statuses = []
    for student in students:
        statuses = []
        for assessment in assessments:
            eligible, _failed = check_criteria(assessment.criteria.all(), student)
            is_completed = (student.stu_annual_key, assessment.id) in completed_pairs
            statuses.append({
                'assessment': assessment,
                'eligible': eligible,
                'completed': is_completed,
            })
            if eligible:
                assessment_stats[assessment.id]['eligible'] += 1
                if is_completed:
                    assessment_stats[assessment.id]['completed'] += 1
                else:
                    pending_by_assessment[assessment.id].append(student)
        student_statuses.append({'student': student, 'statuses': statuses})

    summary = []
    for stat in assessment_stats.values():
        assessment = stat['assessment']
        eligible = stat['eligible']
        completed = stat['completed']
        summary.append({
            'assessment': assessment,
            'eligible': eligible,
            'completed': completed,
            'pending': eligible - completed,
            'percent': round((completed / eligible) * 100, 1) if eligible else None,
        })

    return {
        'school_state_id': school_state_id,
        'school_year': school_year,
        'assessments': assessments,
        'summary': summary,
        'students': student_statuses,
        'pending_by_assessment': pending_by_assessment,
        'total_students': len(students),
    }


def build_report_rows(report):
    """
    CSV-friendly per-student rows. Columns: SSID, Student, Grade, then one
    column per assessment with Completed / Pending / '' (not eligible).
    """
    rows = []
    assessments = report['assessments']
    for entry in report['students']:
        student = entry['student']
        status_by_id = {s['assessment'].id: s for s in entry['statuses']}
        row = {
            'ssid': student.ssid or '',
            'student_name': student.student_name or '',
            'grade': student.grade or '',
        }
        for assessment in assessments:
            status = status_by_id[assessment.id]
            if not status['eligible']:
                row[assessment.id] = ''
            else:
                row[assessment.id] = 'Completed' if status['completed'] else 'Pending'
        rows.append(row)
    return rows


def list_school_teachers(school_state_id, school_year):
    """Teacher names (from the teacher access view) with students in the school/year."""
    from studata.models import CurrentStudent, TeacherAccess

    student_keys = CurrentStudent.objects.filter(
        school_year=school_year,
        school_state_id=school_state_id,
    ).values_list('stu_annual_key', flat=True)

    names = (
        TeacherAccess.objects.filter(school_year=school_year, stu_annual_key__in=student_keys)
        .values_list('staff_name', flat=True)
        .distinct()
    )
    cleaned = sorted({name.strip() for name in names if name and name.strip()}, key=str.casefold)
    return cleaned


def build_entered_supports_report(school_state_id, school_year, teacher=None):
    """
    What users entered: per student row with assigned supports (option names
    grouped by accommodation), direct supports, completion names, and the last
    user who updated the record. Optionally scoped to a teacher's roster.
    """
    from desi.models import StudentAccommodation, StudentAssessmentCompletion
    from studata.models import CurrentStudent, TeacherAccess

    students_qs = CurrentStudent.objects.filter(
        school_year=school_year,
        school_state_id=school_state_id,
    ).order_by('student_name', 'stu_annual_key')

    if teacher:
        roster_keys = set(
            TeacherAccess.objects.filter(
                school_year=school_year,
                staff_name__iexact=teacher,
                stu_annual_key__in=students_qs.values('stu_annual_key'),
            ).values_list('stu_annual_key', flat=True)
        )
        if not roster_keys:
            return {'students': [], 'rows': [], 'total': 0}
        students_qs = students_qs.filter(stu_annual_key__in=roster_keys)

    students = list(students_qs)
    keys = [s.stu_annual_key for s in students]

    records = {
        rec.student_annual_key: rec
        for rec in StudentAccommodation.objects.filter(student_annual_key__in=keys)
        .select_related('updated_by')
        .prefetch_related('options__accommodation', 'selected_accommodations')
    }

    completed_map = {}
    if keys:
        for key, assessment_name in (
            StudentAssessmentCompletion.objects.filter(
                student_annual_key__in=keys,
                is_completed=True,
            ).values_list('student_annual_key', 'assessment__name')
        ):
            completed_map.setdefault(key, []).append(assessment_name)

    rows = []
    for student in students:
        record = records.get(student.stu_annual_key)
        supports = []
        if record:
            for option in record.options.all():
                prefix = f"{option.accommodation.name}: " if option.accommodation else ''
                supports.append(f"{prefix}{option.name}")
            supports += [acc.name for acc in record.selected_accommodations.all()]

        completions = sorted(completed_map.get(student.stu_annual_key, []))
        updater = record.updated_by if record else None
        rows.append({
            'ssid': student.ssid or '',
            'student_name': student.student_name or '',
            'grade': student.grade or '',
            'supports': '; '.join(supports),
            'completions': ', '.join(completions),
            'updated_by': (updater.name or updater.email) if updater else '',
            'updated_at': record.updated_at.strftime('%Y-%m-%d %H:%M') if record else '',
        })

    return {'students': students, 'rows': rows, 'total': len(rows)}
