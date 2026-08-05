use crate::error::ApiError;
use std::env;

#[derive(Debug, Clone)]
pub struct Config {
    pub clickhouse_url: String,
    pub clickhouse_db: String,
    pub clickhouse_user: String,
    pub clickhouse_password: String,
    pub host: String,
    pub port: u16,
    pub default_school_year: String,
    pub default_status: String,
    pub default_page_limit: u32,
    pub max_page_limit: u32,
}

impl Config {
    pub fn from_env() -> Result<Self, ApiError> {
        Ok(Self {
            clickhouse_url: env::var("CLICKHOUSE_URL")
                .map_err(|_| ApiError::Config("CLICKHOUSE_URL is required".into()))?,
            clickhouse_db: env::var("CLICKHOUSE_DB")
                .map_err(|_| ApiError::Config("CLICKHOUSE_DB is required".into()))?,
            clickhouse_user: env::var("CLICKHOUSE_USER").unwrap_or_else(|_| "default".into()),
            clickhouse_password: env::var("CLICKHOUSE_PASSWORD").unwrap_or_default(),
            host: env::var("HOST").unwrap_or_else(|_| "0.0.0.0".into()),
            port: env::var("PORT")
                .ok()
                .and_then(|p| p.parse().ok())
                .unwrap_or(8080),
            default_school_year: env::var("DEFAULT_SCHOOL_YEAR")
                .unwrap_or_else(|_| "2025-2026".into()),
            default_status: env::var("DEFAULT_STATUS").unwrap_or_else(|_| "Active".into()),
            default_page_limit: env::var("DEFAULT_PAGE_LIMIT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(50),
            max_page_limit: env::var("MAX_PAGE_LIMIT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(500),
        })
    }
}
