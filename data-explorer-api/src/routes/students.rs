use crate::clickhouse::ClickHouseClient;
use crate::columns::default_columns;
use crate::error::{bad_request, ok_json, ApiError, ApiResult};
use crate::models::StudentListParams;
use crate::query::build_and_run_student_query;
use rocket::form::FromForm;
use rocket::get;
use rocket::serde::json::Json;
use rocket::State;
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, FromForm)]
pub struct StudentQuery {
    pub page: Option<u32>,
    pub limit: Option<u32>,
    pub columns: Option<String>,
    pub filters: Option<String>,
    pub search: Option<String>,
    pub search_ids: Option<String>,
    pub school_year: Option<String>,
    pub grade: Option<String>,
    pub status: Option<String>,
    pub sort_by: Option<String>,
    pub sort_dir: Option<String>,
}

#[get("/students?<q..>")]
pub async fn get_students(
    q: StudentQuery,
    client: &State<ClickHouseClient>,
) -> ApiResult<Json<Value>> {
    let cfg = client.config();

    let page = q.page.unwrap_or(1).max(1);
    let limit = q
        .limit
        .unwrap_or(cfg.default_page_limit)
        .clamp(1, cfg.max_page_limit);

    let selected_columns = parse_json_array(q.columns.as_deref())
        .unwrap_or_else(default_columns);

    if selected_columns.is_empty() {
        return Err(bad_request("columns must not be empty"));
    }

    let filters = parse_filters(q.filters.as_deref())?;
    let search_ids = parse_json_array(q.search_ids.as_deref()).unwrap_or_default();

    let school_year = q
        .school_year
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| cfg.default_school_year.clone());

    let status = q
        .status
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| cfg.default_status.clone());

    let sort_dir = q
        .sort_dir
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "asc".to_string());

    if sort_dir != "asc" && sort_dir != "desc" {
        return Err(bad_request("sort_dir must be 'asc' or 'desc'"));
    }

    let params = StudentListParams {
        page,
        limit,
        selected_columns,
        filters,
        search: q.search.unwrap_or_default(),
        search_ids,
        school_year,
        grade: q.grade.filter(|g| !g.is_empty()),
        status,
        sort_by: q.sort_by.filter(|s| !s.is_empty()),
        sort_dir,
    };

    let response = build_and_run_student_query(client.inner(), params).await?;
    ok_json(serde_json::to_value(response).map_err(|e| ApiError::Internal(e.to_string()))?)
}

fn parse_json_array(raw: Option<&str>) -> Option<Vec<String>> {
    let raw = raw?;
    if raw.trim().is_empty() {
        return None;
    }
    let value: Value = serde_json::from_str(raw).ok()?;
    match value {
        Value::Array(items) => Some(
            items
                .into_iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect(),
        ),
        Value::String(s) if !s.is_empty() => Some(vec![s]),
        _ => None,
    }
}

fn parse_filters(raw: Option<&str>) -> Result<HashMap<String, Vec<String>>, crate::error::ApiErrorResponse> {
    let Some(raw) = raw else {
        return Ok(HashMap::new());
    };
    if raw.trim().is_empty() {
        return Ok(HashMap::new());
    }

    let value: Value = serde_json::from_str(raw)
        .map_err(|e| bad_request(format!("invalid filters JSON: {e}")))?;

    let Value::Object(map) = value else {
        return Err(bad_request("filters must be a JSON object"));
    };

    let mut out = HashMap::new();
    for (key, val) in map {
        let values = match val {
            Value::Array(items) => items
                .into_iter()
                .map(|v| match v {
                    Value::String(s) => s,
                    Value::Number(n) => n.to_string(),
                    Value::Bool(b) => b.to_string(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                })
                .collect(),
            Value::String(s) => vec![s],
            Value::Number(n) => vec![n.to_string()],
            Value::Bool(b) => vec![b.to_string()],
            Value::Null => vec![String::new()],
            other => vec![other.to_string()],
        };
        out.insert(key, values);
    }

    Ok(out)
}
