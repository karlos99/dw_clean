"""
ClickHouse DDL statements mirroring PostgreSQL analytics tables.

All tables use MergeTree engine with appropriate ORDER BY keys
optimized for the analytics query patterns used in the project.
"""


def get_all_ddl():
    return [
        CREATE_CURRENT_STUDENT,
        CREATE_STUDENT_ENROLL,
        CREATE_RESULTS,
        CREATE_TESTS,
        CREATE_RESULTS_WITH_TESTS,
        CREATE_STUDENT_MARKS_LIST,
        CREATE_NEXTYEARLIST_PLACEMENT,
        CREATE_SCHOOL,
        CREATE_SCHEDULE_LIST,
        CREATE_RELATIVE_YEAR,
        CREATE_MATHR_RECOMMENDATION,
        CREATE_STUDENT_ATTENDANCE,
        CREATE_STUDENT_INCIDENTS,
        CREATE_ANALYTICS_STUDENT_PROFILE,
        CREATE_ANALYTICS_STUDENT_RESULTS,
        CREATE_ANALYTICS_STUDENT_MARKS,
        CREATE_ANALYTICS_AP_RESULTS,
        CREATE_ANALYTICS_AP_AWARDS,
        CREATE_ANALYTICS_SAT_RESULTS,
        CREATE_ANALYTICS_SURVEY_RESPONSES,
        CREATE_ANALYTICS_TEST_METADATA,
        CREATE_ANALYTICS_MARKS_METADATA,
        CREATE_ANALYTICS_STUDENT_COURSE_PROGRAM,
        CREATE_ANALYTICS_COURSE_PROGRAM_VALUES,
        CREATE_ANALYTICS_CCGI_FAVORITE_CAREER,
        CREATE_ANALYTICS_CCGI_CAREER_CLUSTER,
        CREATE_ANALYTICS_CCGI_COLLEGE_FINANCIAL,
        CREATE_ANALYTICS_CCGI_COLLEGE_APPLICATION,
        CREATE_ANALYTICS_CCGI_APPLICATION_STATUS,
        CREATE_ANALYTICS_SYNC_META,
    ]


ANALYTICS_TABLE_NAMES = (
    'analytics_student_profile',
    'analytics_student_results',
    'analytics_student_marks',
    'analytics_ap_results',
    'analytics_ap_awards',
    'analytics_sat_results',
    'analytics_survey_responses',
    # Small DISTINCT column-discovery tables. These back the unfiltered
    # test/mark column tree without re-scanning the multi-million-row
    # results/student_marks_list fact tables on every page load.
    'analytics_test_metadata',
    'analytics_marks_metadata',
    'analytics_student_course_program',
    'analytics_course_program_values',
    # CCGI (California College Guidance Initiative) dashboards. Flattened from
    # the PostgreSQL ccgi_career_explo / ccgi_college_financial JSONB tables and
    # enriched with the student's most recent current_student row.
    'analytics_ccgi_favorite_career',
    'analytics_ccgi_career_cluster',
    'analytics_ccgi_college_financial',
    # One row per (student, college application) across CSU / UC / CCC, with the
    # application status (Completed / In Progress) and destination college.
    'analytics_ccgi_college_application',
    # One row per (student, system, status) where status is Completed /
    # In Progress / Not Applied. Includes on-track students with no application
    # so they are not invisible in the system funnel charts.
    'analytics_ccgi_application_status',
)


CREATE_CURRENT_STUDENT = """
CREATE TABLE IF NOT EXISTS current_student (
    stu_annual_key String,
    ssid String,
    student_id String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    gender Nullable(String),
    race Nullable(String),
    ell Nullable(String),
    sed Nullable(String),
    foster Nullable(String),
    homeless Nullable(String),
    sped Nullable(String),
    gate Nullable(String),
    in_504 Nullable(String),
    dli Nullable(String),
    atendace_rate Nullable(Float64),
    suspension_indicator Nullable(String),
    ltel Nullable(String),
    status Nullable(String),
    school_year String,
    add_data Nullable(String),
    INDEX idx_cs_school school_state_id TYPE set(100) GRANULARITY 4,
    INDEX idx_cs_grade grade TYPE set(20) GRANULARITY 4,
    INDEX idx_cs_status status TYPE set(10) GRANULARITY 4
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, stu_annual_key)
SETTINGS index_granularity = 8192
"""

