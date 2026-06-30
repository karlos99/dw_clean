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
        CREATE_STUDENT_MARKS_LIST,
        CREATE_NEXTYEARLIST_PLACEMENT,
        CREATE_SCHOOL,
        CREATE_SCHEDULE_LIST,
        CREATE_RELATIVE_YEAR,
        CREATE_MATHR_RECOMMENDATION,
    ]


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
    add_data Nullable(String)
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
ORDER BY (ssid, school_year, test_group_key)
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
ORDER BY (stu_annual_key, mark_period)
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
