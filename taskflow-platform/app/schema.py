"""Schema for both dialects. Real deployments would use migrations (Alembic/Flyway); see README roadmap."""

TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id {pk},
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    created_at {ts} NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id {pk},
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owner_id {int} NOT NULL REFERENCES users(id),
    created_at {ts} NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id {int} NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id {int} NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS tasks (
    id {pk},
    project_id {int} NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT NOT NULL DEFAULT 'medium',
    assignee_id {int} REFERENCES users(id) ON DELETE SET NULL,
    creator_id {int} NOT NULL REFERENCES users(id),
    due_date {date},
    created_at {ts} NOT NULL,
    updated_at {ts} NOT NULL,
    completed_at {ts}
);
CREATE TABLE IF NOT EXISTS events (
    id {pk},
    project_id {int} NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at {ts} NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks (project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_project_assignee ON tasks (project_id, assignee_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project_created ON tasks (project_id, id);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks (assignee_id);
CREATE INDEX IF NOT EXISTS idx_members_user ON project_members (user_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events (created_at);
"""

TYPES = {
    "sqlite": {"pk": "INTEGER PRIMARY KEY AUTOINCREMENT", "int": "INTEGER", "ts": "TEXT", "date": "TEXT"},
    "postgres": {"pk": "BIGSERIAL PRIMARY KEY", "int": "BIGINT", "ts": "TIMESTAMPTZ", "date": "DATE"},
}


def statements(dialect):
    sql = TABLES.format(**TYPES[dialect])
    return [s.strip() for s in sql.split(";") if s.strip()]


def init_schema(database):
    with database.session() as conn:
        if database.dialect == "postgres":
            conn.execute("SELECT pg_advisory_lock(727274)")  # several containers may start at once
        try:
            for stmt in statements(database.dialect):
                conn.execute(stmt)
            conn.commit()
        finally:
            if database.dialect == "postgres":
                conn.execute("SELECT pg_advisory_unlock(727274)")
                conn.commit()