CREATE_STUDENT_ENROLL = """
CREATE TABLE IF NOT EXISTS student_enroll (
    stu_enrollment_key String,
    stu_annual_key Nullable(String),
    student_id String,
    ssid Nullable(String),
    last_name Nullable(String),
    middle_name Nullable(String),
    first_name Nullable(String),
    birth_date Nullable(DateTime64),
    gender Nullable(String),
    race Nullable(String),
    status Nullable(String),
    enter_date String,
    leave_date Nullable(DateTime64),
    us_enter_date Nullable(DateTime64),
    state_enter_date Nullable(DateTime64),
    ell_status Nullable(String),
    fep_date Nullable(DateTime64),
    instructional_setting Nullable(String),
    grade Nullable(String),
    special_program_code Nullable(String),
    school_name Nullable(String),
    state_school_code Nullable(String),
    migrant Nullable(String),
    homeless Nullable(String),
    foster Nullable(String),
    school_year String
)
ENGINE = MergeTree
ORDER BY (student_id, school_year, enter_date)
SETTINGS index_granularity = 8192
"""

CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS results (
    test_id String,
    ssid String,
    local_student_id String,
    school_year String,
    school_code String,
    pl Nullable(String),
    ss Nullable(String),
    other_info Nullable(String),
    test_group_key String
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, test_group_key)
SETTINGS index_granularity = 8192
"""

CREATE_TESTS = """
CREATE TABLE IF NOT EXISTS tests (
    test_group_key String,
    test_name String,
    subject String,
    school_year String,
    test_window Nullable(String)
)
ENGINE = MergeTree
ORDER BY (test_group_key)
SETTINGS index_granularity = 8192
"""

CREATE_RESULTS_WITH_TESTS = """
CREATE TABLE IF NOT EXISTS results_with_tests (
    test_id String,
    ssid String,
    local_student_id String,
    school_year String,
    school_code String,
    pl Nullable(String),
    ss Nullable(String),
    other_info Nullable(String),
    test_group_key String,
    test_name String,
    subject String,
    test_window Nullable(String)
)
ENGINE = MergeTree
ORDER BY (ssid, school_year, test_name, subject)
SETTINGS index_granularity = 8192
"""

CREATE_STUDENT_MARKS_LIST = """
CREATE TABLE IF NOT EXISTS student_marks_list (
    marks_id String,
    stu_annual_key String,
    ssid String,
    school_year String,
    student_id Nullable(String),
    school_name Nullable(String),
    state_school_id Nullable(String),
    school_type Nullable(String),
    subject Nullable(String),
    course_name Nullable(String),
    staff_name Nullable(String),
    staff_email Nullable(String),
    staff_employee_id Nullable(String),
    mark_period String,
    mark Nullable(String),
    created_at Nullable(DateTime64),
    updated_at Nullable(DateTime64)
)
ENGINE = MergeTree
ORDER BY (school_year, subject, ssid, stu_annual_key, mark_period)
SETTINGS index_granularity = 8192
"""

CREATE_NEXTYEARLIST_PLACEMENT = """
CREATE TABLE IF NOT EXISTS nextyearlist_placement (
    id Nullable(Int64),
    ssid String,
    student_id Nullable(String),
    stu_annual_key Nullable(String),
    school_year String,
    next_year_school_state_id String,
    next_year_school_name Nullable(String)
)
ENGINE = MergeTree
ORDER BY (ssid, school_year)
SETTINGS index_granularity = 8192
"""

CREATE_SCHOOL = """
CREATE TABLE IF NOT EXISTS school (
    dw_schoolkey Int32,
    state_id Nullable(String),
    sis_key Nullable(String),
    assessment_school_code Nullable(String),
    short_name Nullable(String),
    school_name String,
    school_group Nullable(String)
)
ENGINE = MergeTree
ORDER BY (dw_schoolkey)
SETTINGS index_granularity = 8192
"""

CREATE_SCHEDULE_LIST = """
CREATE TABLE IF NOT EXISTS schedule_list (
    schedule_id String,
    stu_annual_key String,
    ssid String,
    school_year String,
    student_id Nullable(String),
    school_name Nullable(String),
    state_school_id Nullable(String),
    school_type Nullable(String),
    course_state_equivalence_code Nullable(String),
    course_code Nullable(String),
    course_key Nullable(String),
    course_subject Nullable(String),
    course_name Nullable(String),
    period Nullable(String),
    staff_name Nullable(String),
    staff_email Nullable(String),
    staff_employee_id Nullable(String),
    schedule_status Nullable(String),
    created_at Nullable(DateTime64),
    updated_at Nullable(DateTime64)
)
ENGINE = MergeTree
ORDER BY (stu_annual_key, schedule_id)
SETTINGS index_granularity = 8192
"""

CREATE_RELATIVE_YEAR = """
CREATE TABLE IF NOT EXISTS relative_year (
    school_year String,
    relative_alias String,
    relative_label String,
    offset_years Nullable(Int32),
    is_current Nullable(Bool)
)
ENGINE = MergeTree
ORDER BY (school_year)
SETTINGS index_granularity = 8192
"""

CREATE_MATHR_RECOMMENDATION = """
CREATE TABLE IF NOT EXISTS mathr_recommendation (
    id Nullable(Int64),
    stu_annual_key String,
    ssid String,
    school_year String,
    state_school_id Nullable(String),
    staff_employee_id Nullable(String),
    math_selection Nullable(String),
    selected_by_id Nullable(Int64),
    created_at Nullable(DateTime),
    updated_at Nullable(DateTime)
)
ENGINE = MergeTree
ORDER BY (ssid, school_year)
SETTINGS index_granularity = 8192
"""

CREATE_STUDENT_ATTENDANCE = """
CREATE TABLE IF NOT EXISTS student_attendance (
    stu_annual_key String,
    ssid String,
    student_id Nullable(String),
    last_name Nullable(String),
    first_name Nullable(String),
    school_short_name Nullable(String),
    school_year String,
    student_status Nullable(String),
    absent_fte Nullable(Float64),
    tardy_count Nullable(Int32),
    days_enrolled Nullable(Int32),
    attendance_percent Nullable(Float64),
    absence_dates Nullable(String),
    updated_at Nullable(DateTime64)
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, stu_annual_key)
SETTINGS index_granularity = 8192
"""

CREATE_STUDENT_INCIDENTS = """
CREATE TABLE IF NOT EXISTS student_incidents (
    stu_annual_key String,
    ssid String,
    student_id Nullable(Int64),
    incidents_data Nullable(String),
    updated_at Nullable(DateTime64)
)
ENGINE = MergeTree
ORDER BY (ssid, stu_annual_key)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_STUDENT_PROFILE = """
CREATE TABLE IF NOT EXISTS analytics_student_profile (
    stu_annual_key String,
    ssid String,
    student_id String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    school_short_name Nullable(String),
    gender Nullable(String),
    race Nullable(String),
    ell Nullable(String),
    sed Nullable(String),
    foster Nullable(String),
    homeless Nullable(String),
    sped Nullable(String),
    gate Nullable(String),
    in_504 Nullable(String),
    dli Nullable(String),
    atendace_rate Nullable(Float64),
    suspension_indicator Nullable(String),
    ltel Nullable(String),
    status Nullable(String),
    school_year String,
    add_data Nullable(String),
    first_name Nullable(String),
    middle_name Nullable(String),
    last_name Nullable(String),
    birth_date Nullable(DateTime64),
    enter_date Nullable(DateTime64),
    leave_date Nullable(DateTime64),
    us_enter_date Nullable(DateTime64),
    state_enter_date Nullable(DateTime64),
    ell_status Nullable(String),
    fep_date Nullable(DateTime64),
    instructional_setting Nullable(String),
    special_program_code Nullable(String),
    migrant Nullable(String),
    attendance_percent Nullable(Float64),
    absent_fte Nullable(Float64),
    tardy_count Nullable(Int32),
    days_enrolled Nullable(Int32)
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, stu_annual_key)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_STUDENT_RESULTS = """
CREATE TABLE IF NOT EXISTS analytics_student_results (
    test_id String,
    ssid String,
    local_student_id String,
    school_year String,
    school_code String,
    pl Nullable(String),
    ss Nullable(String),
    other_info Nullable(String),
    test_group_key String,
    test_name String,
    subject String,
    test_window Nullable(String),
    stu_annual_key Nullable(String),
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    status Nullable(String),
    gender Nullable(String),
    race Nullable(String),
    ell Nullable(String),
    sed Nullable(String),
    sped Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, test_name, subject, ssid)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_STUDENT_MARKS = """
CREATE TABLE IF NOT EXISTS analytics_student_marks (
    marks_id String,
    stu_annual_key String,
    ssid String,
    school_year String,
    student_id Nullable(String),
    school_name Nullable(String),
    state_school_id Nullable(String),
    school_type Nullable(String),
    subject Nullable(String),
    course_name Nullable(String),
    staff_name Nullable(String),
    staff_email Nullable(String),
    staff_employee_id Nullable(String),
    mark_period String,
    mark Nullable(String),
    created_at Nullable(DateTime64),
    updated_at Nullable(DateTime64),
    student_name Nullable(String),
    grade Nullable(String),
    status Nullable(String),
    gender Nullable(String),
    school Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, stu_annual_key, mark_period)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_AP_RESULTS = """
