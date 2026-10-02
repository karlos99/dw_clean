use crate::config::Config;
use crate::error::ApiError;
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;

#[derive(Clone)]
pub struct ClickHouseClient {
    http: reqwest::Client,
    config: Config,
}

#[derive(Debug, Deserialize)]
struct JsonResponse {
    data: Vec<HashMap<String, Value>>,
    rows: Option<u64>,
}

impl ClickHouseClient {
    pub fn new(config: Config) -> Self {
        Self {
            http: reqwest::Client::new(),
            config,
        }
    }

    pub fn config(&self) -> &Config {
        &self.config
    }

    pub async fn ping(&self) -> Result<(), ApiError> {
        self.query_scalar::<u8>("SELECT 1").await?;
        Ok(())
    }

    pub async fn query_rows(&self, sql: &str) -> Result<Vec<HashMap<String, Value>>, ApiError> {
        let body = self.execute_json(sql).await?;
        Ok(body.data)
    }

    pub async fn query_scalar<T>(&self, sql: &str) -> Result<T, ApiError>
    where
        T: serde::de::DeserializeOwned,
    {
        let rows = self.query_rows(sql).await?;
        let first = rows
            .into_iter()
            .next()
            .ok_or_else(|| ApiError::ClickHouse("empty scalar result".into()))?;
        let value = first
            .values()
            .next()
            .cloned()
            .ok_or_else(|| ApiError::ClickHouse("empty scalar row".into()))?;
        serde_json::from_value(value)
            .map_err(|e| ApiError::ClickHouse(format!("scalar decode failed: {e}")))
    }

    pub async fn query_count(&self, sql: &str) -> Result<u64, ApiError> {
        let body = self.execute_json(sql).await?;
        if let Some(rows) = body.rows {
            return Ok(rows);
        }
        if let Some(first) = body.data.first() {
            if let Some(value) = first.values().next() {
                if let Some(n) = value.as_u64() {
                    return Ok(n);
                }
                if let Some(n) = value.as_i64() {
                    return Ok(n.max(0) as u64);
                }
                if let Some(s) = value.as_str() {
                    if let Ok(n) = s.parse::<u64>() {
                        return Ok(n);
                    }
                }
            }
        }
        Ok(body.data.len() as u64)
    }

    async fn execute_json(&self, sql: &str) -> Result<JsonResponse, ApiError> {
        let sql = sql.trim();
        let sql = if sql.to_ascii_uppercase().contains("FORMAT JSON") {
            sql.to_string()
        } else {
            format!("{sql} FORMAT JSON")
        };

        let url = format!(
            "{}/?database={}",
            self.config.clickhouse_url.trim_end_matches('/'),
            urlencoding(&self.config.clickhouse_db)
        );

        let mut request = self
            .http
            .post(&url)
            .header("Content-Type", "text/plain; charset=utf-8")
            .body(sql);

        if !self.config.clickhouse_user.is_empty() {
            request = request.basic_auth(
                &self.config.clickhouse_user,
                Some(self.config.clickhouse_password.clone()),
            );
        }

        let response = request
            .send()
            .await
            .map_err(|e| ApiError::ClickHouse(format!("request failed: {e}")))?;

        let status = response.status();
        let text = response
            .text()
            .await
            .map_err(|e| ApiError::ClickHouse(format!("read body failed: {e}")))?;

        if !status.is_success() {
            return Err(ApiError::ClickHouse(format!(
                "clickhouse returned {status}: {text}"
            )));
        }

        serde_json::from_str(&text)
            .map_err(|e| ApiError::ClickHouse(format!("json decode failed: {e}; body={text}")))
    }
}

pub fn escape_string(value: &str) -> String {
    value.replace('\\', "\\\\").replace('\'', "\\'")
}

pub fn quote_string(value: &str) -> String {
    format!("'{}'", escape_string(value))
}

pub fn quote_like(value: &str) -> String {
    quote_string(&format!("%{value}%"))
}

fn urlencoding(value: &str) -> String {
    value
        .chars()
        .map(|c| match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            _ => format!("%{:02X}", c as u8),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escape_quotes() {
        assert_eq!(escape_string("O'Brien"), "O\\'Brien");
        assert_eq!(quote_string("abc"), "'abc'");
    }
}
