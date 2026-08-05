use crate::clickhouse::{quote_like, quote_string, ClickHouseClient};
use crate::columns::{is_static_column, static_column_definitions};
use crate::error::ApiError;
use crate::models::{MarkColumnInfo, StudentListParams, StudentsResponse, TestColumnInfo};
use crate::query::mark_columns::{mark_column_label, mark_column_sql, resolve_mark_columns};
use crate::query::test_columns::{resolve_test_columns, test_column_label, test_column_sql};
use serde_json::{json, Value};
use std::collections::HashMap;

const FILTER_OPTION_COLUMNS: &[&str] = &["grade", "gender", "race", "status", "school"];

pub async fn build_and_run_student_query(
    client: &ClickHouseClient,
    params: StudentListParams,
) -> Result<StudentsResponse, ApiError> {
    let test_cols = resolve_test_columns(client, &params.selected_columns).await?;
    let mark_cols = resolve_mark_columns(client, &params.selected_columns).await?;

    let where_sql = build_where_clause(&params)?;
    let select_sql = build_select_clause(&params.selected_columns, &test_cols, &mark_cols);
    let order_sql = build_order_clause(&params, &test_cols, &mark_cols);

    let offset = (params.page.saturating_sub(1)) * params.limit;

    let data_sql = format!(
        "SELECT {select_sql} \
         FROM current_student AS cs \
         WHERE {where_sql} \
         ORDER BY {order_sql} \
         LIMIT {limit} OFFSET {offset}",
        limit = params.limit,
        offset = offset,
    );

    let count_sql = format!(
        "SELECT count() AS total \
         FROM current_student AS cs \
         WHERE {where_sql}"
    );

    let rows = client.query_rows(&data_sql).await?;
    let total = client.query_count(&count_sql).await?;
    let total_pages = if total == 0 {
        0
    } else {
        ((total + params.limit as u64 - 1) / params.limit as u64) as u32
    };

    let students = rows
        .into_iter()
        .map(|row| normalize_student_row(row, &params.selected_columns))
        .collect();

    let column_definitions = build_column_definitions(&params.selected_columns, &test_cols, &mark_cols);
    let filter_options =
        build_filter_options(client, &params, &where_sql).await?;

    Ok(StudentsResponse {
        students,
        selected_columns: params.selected_columns.clone(),
        total,
        page: params.page,
        total_pages,
        column_definitions,
        filter_options,
        filters: params.filters.clone(),
        search: params.search.clone(),
        school_year: params.school_year.clone(),
        grade: params.grade.clone().unwrap_or_default(),
        status: params.status.clone(),
        sort_by: params.sort_by.clone().unwrap_or_default(),
        sort_dir: params.sort_dir.clone(),
        survey_colors: HashMap::new(),
    })
}

fn build_select_clause(
    selected_columns: &[String],
    test_cols: &[TestColumnInfo],
    mark_cols: &[MarkColumnInfo],
) -> String {
    let mut parts = Vec::new();

    // Always include ssid for stable row identity
    if !selected_columns.iter().any(|c| c == "ssid") {
        parts.push("cs.ssid AS ssid".to_string());
    }

    for col in selected_columns {
        if is_static_column(col) {
            parts.push(format!("cs.{col} AS `{alias}`", alias = col.replace('`', "``")));
        } else if let Some(info) = test_cols.iter().find(|t| &t.col_name == col) {
            parts.push(test_column_sql(info));
        } else if let Some(info) = mark_cols.iter().find(|m| &m.col_name == col) {
            parts.push(mark_column_sql(info));
        } else {
            parts.push(format!("NULL AS `{alias}`", alias = col.replace('`', "``")));
        }
    }

    if parts.is_empty() {
        parts.push("cs.ssid AS ssid".to_string());
        parts.push("cs.student_name AS student_name".to_string());
    }

    parts.join(", ")
}

