-- Minimal schema for local smoke tests (subset of Django tables the API uses).
CREATE TABLE IF NOT EXISTS users_role (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS users_user (
    id BIGSERIAL PRIMARY KEY,
    password VARCHAR(128) NOT NULL DEFAULT '',
    last_login TIMESTAMPTZ,
    is_superuser BOOLEAN NOT NULL DEFAULT FALSE,
    username VARCHAR(150) UNIQUE NOT NULL,
    first_name VARCHAR(150) NOT NULL DEFAULT '',
    last_name VARCHAR(150) NOT NULL DEFAULT '',
    is_staff BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    date_joined TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    name VARCHAR(255) NOT NULL DEFAULT '',
    job_title VARCHAR(255) NOT NULL DEFAULT '',
    staff_id VARCHAR(50) UNIQUE,
    schools JSONB NOT NULL DEFAULT '[]'::jsonb,
    grade_levels JSONB NOT NULL DEFAULT '[]'::jsonb,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    email VARCHAR(254) UNIQUE NOT NULL,
    avatar VARCHAR(500)
);

CREATE TABLE IF NOT EXISTS users_user_roles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users_user(id) ON DELETE CASCADE,
    role_id BIGINT NOT NULL REFERENCES users_role(id) ON DELETE CASCADE,
    UNIQUE (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS users_page (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    app_reference VARCHAR(100) NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    url_path VARCHAR(500) NOT NULL,
    icon VARCHAR(100) NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    required_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_public BOOLEAN NOT NULL DEFAULT FALSE,
    show_in_menu BOOLEAN NOT NULL DEFAULT TRUE,
    menu_order INTEGER NOT NULL DEFAULT 0,
    menu_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    parent_page_id BIGINT REFERENCES users_page(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_id BIGINT REFERENCES users_user(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS current_student (
    stu_annual_key VARCHAR(32) PRIMARY KEY,
    ssid VARCHAR(20),
    student_id VARCHAR(20),
    student_name VARCHAR(255),
    grade VARCHAR(50),
    school VARCHAR(255),
    school_state_id VARCHAR(20),
    gender VARCHAR(20),
    race VARCHAR(50),
    status VARCHAR(20),
    school_year VARCHAR(20)
);

INSERT INTO users_role (name, description)
VALUES ('Project Admin', 'Local smoke-test admin')
ON CONFLICT (name) DO NOTHING;

INSERT INTO users_user (username, email, name, is_active, is_superuser, is_staff)
VALUES ('devadmin', 'dev@example.com', 'Dev Admin', TRUE, TRUE, TRUE)
ON CONFLICT (email) DO NOTHING;

INSERT INTO users_user_roles (user_id, role_id)
SELECT u.id, r.id
FROM users_user u, users_role r
WHERE u.email = 'dev@example.com' AND r.name = 'Project Admin'
ON CONFLICT DO NOTHING;

INSERT INTO users_page (name, slug, url_path, icon, status, show_in_menu, menu_order, required_roles)
VALUES
  ('Home', 'home', '/', 'home', 'active', TRUE, 0, '[]'::jsonb),
  ('Data Exploration', 'data-exploration', '/exploration', 'table', 'active', TRUE, 10, '[]'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO current_student (stu_annual_key, ssid, student_id, student_name, grade, school, school_state_id, status, school_year)
VALUES
  ('a1', '1001', 'S1001', 'Ada Lovelace', '08', 'Demo Middle', '100', 'Active', '2025-2026'),
  ('a2', '1002', 'S1002', 'Alan Turing', '09', 'Demo High', '100', 'Active', '2025-2026'),
  ('a3', '1003', 'S1003', 'Grace Hopper', '08', 'Demo Middle', '100', 'Active', '2024-2025')
ON CONFLICT (stu_annual_key) DO NOTHING;
