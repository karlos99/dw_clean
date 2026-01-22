"""Column definitions and constants for data exploration views."""

# Base column definitions (student demographics and info)
COLUMN_DEFINITIONS = {
    # Student Info
    "ssid": "SSID",
    "student_id": "Student ID",
    "student_name": "Student Name",
    "grade": "Grade",
    "school_year": "School Year",
    "school": "School",
    "school_state_id": "School Code",
    
    # Demographics
    "gender": "Gender",
    "race": "Race/Ethnicity",
    "ell": "ELL Status",
    "status": "Status",
    
    # Programs & Services
    "sped": "Special Education",
    "gate": "GATE",
    "in_504": "504 Plan",
    "sed": "SED",
    "foster": "Foster Youth",
    "homeless": "Homeless",
    "dli": "DLI",
    "ltel": "LTEL",
    "atendace_rate": "Attendance Rate",
    "suspension_indicator": "Suspension",
}

# Static column categories (non-test columns)
COLUMN_CATEGORIES = {
    "student_info": {
        "title": "Student Information",
        "columns": ["ssid", "student_id", "student_name", "grade", "school_year", "school", "school_state_id"]
    },
    "demographics": {
        "title": "Demographics",
        "columns": ["gender", "race", "ell", "status"]
    },
    "programs": {
        "title": "Programs & Services",
        "columns": ["sped", "gate", "in_504", "sed", "foster", "homeless", "dli", "ltel"]
    },
    "performance": {
        "title": "Performance & Behavior",
        "columns": ["atendace_rate", "suspension_indicator"]
    }
}

# Default visible columns
DEFAULT_COLUMNS = ["student_name", "ssid", "grade", "school_year", "status"]
