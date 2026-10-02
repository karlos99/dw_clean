use std::collections::HashMap;

pub const DEFAULT_COLUMNS: &[&str] =
    &["student_name", "ssid", "grade", "school_year", "status"];

/// Static column labels mirrored from django_pro/dataexploration/constants.py
pub fn static_column_definitions() -> HashMap<&'static str, &'static str> {
    HashMap::from([
        ("ssid", "SSID"),
        ("student_id", "Student ID"),
        ("student_name", "Student Name"),
        ("grade", "Grade"),
        ("school_year", "School Year"),
        ("school", "School"),
        ("school_state_id", "School Code"),
        ("gender", "Gender"),
        ("race", "Race/Ethnicity"),
        ("ell", "ELL Status"),
        ("status", "Status"),
        ("sped", "Special Education"),
        ("gate", "GATE"),
        ("in_504", "504 Plan"),
        ("sed", "SED"),
        ("foster", "Foster Youth"),
        ("homeless", "Homeless"),
        ("dli", "DLI"),
        ("ltel", "LTEL"),
        ("atendace_rate", "Attendance Rate"),
        ("suspension_indicator", "Suspension"),
        ("avid", "AVID"),
        ("avid_type", "AVID Type"),
    ])
}

pub fn is_static_column(col: &str) -> bool {
    static_column_definitions().contains_key(col)
}

pub fn static_select_expression(col: &str) -> Option<String> {
    if is_static_column(col) {
        Some(col.to_string())
    } else {
        None
    }
}

pub fn default_columns() -> Vec<String> {
    DEFAULT_COLUMNS.iter().map(|c| (*c).to_string()).collect()
}

pub fn display_test_name(name: &str) -> String {
    let mut out = name.to_string();
    if out.contains("Dibbles") {
        out = out.replace("Dibbles", "Dibels");
    }
    if out.to_ascii_lowercase().contains("iready") {
        out = out.replace("iready", "iReady").replace("Iready", "iReady");
    }
    out
}

pub fn normalize_key(value: &str) -> String {
    value
        .replace('-', "_")
        .replace(' ', "_")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect::<String>()
}

pub fn parse_year_from_prefixed_col(col: &str, prefix: &str) -> Option<String> {
    let normalized = col.replace('-', "_");
    if !normalized.starts_with(prefix) {
        return None;
    }
    let tail = &normalized[prefix.len()..];
    if tail.len() >= 9 && tail.chars().nth(4) == Some('_') {
        let year_part = &tail[..9];
        if year_part.chars().take(4).all(|c| c.is_ascii_digit())
            && year_part.chars().nth(4) == Some('_')
            && year_part.chars().skip(5).take(4).all(|c| c.is_ascii_digit())
        {
            return Some(year_part.replace('_', "-"));
        }
    }
    tail.split('_').next().map(|y| y.replace('_', "-"))
}
