# API map (Django → Axum)

Partial map for the implemented slice. Expand per phase.

| Django | Axum | Status |
|--------|------|--------|
| health (implicit) | `GET /api/health` | done |
| `/accounts/google/login/` | `GET /api/auth/google/start` | done |
| allauth callback | `GET /api/auth/google/callback` | done |
| logout | `POST /api/auth/logout` | done |
| session user (templates) | `GET /api/auth/me` | done |
| `users/switch-role/` | `POST /api/auth/switch-role` | done |
| nav context processor | `GET /api/nav` | done |
| `dataexploration` load_students | `POST /api/v1/exploration/students/query` | simplified |
| dataviz / placement / desi / … | TBD | phase 4–6 |
