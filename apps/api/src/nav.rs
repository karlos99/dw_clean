use axum::extract::State;
use axum::Json;
use serde::Serialize;
use tower_sessions::Session;

use crate::auth::{user_from_session, AuthError};
use crate::AppState;

#[derive(Debug, Serialize, sqlx::FromRow)]
pub struct NavItem {
    pub id: i64,
    pub name: String,
    pub slug: String,
    pub url_path: String,
    pub icon: String,
    pub menu_order: i32,
    pub parent_page_id: Option<i64>,
    pub required_roles: serde_json::Value,
    pub is_public: bool,
}

fn role_allowed(required: &serde_json::Value, user_roles: &[String], is_superuser: bool) -> bool {
    if is_superuser {
        return true;
    }
    let Some(arr) = required.as_array() else {
        return true;
    };
    if arr.is_empty() {
        return true;
    }
    arr.iter().any(|r| {
        r.as_str()
            .map(|name| user_roles.iter().any(|ur| ur == name))
            .unwrap_or(false)
    })
}

pub async fn nav(
    State(state): State<AppState>,
    session: Session,
) -> Result<Json<Vec<NavItem>>, AuthError> {
    let user = user_from_session(&session, &state.db)
        .await?
        .ok_or(AuthError::Unauthorized)?;

    let rows = sqlx::query_as::<_, NavItem>(
        r#"
        SELECT id, name, slug, url_path, COALESCE(icon, '') AS icon,
               menu_order, parent_page_id,
               COALESCE(required_roles, '[]'::jsonb) AS required_roles,
               is_public
        FROM users_page
        WHERE status = 'active' AND show_in_menu = true
        ORDER BY menu_order, name
        "#,
    )
    .fetch_all(&state.db)
    .await?;

    let filtered = rows
        .into_iter()
        .filter(|p| p.is_public || role_allowed(&p.required_roles, &user.roles, user.is_superuser))
        .collect();

    Ok(Json(filtered))
}
