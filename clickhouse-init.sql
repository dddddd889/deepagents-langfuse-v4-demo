-- 由 ClickHouse 管理员在外部单机实例执行；密码需与 .env 完全一致。
-- 适用于全新部署，库中表由 Langfuse 启动时自动创建。
CREATE DATABASE IF NOT EXISTS langfuse;
CREATE USER IF NOT EXISTS langfuse IDENTIFIED WITH sha256_password BY 'CHANGE_ME_CLICKHOUSE_PASSWORD';

-- 将管理权限限定在 Langfuse 专用数据库，用于建表、改表和读写。
GRANT ALL ON langfuse.* TO langfuse;

-- Langfuse v4 的后台任务需要读取下列系统信息。
GRANT SELECT(database, table, name, partition, partition_id, active, rows) ON system.parts TO langfuse;
GRANT SELECT(database, table, is_done) ON system.mutations TO langfuse;
GRANT SELECT(database, name, engine) ON system.tables TO langfuse;
GRANT SELECT ON system.processes TO langfuse;
GRANT SELECT ON system.query_log* TO langfuse;

-- 确认版本不低于 25.12，且时区为 UTC。
SELECT version(), timezone();
