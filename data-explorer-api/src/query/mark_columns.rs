use crate::clickhouse::{quote_like, quote_string, ClickHouseClient};
use crate::columns::{normalize_key, parse_year_from_prefixed_col};
use crate::error::ApiError;
use crate::models::MarkColumnInfo;
use std::collections::{HashMap, HashSet};

pub async fn resolve_mark_columns(
    client: &ClickHouseClient,
    selected: &[String],
) -> Result<Vec<MarkColumnInfo>, ApiError> {
    let mark_cols: Vec<String> = selected
        .iter()
        .filter(|c| c.starts_with("mark_"))
        .cloned()
        .collect();

    if mark_cols.is_empty() {
        return Ok(Vec::new());
    }

    let mut years: HashSet<String> = HashSet::new();
    for col in &mark_cols {
        if let Some(year) = parse_year_from_prefixed_col(col, "mark_") {
            years.insert(year);
        }
    }

    if years.is_empty() {
        return Ok(Vec::new());
    }

    let year_list = years
        .iter()
        .map(|y| quote_string(y))
        .collect::<Vec<_>>()
        .join(", ");

    let sql = format!(
        "SELECT DISTINCT school_year, subject, course_name, mark_period \
         FROM student_marks_list \
         WHERE school_year IN ({year_list})"
    );

    let rows = client.query_rows(&sql).await?;
    let normalized_to_original: HashMap<String, String> = mark_cols
        .iter()
        .map(|c| (normalize_key(c), c.clone()))
        .collect();

    let mut infos = Vec::new();

    for row in rows {
        let year = row
            .get("school_year")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let subject = row
            .get("subject")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let course_name = row
            .get("course_name")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let mark_period = row
            .get("mark_period")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty());

        let key_base = format!(
            "{}_{}_{}_{}",
            normalize_key(&year),
            normalize_key(&subject),
            normalize_key(&course_name),
            normalize_key(mark_period.as_deref().unwrap_or(""))
        );
        let col_norm = format!("mark_{key_base}");

        if let Some(original) = normalized_to_original.get(&col_norm) {
            infos.push(MarkColumnInfo {
                col_name: original.clone(),
                year,
                subject,
                course_name,
                mark_period,
            });
        }
    }

    Ok(infos)
}

pub fn mark_column_sql(info: &MarkColumnInfo) -> String {
    let period_clause = if let Some(period) = &info.mark_period {
        format!(" AND lower(sm.mark_period) LIKE lower({}) ", quote_like(period))
    } else {
        " AND (sm.mark_period IS NULL OR sm.mark_period = '') ".to_string()
    };

    format!(
        "(SELECT sm.mark \
          FROM student_marks_list AS sm \
          WHERE sm.stu_annual_key = cs.stu_annual_key \
            AND sm.school_year = {year} \
            AND lower(sm.subject) LIKE lower({subject}) \
            AND lower(sm.course_name) LIKE lower({course}) \
            {period_clause} \
          LIMIT 1) AS `{alias}`",
        year = quote_string(&info.year),
        subject = quote_like(&info.subject),
        course = quote_like(&info.course_name),
        period_clause = period_clause,
        alias = info.col_name.replace('`', "``"),
    )
}

pub fn mark_column_label(info: &MarkColumnInfo) -> String {
    format!(
        "{} ({}) {}\n{}",
        info.course_name,
        info.subject,
        info.mark_period.as_deref().unwrap_or("Unknown"),
        info.year
    )
}
