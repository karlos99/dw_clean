use axum::extract::State;
use axum::Json;
use serde::{Deserialize, Serialize};
use tower_sessions::Session;

use crate::auth::{user_from_session, AuthError};
use crate::AppState;

#[derive(Deserialize)]
pub struct StudentQuery {
    pub school_year: Option<String>,
    pub search: Option<String>,
    pub limit: Option<i64>,
    pub offset: Option<i64>,
}

#[derive(Serialize, sqlx::FromRow)]
pub struct StudentRow {
    pub stu_annual_key: String,
    pub student_id: Option<String>,
    pub student_name: Option<String>,
    pub grade: Option<String>,
    pub school: Option<String>,
    pub school_year: Option<String>,
    pub status: Option<String>,
}

#[derive(Serialize)]
pub struct StudentQueryResponse {
    pub columns: Vec<&'static str>,
    pub rows: Vec<StudentRow>,
    pub total: i64,
    pub limit: i64,
    pub offset: i64,
}

pub async fn query_students(
    State(state): State<AppState>,
    session: Session,
    Json(body): Json<StudentQuery>,
) -> Result<Json<StudentQueryResponse>, AuthError> {
    let _user = user_from_session(&session, &state.db)
        .await?
        .ok_or(AuthError::Unauthorized)?;

    let limit = body.limit.unwrap_or(50).clamp(1, 500);
    let offset = body.offset.unwrap_or(0).max(0);
    let school_year = body.school_year.filter(|s| !s.is_empty());
    let search = body
        .search
        .filter(|s| !s.is_empty())
        .map(|s| format!("%{}%", s));

    // ponytail: simplified first-slice query; full filter/column compiler lands in phase 3
    let total = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT COUNT(*)::bigint
        FROM current_student
        WHERE ($1::text IS NULL OR school_year = $1)
          AND ($2::text IS NULL OR student_name ILIKE $2 OR student_id ILIKE $2)
        "#,
    )
    .bind(&school_year)
    .bind(&search)
    .fetch_one(&state.db)
    .await?;

    let rows = sqlx::query_as::<_, StudentRow>(
        r#"
        SELECT stu_annual_key, student_id, student_name, grade, school, school_year, status
        FROM current_student
        WHERE ($1::text IS NULL OR school_year = $1)
          AND ($2::text IS NULL OR student_name ILIKE $2 OR student_id ILIKE $2)
        ORDER BY student_name NULLS LAST
        LIMIT $3 OFFSET $4
        "#,
    )
    .bind(&school_year)
    .bind(&search)
    .bind(limit)
    .bind(offset)
    .fetch_all(&state.db)
    .await?;

    Ok(Json(StudentQueryResponse {
        columns: vec![
            "stu_annual_key",
            "student_id",
            "student_name",
            "grade",
            "school",
            "school_year",
            "status",
        ],
        rows,
        total,
        limit,
        offset,
    }))
}
