use crate::clickhouse::{quote_like, quote_string, ClickHouseClient};
use crate::columns::{display_test_name, normalize_key, parse_year_from_prefixed_col};
use crate::error::ApiError;
use crate::models::{TestColumnInfo, TestMetric};
use std::collections::{HashMap, HashSet};

pub async fn resolve_test_columns(
    client: &ClickHouseClient,
    selected: &[String],
) -> Result<Vec<TestColumnInfo>, ApiError> {
    let test_cols: Vec<String> = selected
        .iter()
        .filter(|c| c.starts_with("test_"))
        .cloned()
        .collect();

    if test_cols.is_empty() {
        return Ok(Vec::new());
    }

    let mut years: HashSet<String> = HashSet::new();
    for col in &test_cols {
        if let Some(year) = parse_year_from_prefixed_col(col, "test_") {
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
        "SELECT DISTINCT school_year, test_name, subject, test_window \
         FROM tests \
         WHERE school_year IN ({year_list}) AND test_name != ''"
    );

    let rows = client.query_rows(&sql).await?;
    let normalized_to_original: HashMap<String, String> = test_cols
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
        let test_name = row
            .get("test_name")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let subject = row
            .get("subject")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let test_window = row
            .get("test_window")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty());

        let year_norm = normalize_key(&year);
        let test_norm = normalize_key(&test_name);
        let subject_norm = normalize_key(&subject);

        let (key_base, window_value) = if let Some(ref window) = test_window {
            let win_norm = normalize_key(window).trim_matches('_').to_string();
            (
                format!("{year_norm}_{test_norm}_{subject_norm}_{win_norm}"),
                Some(window.clone()),
            )
        } else {
            (
                format!("{year_norm}_{test_norm}_{subject_norm}"),
                None,
            )
        };

        for metric in [TestMetric::Pl, TestMetric::Ss, TestMetric::Dfp] {
            let suffix = match metric {
                TestMetric::Pl => "pl",
                TestMetric::Ss => "ss",
                TestMetric::Dfp => "dfp",
                TestMetric::Oi => "oi",
            };
            let col_norm = format!("test_{key_base}_{suffix}");
            if let Some(original) = normalized_to_original.get(&col_norm) {
                infos.push(TestColumnInfo {
                    col_name: original.clone(),
                    year: year.clone(),
                    test_name: test_name.clone(),
                    subject: subject.clone(),
                    test_window: window_value.clone(),
                    metric,
                    other_info_key: None,
                });
            }
        }
    }

    Ok(infos)
}

pub fn test_column_sql(info: &TestColumnInfo) -> String {
    let window_clause = info
        .test_window
        .as_ref()
        .map(|w| format!(" AND lower(t.test_window) LIKE lower({}) ", quote_like(w)))
        .unwrap_or_default();

    let metric_expr = match info.metric {
        TestMetric::Pl => "r.pl".to_string(),
        TestMetric::Ss => "r.ss".to_string(),
        TestMetric::Dfp => {
            "JSONExtractString(r.other_info, 'dfp')".to_string()
        }
        TestMetric::Oi => {
            let key = info.other_info_key.as_deref().unwrap_or("");
            format!("JSONExtractString(r.other_info, {})", quote_string(key))
        }
    };

    format!(
        "(SELECT {metric_expr} \
          FROM results AS r \
          INNER JOIN tests AS t ON r.test_group_key = t.test_group_key \
          WHERE (r.ssid = cs.ssid OR r.local_student_id = cs.student_id) \
            AND r.school_year = {year} \
            AND lower(t.test_name) = lower({test_name}) \
            AND lower(t.subject) = lower({subject}) \
            {window_clause} \
          LIMIT 1) AS `{alias}`",
        metric_expr = metric_expr,
        year = quote_string(&info.year),
        test_name = quote_string(&info.test_name),
        subject = quote_string(&info.subject),
        window_clause = window_clause,
        alias = info.col_name.replace('`', "``"),
    )
}

pub fn test_column_label(info: &TestColumnInfo) -> String {
    let mut label = format!(
        "{} {} ({}) {}",
        display_test_name(&info.test_name),
        display_test_name(&info.subject),
        info.year,
        info.metric.label()
    );
    if let Some(window) = &info.test_window {
        label.push('\n');
        label.push_str(window);
    }
    label
}