CREATE TABLE IF NOT EXISTS analytics_ap_results (
    ssid String,
    admin_year String,
    exam_name String,
    exam_grade Nullable(String),
    exam_id String
)
ENGINE = MergeTree
ORDER BY (admin_year, ssid, exam_name)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_AP_AWARDS = """
CREATE TABLE IF NOT EXISTS analytics_ap_awards (
    ssid String,
    award_year String,
    award_description String
)
ENGINE = MergeTree
ORDER BY (award_year, ssid)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_SAT_RESULTS = """
CREATE TABLE IF NOT EXISTS analytics_sat_results (
    ssid String,
    school_year String,
    test_date String,
    school_name String,
    total_score Nullable(Int32),
    ebrw_score Nullable(Int32),
    math_score Nullable(Int32),
    percentile_state_total Nullable(Int32),
    percentile_state_rw Nullable(Int32),
    percentile_state_math Nullable(Int32),
    percentile_country_total Nullable(Int32),
    percentile_country_rw Nullable(Int32),
    percentile_country_math Nullable(Int32),
    percentile_natrep_total Nullable(Int32),
    percentile_natrep_rw Nullable(Int32),
    percentile_natrep_math Nullable(Int32),
    percentile_natuser_total Nullable(Int32),
    percentile_natuser_rw Nullable(Int32),
    percentile_natuser_math Nullable(Int32)
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, test_date)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_SURVEY_RESPONSES = """
CREATE TABLE IF NOT EXISTS analytics_survey_responses (
    student_id String,
    ssid String,
    survey_type String,
    school_year String,
    testing_window String,
    grade_range String,
    question_order Int32,
    value Nullable(Int32)
)
ENGINE = MergeTree
ORDER BY (school_year, student_id, survey_type, question_order)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_TEST_METADATA = """
CREATE TABLE IF NOT EXISTS analytics_test_metadata (
    school_year String,
    test_name String,
    subject String,
    test_window String,
    info_key String
)
ENGINE = MergeTree
ORDER BY (school_year, test_name, subject, test_window, info_key)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_MARKS_METADATA = """
CREATE TABLE IF NOT EXISTS analytics_marks_metadata (
    school_year String,
    subject String,
    course_name String,
    mark_period String
)
ENGINE = MergeTree
ORDER BY (school_year, subject, course_name, mark_period)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_STUDENT_COURSE_PROGRAM = """
CREATE TABLE IF NOT EXISTS analytics_student_course_program (
    stu_annual_key String,
    school_year String,
    course_prog_tag String,
    course_prog_category String,
    course_prog_discipline String,
    course_prog_level String
)
ENGINE = MergeTree
ORDER BY (school_year, stu_annual_key)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_COURSE_PROGRAM_VALUES = """
CREATE TABLE IF NOT EXISTS analytics_course_program_values (
    stu_annual_key String,
    school_year String,
    dimension String,
    value String
)
ENGINE = MergeTree
ORDER BY (school_year, dimension, value, stu_annual_key)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_CCGI_FAVORITE_CAREER = """
CREATE TABLE IF NOT EXISTS analytics_ccgi_favorite_career (
    ssid String,
    stu_annual_key String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    status Nullable(String),
    school_year String,
    career String
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, career)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_CCGI_CAREER_CLUSTER = """
CREATE TABLE IF NOT EXISTS analytics_ccgi_career_cluster (
    ssid String,
    stu_annual_key String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    status Nullable(String),
    school_year String,
    cluster String,
    cluster_count Int32
)
ENGINE = MergeTree
ORDER BY (school_year, ssid, cluster)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_CCGI_COLLEGE_FINANCIAL = """
CREATE TABLE IF NOT EXISTS analytics_ccgi_college_financial (
    ssid String,
    stu_annual_key String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    status Nullable(String),
    school_year String,
    csu_gpa Nullable(Float64),
    csu_eligibility Nullable(String),
    csu_completed_apps Int32,
    csu_in_progress_apps Int32,
    uc_gpa Nullable(Float64),
    uc_eligibility Nullable(String),
    uc_completed_apps Int32,
    uc_in_progress_apps Int32,
    ccc_completed_apps Int32,
    ccc_completed_app Nullable(String),
    ccc_most_recent_completed_date Nullable(String),
    financial_aid_status Nullable(String),
    csu_eligibility_detail Nullable(String),
    uc_eligibility_detail Nullable(String),
    csu_requires_review Int32,
    uc_requires_review Int32,
    review_scope Nullable(String),
    ccc_apply_id Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, ssid)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_CCGI_COLLEGE_APPLICATION = """
CREATE TABLE IF NOT EXISTS analytics_ccgi_college_application (
    ssid String,
    stu_annual_key String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    status Nullable(String),
    school_year String,
    system String,
    application_status String,
    college_name String,
    most_recent_completed_date Nullable(String),
    csu_eligibility Nullable(String),
    uc_eligibility Nullable(String),
    application_id Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, system, ssid, college_name)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_CCGI_APPLICATION_STATUS = """
