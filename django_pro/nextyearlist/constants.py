"""Column definitions and constants for next year list views."""

# Base column definitions — extends dataexploration columns with NYL-specific fields
COLUMN_DEFINITIONS = {
    # Student Info
    "ssid": "SSID",
    "student_id": "Student ID",
    "student_name": "Student Name",
    "grade": "Grade",
    "school_year": "School Year",
    "school": "Current School",
    "school_state_id": "Current School Code",
    "elem_teacher_name": "Elementary Homeroom Teacher",

    # Next Year fields
    "next_year_school_state_id": "Next Year School Code",
    "next_year_school_name": "Next Year School",

    # Math recommendation
    "math_selection": "Math Recommendation",

    # Demographics
    "gender": "Gender",
    "race": "Race/Ethnicity",
    "ell": "ELL Status",
    "status": "Status",
    "birth_date": "Birth Date",
    "us_enter_date": "US Enter Date",

    # Programs & Services
    "sped": "Special Education",
    "gate": "GATE",
    "in_504": "504 Plan",
    "sed": "SED",
    "foster": "Foster Youth",
    "homeless": "Homeless",
    "dli": "DLI",
    "ltel": "LTEL",
    "fep_date": "FEP Date",
    "migrant": "Migrant",
    "atendace_rate": "Attendance Rate",
    "suspension_indicator": "Suspension",
}

COLUMN_CATEGORIES = {
    "student_info": {
        "title": "Student Information",
        "columns": [
            "ssid", "student_id", "student_name", "grade", "school_year",
            "school", "school_state_id", "elem_teacher_name",
        ],
    },
    "next_year": {
        "title": "Next Year Placement",
        "columns": ["next_year_school_state_id", "next_year_school_name"],
    },
    "math_recommendation": {
        "title": "Math Recommendation",
        "columns": ["math_selection"],
    },
    "demographics": {
        "title": "Demographics",
        "columns": ["gender", "race", "status"],
    },
    "dates": {
        "title": "Dates",
        "columns": ["birth_date", "us_enter_date", "fep_date"],
    },
    "programs": {
        "title": "Programs & Services",
        "columns": ["ell", "sped", "gate", "in_504", "sed", "foster", "homeless", "dli", "ltel", "migrant"],
    },
    "performance": {
        "title": "Performance & Behavior",
        "columns": ["atendace_rate", "suspension_indicator"],
    },
}

DEFAULT_COLUMNS = [
    "student_name", "ssid", "grade", "school_year",
    "next_year_school_name", "math_selection", "status",
]
