#!/usr/bin/env bash
# 生成本机测试用的自签名证书到 deploy/certs/。生产证书由客户提供（技术方案 12）。
# 证书已存在时跳过（可重复执行）。
set -euo pipefail

cd "$(dirname "$0")"

mkdir -p certs

if [ -e certs/server.crt ] && [ -e certs/server.key ]; then
    echo "deploy/certs/server.crt 已存在，跳过。"
    exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout certs/server.key \
    -out certs/server.crt \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 certs/server.key
echo "已生成自签名证书：deploy/certs/server.crt、deploy/certs/server.key（仅本机测试用）。"
