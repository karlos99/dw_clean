#[macro_use]
extern crate rocket;

mod clickhouse;
mod columns;
mod config;
mod error;
mod models;
mod query;
mod routes;

use clickhouse::ClickHouseClient;
use config::Config;
use error::ApiErrorResponse;
use rocket::get;
use rocket::serde::json::Json;
use rocket::State;
use serde::Serialize;
use tracing_subscriber::EnvFilter;

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    clickhouse: &'static str,
}

#[get("/health")]
async fn health(client: &State<ClickHouseClient>) -> Result<Json<HealthResponse>, ApiErrorResponse> {
    client.ping().await.map_err(ApiErrorResponse)?;
    Ok(Json(HealthResponse {
        status: "ok",
        clickhouse: "ok",
    }))
}

#[get("/")]
fn root() -> &'static str {
    "data-explorer-api"
}

#[launch]
fn rocket() -> _ {
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .init();

    let config = Config::from_env().expect("invalid configuration");
    let host = config.host.clone();
    let port = config.port;
    let client = ClickHouseClient::new(config);

    rocket::build()
        .manage(client)
        .configure(
            rocket::Config::figment()
                .merge(("address", host))
                .merge(("port", port)),
        )
        .mount("/", routes![root, health])
        .mount("/api", routes![routes::students::get_students])
}
