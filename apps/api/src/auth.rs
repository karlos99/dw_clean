use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Redirect, Response};
use axum::Json;
use oauth2::basic::BasicClient;
use oauth2::{
    AuthUrl, AuthorizationCode, ClientId, ClientSecret, CsrfToken, RedirectUrl, Scope,
    TokenResponse, TokenUrl,
};
use serde::{Deserialize, Serialize};
use tower_sessions::Session;

use crate::AppState;

const SESSION_USER_ID: &str = "user_id";
const SESSION_ACTIVE_ROLE: &str = "active_role";
const SESSION_ACTIVE_SCHOOL: &str = "active_school_id";
const SESSION_OAUTH_CSRF: &str = "oauth_csrf";

#[derive(Debug, Clone, Serialize)]
pub struct AuthUser {
    pub id: i64,
    pub email: String,
    pub name: String,
    pub avatar: Option<String>,
    pub roles: Vec<String>,
    pub schools: serde_json::Value,
    pub grade_levels: serde_json::Value,
    pub active_role: Option<String>,
    pub active_school_id: Option<String>,
    pub is_superuser: bool,
}

#[derive(Debug, sqlx::FromRow)]
struct UserRow {
    id: i64,
    email: String,
    name: String,
    avatar: Option<String>,
    schools: serde_json::Value,
    grade_levels: serde_json::Value,
    is_superuser: bool,
    is_active: bool,
}

pub async fn load_user(db: &sqlx::PgPool, user_id: i64) -> Result<Option<AuthUser>, sqlx::Error> {
    let row = sqlx::query_as::<_, UserRow>(
        r#"
        SELECT id, email, COALESCE(name, '') AS name, avatar,
               COALESCE(schools, '[]'::jsonb) AS schools,
               COALESCE(grade_levels, '[]'::jsonb) AS grade_levels,
               is_superuser, is_active
        FROM users_user
        WHERE id = $1
        "#,
    )
    .bind(user_id)
    .fetch_optional(db)
    .await?;

    let Some(row) = row else {
        return Ok(None);
    };
    if !row.is_active {
        return Ok(None);
    }

    let roles = sqlx::query_scalar::<_, String>(
        r#"
        SELECT r.name
        FROM users_role r
        INNER JOIN users_user_roles ur ON ur.role_id = r.id
        WHERE ur.user_id = $1
        ORDER BY r.name
        "#,
    )
    .bind(user_id)
    .fetch_all(db)
    .await?;

    Ok(Some(AuthUser {
        id: row.id,
        email: row.email,
        name: row.name,
        avatar: row.avatar,
        roles,
        schools: row.schools,
        grade_levels: row.grade_levels,
        active_role: None,
        active_school_id: None,
        is_superuser: row.is_superuser,
    }))
}

pub async fn user_from_session(
    session: &Session,
    db: &sqlx::PgPool,
) -> Result<Option<AuthUser>, SessionError> {
    let Some(user_id) = session.get::<i64>(SESSION_USER_ID).await? else {
        return Ok(None);
    };
    let mut user = load_user(db, user_id)
        .await?
        .ok_or(SessionError::Unauthorized)?;
    user.active_role = session.get(SESSION_ACTIVE_ROLE).await?;
    user.active_school_id = session.get(SESSION_ACTIVE_SCHOOL).await?;
    if user.active_role.is_none() {
        user.active_role = user.roles.first().cloned();
    }
    Ok(Some(user))
}

fn google_client(state: &AppState) -> Result<BasicClient, AuthError> {
    let id = state
        .config
        .google_client_id
        .clone()
        .ok_or(AuthError::GoogleNotConfigured)?;
    let secret = state
        .config
        .google_client_secret
        .clone()
        .ok_or(AuthError::GoogleNotConfigured)?;
    let redirect = format!(
        "{}/api/auth/google/callback",
        state.config.api_public_url.trim_end_matches('/')
    );
    Ok(BasicClient::new(
        ClientId::new(id),
        Some(ClientSecret::new(secret)),
        AuthUrl::new("https://accounts.google.com/o/oauth2/v2/auth".into())
            .map_err(|_| AuthError::Internal)?,
        Some(
            TokenUrl::new("https://oauth2.googleapis.com/token".into())
                .map_err(|_| AuthError::Internal)?,
        ),
    )
    .set_redirect_uri(RedirectUrl::new(redirect).map_err(|_| AuthError::Internal)?))
}

pub async fn google_start(
    State(state): State<AppState>,
    session: Session,
) -> Result<Response, AuthError> {
    let client = google_client(&state)?;
    let (auth_url, csrf) = client
        .authorize_url(CsrfToken::new_random)
        .add_scope(Scope::new("openid".into()))
        .add_scope(Scope::new("email".into()))
        .add_scope(Scope::new("profile".into()))
        .add_extra_param("access_type", "online")
        .add_extra_param("prompt", "select_account")
        .url();
    session.insert(SESSION_OAUTH_CSRF, csrf.secret().clone()).await?;
    Ok(Redirect::temporary(auth_url.as_str()).into_response())
}

#[derive(Deserialize)]
pub struct OAuthCallback {
    code: String,
    state: String,
}

#[derive(Deserialize)]
struct GoogleUserInfo {
    email: Option<String>,
    name: Option<String>,
    picture: Option<String>,
}

