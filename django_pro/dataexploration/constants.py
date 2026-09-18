"""Column definitions and constants for data exploration views."""

# Columns that are not physical current_student fields (no SQL SELECT/filter).
VIRTUAL_COLUMNS = frozenset({"picture"})

# Base column definitions (student demographics and info)
COLUMN_DEFINITIONS = {
    # Student Info
    "picture": "Picture",
    "ssid": "SSID",
    "student_id": "Student ID",
    "student_name": "Student Name",
    "grade": "Grade",
    "school_year": "School Year",
    "school": "School",
    "school_state_id": "School Code",
    "elem_teacher_name": "Elementary Homeroom Teacher",
    
    # Demographics
    "gender": "Gender",
    "race": "Race/Ethnicity",
    "race_list": "Race List",
    "ell": "ELL Status",
    "status": "Status",
    "birth_date": "Birth Date",
    "us_enter_date": "US Enter Date",
    "enter_date": "Enrollment Enter Date",
    "leave_date": "Enrollment Leave Date",
    
    # Programs & Services
    "sped": "Special Education",
    "gate": "GATE",
    "in_504": "504 Plan",
    "sed": "SED",
    "foster": "Foster Youth",
    "homeless": "Homeless",
    "dli": "DLI",
    "ltel": "LTEL",
    "dsusd_fep": "DSUSD FEP",
    "fep_date": "FEP Date",
    "migrant": "Migrant",
    "atendace_rate": "Attendance Rate",
    "suspension_indicator": "Suspension",
    "avid": "AVID",
    "avid_type": "AVID Type",
}

# Static column categories (non-test columns)
COLUMN_CATEGORIES = {
    "student_info": {
        "title": "Student Information",
        "columns": ["picture", "ssid", "student_id", "student_name", "grade", "school_year", "school", "school_state_id", "elem_teacher_name"]
    },
    "demographics": {
        "title": "Demographics",
        "columns": ["gender", "race", "race_list", "status"]
    },
    "dates": {
        "title": "Dates",
        "columns": ["birth_date", "us_enter_date", "fep_date", "enter_date", "leave_date"]
    },
    "programs": {
        "title": "Programs & Services",
        "columns": ["ell", "sped", "gate", "in_504", "sed", "foster", "homeless", "dli", "ltel", "dsusd_fep", "migrant", "avid", "avid_type"]
    },
    "performance": {
        "title": "Performance & Behavior",
        "columns": ["atendace_rate", "suspension_indicator"]
    }
}

# Default visible columns
DEFAULT_COLUMNS = ["student_name", "ssid", "grade", "school_year", "status"]

# ---------------------------------------------------------------------------
# School-year alias constants
# ---------------------------------------------------------------------------
# These sentinel values can be stored in query/chart configs instead of a
# hard-coded year string.  They are resolved to real years at query runtime
# via dataexploration.school_year_utils.resolve_school_year().

SY_THIS_YEAR = '__sy_this__'   # resolves to ProjectConfig.default_school_year
SY_LAST_YEAR = '__sy_prev1__'  # resolves to one year before the default
SY_TWO_PRIOR = '__sy_prev2__'  # resolves to two years before the default
SY_THREE_PRIOR = '__sy_prev3__'  # resolves to three years before the default
SY_FOUR_PRIOR = '__sy_prev4__'  # resolves to four years before the default
SY_FIVE_PRIOR = '__sy_prev5__'  # resolves to five years before the default

# Human-readable labels used in dropdowns
SCHOOL_YEAR_ALIASES = {
    SY_THIS_YEAR: 'This School Year',
    SY_LAST_YEAR: 'Last School Year',
    SY_TWO_PRIOR: 'Two Years Prior',
    SY_THREE_PRIOR: 'Three Years Prior',
    SY_FOUR_PRIOR: 'Four Years Prior',
    SY_FIVE_PRIOR: 'Five Years Prior',
}

