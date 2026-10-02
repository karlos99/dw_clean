use rocket::http::Status;
use rocket::request::Request;
use rocket::response::{self, Responder, Response};
use rocket::serde::json::Json;
use serde::Serialize;
use std::io::Cursor;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ApiError {
    #[error("configuration error: {0}")]
    Config(String),
    #[error("bad request: {0}")]
    BadRequest(String),
    #[error("clickhouse error: {0}")]
    ClickHouse(String),
    #[error("internal error: {0}")]
    Internal(String),
}

impl ApiError {
    fn status(&self) -> Status {
        match self {
            Self::BadRequest(_) => Status::BadRequest,
            Self::Config(_) | Self::ClickHouse(_) | Self::Internal(_) => Status::InternalServerError,
        }
    }
}

#[derive(Serialize)]
struct ErrorBody<'a> {
    status: &'static str,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    detail: Option<&'a str>,
}

pub struct ApiErrorResponse(pub ApiError);

impl<'r> Responder<'r, 'static> for ApiErrorResponse {
    fn respond_to(self, _req: &'r Request<'_>) -> response::Result<'static> {
        let status = self.0.status();
        let body = ErrorBody {
            status: "error",
            message: self.0.to_string(),
            detail: None,
        };
        let json = serde_json::to_string(&body).unwrap_or_else(|_| {
            r#"{"status":"error","message":"serialization failed"}"#.into()
        });
        Response::build()
            .status(status)
            .header(rocket::http::ContentType::JSON)
            .sized_body(json.len(), Cursor::new(json))
            .ok()
    }
}

pub type ApiResult<T> = Result<T, ApiErrorResponse>;

impl From<ApiError> for ApiErrorResponse {
    fn from(value: ApiError) -> Self {
        Self(value)
    }
}

pub fn bad_request(message: impl Into<String>) -> ApiErrorResponse {
    ApiErrorResponse(ApiError::BadRequest(message.into()))
}

pub fn ok_json<T: Serialize>(value: T) -> ApiResult<Json<T>> {
    Ok(Json(value))
}