pub async fn google_callback(
    State(state): State<AppState>,
    session: Session,
    Query(q): Query<OAuthCallback>,
) -> Result<Response, AuthError> {
    let expected = session
        .get::<String>(SESSION_OAUTH_CSRF)
        .await?
        .ok_or(AuthError::Unauthorized)?;
    if expected != q.state {
        return Err(AuthError::Unauthorized);
    }
    session.remove::<String>(SESSION_OAUTH_CSRF).await?;

    let client = google_client(&state)?;
    let token = client
        .exchange_code(AuthorizationCode::new(q.code))
        .request_async(oauth2::reqwest::async_http_client)
        .await
        .map_err(|e| {
            tracing::error!("token exchange failed: {e}");
            AuthError::Unauthorized
        })?;

    let info: GoogleUserInfo = state
        .http
        .get("https://www.googleapis.com/oauth2/v3/userinfo")
        .bearer_auth(token.access_token().secret())
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;

    let email = info
        .email
        .ok_or(AuthError::Unauthorized)?
        .to_lowercase();

    // Match allauth adapter: link by existing email; update name/avatar.
    let user_id = sqlx::query_scalar::<_, i64>(
        r#"
        UPDATE users_user
        SET name = COALESCE(NULLIF($2, ''), name),
            avatar = COALESCE($3, avatar),
            last_login = NOW()
        WHERE lower(email) = $1 AND is_active
        RETURNING id
        "#,
    )
    .bind(&email)
    .bind(info.name.unwrap_or_default())
    .bind(info.picture)
    .fetch_optional(&state.db)
    .await?
    .ok_or(AuthError::UserNotProvisioned)?;

    establish_session(&session, &state.db, user_id).await?;
    Ok(Redirect::temporary(&state.config.frontend_url).into_response())
}

async fn establish_session(
    session: &Session,
    db: &sqlx::PgPool,
    user_id: i64,
) -> Result<(), AuthError> {
    let user = load_user(db, user_id)
        .await?
        .ok_or(AuthError::Unauthorized)?;
    session.cycle_id().await?;
    session.insert(SESSION_USER_ID, user_id).await?;
    if let Some(role) = user.roles.first() {
        session.insert(SESSION_ACTIVE_ROLE, role.clone()).await?;
    }
    Ok(())
}

pub async fn logout(session: Session) -> Result<StatusCode, AuthError> {
    session.flush().await?;
    Ok(StatusCode::NO_CONTENT)
}

pub async fn me(
    State(state): State<AppState>,
    session: Session,
) -> Result<Json<AuthUser>, AuthError> {
    let user = user_from_session(&session, &state.db)
        .await?
        .ok_or(AuthError::Unauthorized)?;
    Ok(Json(user))
}

#[derive(Deserialize)]
pub struct DevLoginBody {
    pub email: String,
}

pub async fn dev_login(
    State(state): State<AppState>,
    session: Session,
    Json(body): Json<DevLoginBody>,
) -> Result<Json<AuthUser>, AuthError> {
    if !state.config.allow_dev_login {
        return Err(AuthError::Forbidden);
    }
    let email = body.email.to_lowercase();
    let user_id = sqlx::query_scalar::<_, i64>(
        "SELECT id FROM users_user WHERE lower(email) = $1 AND is_active",
    )
    .bind(&email)
    .fetch_optional(&state.db)
    .await?
    .ok_or(AuthError::UserNotProvisioned)?;

    establish_session(&session, &state.db, user_id).await?;
    let user = user_from_session(&session, &state.db)
        .await?
        .ok_or(AuthError::Unauthorized)?;
    Ok(Json(user))
}

#[derive(Deserialize)]
pub struct SwitchRoleBody {
    pub role: Option<String>,
    pub school_id: Option<String>,
}

pub async fn switch_role(
    State(state): State<AppState>,
    session: Session,
    Json(body): Json<SwitchRoleBody>,
) -> Result<Json<AuthUser>, AuthError> {
    let user = user_from_session(&session, &state.db)
        .await?
        .ok_or(AuthError::Unauthorized)?;

    if let Some(role) = body.role {
        if !user.roles.iter().any(|r| r == &role) && !user.is_superuser {
            return Err(AuthError::Forbidden);
        }
        session.insert(SESSION_ACTIVE_ROLE, role).await?;
    }
    if let Some(school_id) = body.school_id {
        session
            .insert(SESSION_ACTIVE_SCHOOL, school_id)
            .await?;
    }

    let user = user_from_session(&session, &state.db)
        .await?
        .ok_or(AuthError::Unauthorized)?;
    Ok(Json(user))
}

#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("unauthorized")]
    Unauthorized,
    #[error("forbidden")]
    Forbidden,
    #[error("user not provisioned")]
    UserNotProvisioned,
    #[error("google oauth not configured")]
    GoogleNotConfigured,
    #[error(transparent)]
    Session(#[from] tower_sessions::session::Error),
    #[error(transparent)]
    Db(#[from] sqlx::Error),
    #[error(transparent)]
    Http(#[from] reqwest::Error),
    #[error("internal")]
    Internal,
}

pub type SessionError = AuthError;

impl IntoResponse for AuthError {
    fn into_response(self) -> Response {
        let status = match &self {
            AuthError::Unauthorized => StatusCode::UNAUTHORIZED,
            AuthError::Forbidden | AuthError::GoogleNotConfigured => StatusCode::FORBIDDEN,
            AuthError::UserNotProvisioned => StatusCode::FORBIDDEN,
            AuthError::Session(_) | AuthError::Db(_) | AuthError::Http(_) | AuthError::Internal => {
                StatusCode::INTERNAL_SERVER_ERROR
            }
        };
        let body = Json(serde_json::json!({ "error": self.to_string() }));
        (status, body).into_response()
    }
}
