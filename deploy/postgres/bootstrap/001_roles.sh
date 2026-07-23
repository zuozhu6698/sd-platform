#!/bin/sh
set -eu

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=database_name="$POSTGRES_DB" \
  --set=app_user="$SD_APP_DB_USER" \
  --set=app_password="$SD_APP_DB_PASSWORD" \
  --set=bi_user="$BI_READER_USER" \
  --set=bi_password="$BI_READER_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'bi_user', :'bi_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'bi_user') \gexec

SELECT format('GRANT CONNECT, CREATE ON DATABASE %I TO %I', :'database_name', :'app_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'database_name', :'bi_user') \gexec
SELECT format('CREATE SCHEMA IF NOT EXISTS sd_app AUTHORIZATION %I', :'app_user') \gexec
SELECT format('CREATE SCHEMA IF NOT EXISTS bi AUTHORIZATION %I', :'app_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA bi TO %I', :'bi_user') \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA bi GRANT SELECT ON TABLES TO %I',
  :'app_user',
  :'bi_user'
) \gexec
SQL
