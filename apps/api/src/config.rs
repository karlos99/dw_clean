use anyhow::{bail, Context};

#[derive(Clone, Debug)]
pub struct Config {
    pub database_url: String,
    pub port: u16,
    pub frontend_url: String,
    pub api_public_url: String,
    pub google_client_id: Option<String>,
    pub google_client_secret: Option<String>,
    pub cookie_secure: bool,
    pub allow_dev_login: bool,
}

impl Config {
    pub fn from_env() -> anyhow::Result<Self> {
        let database_url = std::env::var("DATABASE_URL").or_else(|_| {
            let host = std::env::var("POSTGRES_HOST").unwrap_or_else(|_| "localhost".into());
            let port = std::env::var("POSTGRES_PORT").unwrap_or_else(|_| "5432".into());
            let user = std::env::var("POSTGRES_USER").context("POSTGRES_USER or DATABASE_URL")?;
            let pass =
                std::env::var("POSTGRES_PASSWORD").context("POSTGRES_PASSWORD or DATABASE_URL")?;
            let db = std::env::var("POSTGRES_DB").context("POSTGRES_DB or DATABASE_URL")?;
            Ok::<_, anyhow::Error>(format!("postgres://{user}:{pass}@{host}:{port}/{db}"))
        })?;

        let google_client_id = std::env::var("GOOGLE_CLIENT_ID").ok().filter(|s| !s.is_empty());
        let google_client_secret = std::env::var("GOOGLE_CLIENT_SECRET")
            .ok()
            .filter(|s| !s.is_empty());

        if google_client_id.is_some() != google_client_secret.is_some() {
            bail!("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must both be set or both unset");
        }

        Ok(Self {
            database_url,
            port: std::env::var("API_PORT")
                .ok()
                .and_then(|p| p.parse().ok())
                .unwrap_or(8080),
            frontend_url: std::env::var("FRONTEND_URL")
                .unwrap_or_else(|_| "http://localhost:5173".into()),
            api_public_url: std::env::var("API_PUBLIC_URL")
                .unwrap_or_else(|_| "http://localhost:8080".into()),
            google_client_id,
            google_client_secret,
            cookie_secure: std::env::var("COOKIE_SECURE")
                .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
                .unwrap_or(false),
            allow_dev_login: std::env::var("ALLOW_DEV_LOGIN")
                .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
                .unwrap_or(cfg!(debug_assertions)),
        })
    }

    pub fn google_configured(&self) -> bool {
        self.google_client_id.is_some()
    }
}
