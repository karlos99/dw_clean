mod auth;
mod config;
mod exploration;
mod nav;

use std::net::SocketAddr;
use std::sync::Arc;

use axum::routing::{get, post};
use axum::Router;
use tower_http::cors::{AllowOrigin, CorsLayer};
use tower_http::trace::TraceLayer;
use tower_sessions::{MemoryStore, SessionManagerLayer};
use tracing_subscriber::EnvFilter;

use crate::config::Config;

pub type Db = sqlx::PgPool;

#[derive(Clone)]
pub struct AppState {
    pub db: Db,
    pub config: Arc<Config>,
    pub http: reqwest::Client,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("dw_api=info".parse()?))
        .init();

    let config = Arc::new(Config::from_env()?);
    let db = sqlx::postgres::PgPoolOptions::new()
        .max_connections(10)
        .connect(&config.database_url)
        .await?;

    let state = AppState {
        db,
        config: config.clone(),
        http: reqwest::Client::new(),
    };

    let session_store = MemoryStore::default();
    // ponytail: memory sessions; swap to Redis store when REDIS_URL is required across replicas
    let session_layer = SessionManagerLayer::new(session_store)
        .with_secure(config.cookie_secure)
        .with_same_site(tower_sessions::cookie::SameSite::Lax)
        .with_name("dw_session");

    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::exact(config.frontend_url.parse()?))
        .allow_credentials(true)
        .allow_methods([
            axum::http::Method::GET,
            axum::http::Method::POST,
            axum::http::Method::OPTIONS,
        ])
        .allow_headers([
            axum::http::header::CONTENT_TYPE,
            axum::http::header::ACCEPT,
        ]);

    let app = Router::new()
        .route("/api/health", get(health))
        .route("/api/auth/google/start", get(auth::google_start))
        .route("/api/auth/google/callback", get(auth::google_callback))
        .route("/api/auth/logout", post(auth::logout))
        .route("/api/auth/me", get(auth::me))
        .route("/api/auth/dev-login", post(auth::dev_login))
        .route("/api/auth/switch-role", post(auth::switch_role))
        .route("/api/nav", get(nav::nav))
        .route(
            "/api/v1/exploration/students/query",
            post(exploration::query_students),
        )
        .with_state(state)
        .layer(session_layer)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr = SocketAddr::from(([0, 0, 0, 0], config.port));
    tracing::info!("listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn health(
    axum::extract::State(state): axum::extract::State<AppState>,
) -> Result<axum::Json<serde_json::Value>, axum::http::StatusCode> {
    sqlx::query_scalar::<_, i32>("SELECT 1")
        .fetch_one(&state.db)
        .await
        .map_err(|_| axum::http::StatusCode::SERVICE_UNAVAILABLE)?;
    Ok(axum::Json(serde_json::json!({
        "status": "ok",
        "service": "dw_api",
    })))
}
