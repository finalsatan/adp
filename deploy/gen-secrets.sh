#!/usr/bin/env bash
# 生成生产部署的 deploy/.env：随机的数据库密码和 JWT_SECRET。
# 已存在 .env 时拒绝覆盖，避免把现有部署的密码冲掉。
set -euo pipefail

cd "$(dirname "$0")"

if [ -e .env ]; then
    echo "deploy/.env 已存在，拒绝覆盖。要重新生成请先备份并删除它。" >&2
    exit 1
fi

# 用十六进制，避免密码里的特殊字符在连接串里需要转义
gen_password() {
    openssl rand -hex 24
}

POSTGRES_PASSWORD="$(gen_password)"
ADP_MIGRATOR_PASSWORD="$(gen_password)"
ADP_APP_PASSWORD="$(gen_password)"
JWT_SECRET="$(openssl rand -hex 48)"
MASTER_KEY="$(openssl rand -base64 32)"
S3_ACCESS_KEY="$(gen_password)"
S3_SECRET_KEY="$(gen_password)"
PARSER_TOKEN="$(gen_password)"

cat > .env <<EOF
# 由 deploy/gen-secrets.sh 生成，包含密钥，不要提交。
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
ADP_MIGRATOR_PASSWORD=${ADP_MIGRATOR_PASSWORD}
ADP_APP_PASSWORD=${ADP_APP_PASSWORD}
DATABASE_URL=postgresql+asyncpg://adp_app:${ADP_APP_PASSWORD}@postgres:5432/adp
MIGRATION_DATABASE_URL=postgresql+asyncpg://adp_migrator:${ADP_MIGRATOR_PASSWORD}@postgres:5432/adp
REDIS_URL=redis://redis:6379/0
JWT_SECRET=${JWT_SECRET}
MASTER_KEY=${MASTER_KEY}
# 对象存储与解析服务（第三期）：凭据随机生成
S3_ACCESS_KEY=${S3_ACCESS_KEY}
S3_SECRET_KEY=${S3_SECRET_KEY}
S3_ENDPOINT=http://rustfs:9000
S3_BUCKET=adp
S3_REGION=us-east-1
PARSER_URL=http://parser:59001
PARSER_TOKEN=${PARSER_TOKEN}
PARSER_WORKERS=2
# MinerU 快速 / 基础档（第三期）：当前服务器 Docling / MinerU / 格式转换搁置，留空即可
MINERU_URL=
MINERU_API_KEY=
CELERY_BROKER_URL=redis://redis:6379/1
WORKER_CONCURRENCY=2
# LiteLLM 不联网拉取模型价格表（代码里已默认设置，这里显式写出）
LITELLM_LOCAL_MODEL_COST_MAP=True
APP_ENV=prod
COOKIE_SECURE=true
HTTP_PORT=80
HTTPS_PORT=443
EOF

chmod 600 .env
echo "已生成 deploy/.env（含随机密码、JWT_SECRET、MASTER_KEY、对象存储凭据和 PARSER_TOKEN），请备份 JWT_SECRET 和 MASTER_KEY。"
echo "MASTER_KEY 丢失后所有已保存的模型密钥都无法解密，必须和数据库一起备份。"
echo "第三期起还要备份对象存储的 RustFS 数据卷（adp-prod_adp_rustfs），数据库备份已包含 kb_index schema。"
