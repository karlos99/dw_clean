COLOR_MAP = {
    1: {"name": "Red", "hex": "#e21736", "class": "color-red"},
    2: {"name": "Orange", "hex": "#f58220", "class": "color-orange"},
    3: {"name": "Yellow", "hex": "#ffce00", "class": "color-yellow"},
    4: {"name": "Green", "hex": "#7fc31c", "class": "color-green"},
    5: {"name": "Blue", "hex": "#0071bc", "class": "color-blue"},
    0: {"name": "None", "hex": "#cccccc", "class": "color-none"},
}

STUDENT_GROUP_LABELS = {
    "ALL": "All Students",
    "AA": "Black or African American",
    "AI": "American Indian or Alaska Native",
    "AS": "Asian",
    "FI": "Filipino",
    "HI": "Hispanic or Latino",
    "PI": "Native Hawaiian or Pacific Islander",
    "WH": "White",
    "MR": "Multiple Races/Two or More Races",
    "EL": "English Learner",
    "LTEL": "Long-Term English Learner",
    "LT EL": "Long-Term English Learner",
    "SED": "Socioeconomically Disadvantaged",
    "SWD": "Students with Disabilities",
    "FOS": "Foster Youth",
    "HOM": "Homeless Youth",
}

STATUS_LEVEL_LABELS = {
    0: "No Status",
    1: "Very High",
    2: "High",
    3: "Medium",
    4: "Low",
    5: "Very Low",
}

# Status level codes when higher performance is better (Science, ELPI, etc.): 1 = Very Low … 5 = Very High.
STATUS_LEVEL_LABELS_HIGHER_IS_BETTER = {
    0: "No Status",
    1: "Very Low",
    2: "Low",
    3: "Medium",
    4: "High",
    5: "Very High",
}

HIGHER_IS_BETTER_INDICATOR_CODES = frozenset(
    {"SCIENCE", "ELPI", "CCI", "GRAD", "ELA", "MATH"}
)

CHANGE_LEVEL_LABELS = {
    0: "No Change",
    1: "Increased Significantly",
    2: "Increased",
    3: "Maintained",
    4: "Declined",
    5: "Declined Significantly",
}

ELPI_CHANGE_LABELS = {
    0: "No Change",
    1: "Declined Significantly",
    2: "Declined",
    3: "Maintained",
    4: "Increased",
    5: "Increased Significantly",
}

# CDE record layout student groups (excluding ALL)
INDICATOR_STUDENT_GROUPS = [
    "AA",
    "AI",
    "AS",
    "FI",
    "HI",
    "PI",
    "WH",
    "MR",
    "EL",
    "LTEL",
    "SED",
    "SWD",
    "FOS",
    "HOM",
]
