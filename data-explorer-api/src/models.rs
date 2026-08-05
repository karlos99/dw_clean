use serde::Serialize;
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize)]
pub struct StudentsResponse {
    pub students: Vec<HashMap<String, serde_json::Value>>,
    pub selected_columns: Vec<String>,
    pub total: u64,
    pub page: u32,
    pub total_pages: u32,
    pub column_definitions: HashMap<String, String>,
    pub filter_options: HashMap<String, Vec<serde_json::Value>>,
    pub filters: HashMap<String, Vec<String>>,
    pub search: String,
    pub school_year: String,
    pub grade: String,
    pub status: String,
    pub sort_by: String,
    pub sort_dir: String,
    pub survey_colors: HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct TestColumnInfo {
    pub col_name: String,
    pub year: String,
    pub test_name: String,
    pub subject: String,
    pub test_window: Option<String>,
    pub metric: TestMetric,
    pub other_info_key: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TestMetric {
    Pl,
    Ss,
    Dfp,
    Oi,
}

impl TestMetric {
    pub fn from_suffix(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "pl" => Some(Self::Pl),
            "ss" => Some(Self::Ss),
            "dfp" => Some(Self::Dfp),
            "oi" => Some(Self::Oi),
            _ => None,
        }
    }

    pub fn label(&self) -> &'static str {
        match self {
            Self::Pl => "Perf Lvl",
            Self::Ss => "Scale Score",
            Self::Dfp => "DFP",
            Self::Oi => "Info",
        }
    }
}

#[derive(Debug, Clone)]
pub struct MarkColumnInfo {
    pub col_name: String,
    pub year: String,
    pub subject: String,
    pub course_name: String,
    pub mark_period: Option<String>,
}

#[derive(Debug, Clone)]
pub struct StudentListParams {
    pub page: u32,
    pub limit: u32,
    pub selected_columns: Vec<String>,
    pub filters: HashMap<String, Vec<String>>,
    pub search: String,
    pub search_ids: Vec<String>,
    pub school_year: String,
    pub grade: Option<String>,
    pub status: String,
    pub sort_by: Option<String>,
    pub sort_dir: String,
}
