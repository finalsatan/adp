#!/usr/bin/env bash
# 数据库初始化：建迁移账号和应用账号，按 ADP_DATABASES 建库、扩展和 kb_index schema。
#
# 容器首次初始化时由 docker-entrypoint-initdb.d 执行；改了本脚本要删数据卷重来。
# 也可手动调用（供 make test-db-reset 使用）：
#   10-adp.sh reset <db>
set -euo pipefail

PGUSER="${POSTGRES_USER:-postgres}"
PGDATABASE="${POSTGRES_DB:-postgres}"

: "${ADP_MIGRATOR_PASSWORD:?需要设置 ADP_MIGRATOR_PASSWORD}"
: "${ADP_APP_PASSWORD:?需要设置 ADP_APP_PASSWORD}"

psql_do() {
    psql -v ON_ERROR_STOP=1 --no-password --no-psqlrc --username "$PGUSER" "$@"
}

# 角色不存在就建，存在就更新密码
ensure_role() {
    local role="$1"
    local password="$2"
    if [ "$(psql_do -d "$PGDATABASE" -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$role'")" = "1" ]; then
        psql_do -d "$PGDATABASE" -v pw="$password" <<SQL
ALTER ROLE $role WITH LOGIN PASSWORD :'pw';
SQL
    else
        psql_do -d "$PGDATABASE" -v pw="$password" <<SQL
CREATE ROLE $role WITH LOGIN PASSWORD :'pw';
SQL
    fi
}

ensure_roles() {
    ensure_role adp_migrator "$ADP_MIGRATOR_PASSWORD"
    ensure_role adp_app "$ADP_APP_PASSWORD"
}

# 建库（不存在时）并设置扩展、schema 和权限
setup_database() {
    local db="$1"
    if [ "$(psql_do -d "$PGDATABASE" -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'")" != "1" ]; then
        psql_do -d "$PGDATABASE" -c "CREATE DATABASE \"$db\" OWNER adp_migrator"
    fi

    psql_do -d "$db" -v db="$db" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;
CREATE SCHEMA IF NOT EXISTS kb_index AUTHORIZATION adp_migrator;
GRANT USAGE, CREATE ON SCHEMA kb_index TO adp_app;
GRANT CONNECT ON DATABASE :"db" TO adp_app;
GRANT USAGE ON SCHEMA public TO adp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE adp_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO adp_app;
ALTER DEFAULT PRIVILEGES FOR ROLE adp_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO adp_app;
SQL
}

reset_database() {
    local db="$1"
    psql_do -d "$PGDATABASE" -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)"
    setup_database "$db"
}

main() {
    if [ "${1:-}" = "reset" ]; then
        if [ -z "${2:-}" ]; then
            echo "用法: $0 reset <db>" >&2
            exit 1
        fi
        ensure_roles
        reset_database "$2"
        return
    fi

    ensure_roles
    for db in ${ADP_DATABASES:-}; do
        setup_database "$db"
    done
}

main "$@"