CREATE TABLE IF NOT EXISTS analytics_ccgi_application_status (
    ssid String,
    stu_annual_key String,
    student_name Nullable(String),
    grade Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    status Nullable(String),
    school_year String,
    system String,
    application_status String,
    eligibility Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, system, application_status, ssid)
SETTINGS index_granularity = 8192
"""

CREATE_ANALYTICS_SYNC_META = """
CREATE TABLE IF NOT EXISTS analytics_sync_meta (
    table_name String,
    last_sync DateTime64,
    source_max_ts Nullable(DateTime64),
    row_count Int64,
    sync_version String,
    mode String
)
ENGINE = ReplacingMergeTree(last_sync)
ORDER BY table_name
SETTINGS index_granularity = 8192
"""

PROJECTION_DDL = (
    "ALTER TABLE analytics_student_profile ADD PROJECTION IF NOT EXISTS p_year_ssid "
    "(SELECT * ORDER BY school_year, ssid, stu_annual_key)",
    "ALTER TABLE analytics_student_results ADD PROJECTION IF NOT EXISTS p_test_lookup "
    "(SELECT * ORDER BY ssid, school_year, test_name, subject)",
    "ALTER TABLE analytics_student_marks ADD PROJECTION IF NOT EXISTS p_mark_lookup "
    "(SELECT * ORDER BY stu_annual_key, school_year, subject)",
)

SECONDARY_INDEX_DDL = (
    "ALTER TABLE current_student ADD INDEX IF NOT EXISTS idx_cs_school school_state_id TYPE set(100) GRANULARITY 4",
    "ALTER TABLE current_student ADD INDEX IF NOT EXISTS idx_cs_grade grade TYPE set(20) GRANULARITY 4",
    "ALTER TABLE current_student ADD INDEX IF NOT EXISTS idx_cs_status status TYPE set(10) GRANULARITY 4",
)

# Columns added to existing tables after their initial CREATE. ``CREATE TABLE
# IF NOT EXISTS`` never alters an already-existing table, so additive column
# changes must be applied explicitly. Order matters: it must match the order
# used in the corresponding CREATE and the derived INSERT ... SELECT projection.
COLUMN_MIGRATION_DDL = (
    "ALTER TABLE analytics_ccgi_college_financial "
    "ADD COLUMN IF NOT EXISTS csu_eligibility_detail Nullable(String)",
    "ALTER TABLE analytics_ccgi_college_financial "
    "ADD COLUMN IF NOT EXISTS uc_eligibility_detail Nullable(String)",
    "ALTER TABLE analytics_ccgi_college_financial "
    "ADD COLUMN IF NOT EXISTS csu_requires_review Int32 DEFAULT 0",
    "ALTER TABLE analytics_ccgi_college_financial "
    "ADD COLUMN IF NOT EXISTS uc_requires_review Int32 DEFAULT 0",
    "ALTER TABLE analytics_ccgi_college_financial "
    "ADD COLUMN IF NOT EXISTS review_scope Nullable(String)",
    "ALTER TABLE analytics_ccgi_college_financial "
    "ADD COLUMN IF NOT EXISTS ccc_apply_id Nullable(String)",
    "ALTER TABLE analytics_ccgi_college_application "
    "ADD COLUMN IF NOT EXISTS csu_eligibility Nullable(String)",
    "ALTER TABLE analytics_ccgi_college_application "
    "ADD COLUMN IF NOT EXISTS uc_eligibility Nullable(String)",
    "ALTER TABLE analytics_ccgi_college_application "
    "ADD COLUMN IF NOT EXISTS application_id Nullable(String)",
)