fn build_where_clause(params: &StudentListParams) -> Result<String, ApiError> {
    let mut clauses = vec![
        format!("cs.school_year = {}", quote_string(&params.school_year)),
        format!("cs.status = {}", quote_string(&params.status)),
    ];

    if let Some(grade) = &params.grade {
        if !grade.is_empty() {
            clauses.push(format!("cs.grade = {}", quote_string(grade)));
        }
    }

    if !params.search_ids.is_empty() {
        let ids = params
            .search_ids
            .iter()
            .map(|id| quote_string(id))
            .collect::<Vec<_>>()
            .join(", ");
        clauses.push(format!("cs.ssid IN ({ids})"));
    } else if !params.search.trim().is_empty() {
        let term = quote_like(params.search.trim());
        clauses.push(format!(
            "(lower(cs.student_name) LIKE lower({term}) \
              OR cs.ssid LIKE {term} \
              OR cs.student_id LIKE {term})"
        ));
    }

    for (col, values) in &params.filters {
        if values.is_empty() {
            continue;
        }
        if col == "search" || col == "school_year" || col == "grade" || col == "status" {
            continue;
        }

        if is_static_column(col) {
            let quoted: Vec<String> = values
                .iter()
                .filter(|v| !v.is_empty())
                .map(|v| quote_string(v))
                .collect();
            if !quoted.is_empty() {
                clauses.push(format!("cs.{col} IN ({})", quoted.join(", ")));
            }
            if values.iter().any(|v| v.is_empty()) {
                clauses.push(format!("(cs.{col} IS NULL OR cs.{col} = '')"));
            }
        }
    }

    Ok(clauses.join(" AND "))
}

fn build_order_clause(
    params: &StudentListParams,
    test_cols: &[TestColumnInfo],
    mark_cols: &[MarkColumnInfo],
) -> String {
    let direction = if params.sort_dir.eq_ignore_ascii_case("desc") {
        "DESC"
    } else {
        "ASC"
    };

    if let Some(sort_by) = &params.sort_by {
        if !sort_by.is_empty() {
            if is_static_column(sort_by) {
                return format!("cs.{sort_by} {direction} NULLS LAST");
            }
            if test_cols.iter().any(|t| &t.col_name == sort_by)
                || mark_cols.iter().any(|m| &m.col_name == sort_by)
            {
                return format!("`{alias}` {direction} NULLS LAST", alias = sort_by.replace('`', "``"));
            }
        }
    }

    "cs.student_name ASC NULLS LAST".to_string()
}

fn build_column_definitions(
    selected_columns: &[String],
    test_cols: &[TestColumnInfo],
    mark_cols: &[MarkColumnInfo],
) -> HashMap<String, String> {
    let static_defs = static_column_definitions();
    let mut out = HashMap::new();

    for col in selected_columns {
        if let Some(label) = static_defs.get(col.as_str()) {
            out.insert(col.clone(), (*label).to_string());
        } else if let Some(info) = test_cols.iter().find(|t| &t.col_name == col) {
            out.insert(col.clone(), test_column_label(info));
        } else if let Some(info) = mark_cols.iter().find(|m| &m.col_name == col) {
            out.insert(col.clone(), mark_column_label(info));
        } else {
            out.insert(col.clone(), col.clone());
        }
    }

    out
}

async fn build_filter_options(
    client: &ClickHouseClient,
    params: &StudentListParams,
    where_sql: &str,
) -> Result<HashMap<String, Vec<Value>>, ApiError> {
    let mut options = HashMap::new();

    for col in FILTER_OPTION_COLUMNS {
        let sql = format!(
            "SELECT DISTINCT cs.{col} AS value \
             FROM current_student AS cs \
             WHERE {where_sql} AND cs.{col} IS NOT NULL AND cs.{col} != '' \
             ORDER BY value ASC \
             LIMIT 31"
        );

        let rows = client.query_rows(&sql).await?;
        if rows.len() <= 1 {
            continue;
        }

        let values: Vec<Value> = rows
            .into_iter()
            .filter_map(|row| row.get("value").cloned())
            .map(|v| {
                if v.is_null() {
                    json!("")
                } else {
                    v
                }
            })
            .collect();

        if !values.is_empty() {
            options.insert((*col).to_string(), values);
        }
    }

    // Suppress unused variable warning for params in future extensions
    let _ = params;

    Ok(options)
}

fn normalize_student_row(
    mut row: HashMap<String, Value>,
    selected_columns: &[String],
) -> HashMap<String, Value> {
    let mut out = HashMap::new();

    for col in selected_columns {
        let value = row
            .remove(col)
            .or_else(|| row.remove(&col.replace('-', "_")))
            .unwrap_or(Value::Null);

        out.insert(
            col.clone(),
            match value {
                Value::Null => Value::String(String::new()),
                Value::Number(n) => {
                    if let Some(f) = n.as_f64() {
                        json!(f)
                    } else {
                        Value::Number(n)
                    }
                }
                Value::String(s) => Value::String(s),
                other => other,
            },
        );
    }

    // Include ssid if it was auto-selected but not requested
    if !selected_columns.iter().any(|c| c == "ssid") {
        if let Some(ssid) = row.remove("ssid") {
            out.insert("ssid".into(), ssid);
        }
    }

    out
}
