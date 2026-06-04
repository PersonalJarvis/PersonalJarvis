---
plugin_id: supabase
keywords: supabase, datenbank, database, postgres, postgresql, sql, tabelle, table, query, abfrage, edge function, edge functions, projekt, project, migration, schema
---
Use supabase/* tools to query and manage Supabase projects: run SQL, list tables,
apply migrations, deploy Edge Functions, and inspect logs or advisor recommendations.
- Prefer `execute_sql` for data reads; use `apply_migration` for schema changes.
- Always call `list_tables` first when the schema is unknown.
- Read/act directly; state what you did (rows returned, migration applied) afterwards.
- The default install is read-only; write operations require toggling off `--read-only`.
