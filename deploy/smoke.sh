#!/usr/bin/env bash
# 部署后的冒烟检查。用法：
#   deploy/smoke.sh [HTTPS 基础地址] [HTTP 基础地址]
# 不带参数时读 deploy/.env 的 HTTPS_PORT / HTTP_PORT（默认 443 / 80），
# 地址为 https://localhost:<HTTPS_PORT> 和 http://localhost:<HTTP_PORT>。
#
# 检查内容：
#   1. https://.../ 返回 HTML
#   2. /api/v1/openapi.json 返回 200
#   3. 错误密码登录返回 401 JSON，响应头带 X-Request-Id
#   4. 伪造 X-Real-IP 连续失败 5 次后锁定，换一个伪造值仍锁定（nginx 覆盖了该头）
#   5. http://.../ 跳转到 https
set -euo pipefail

cd "$(dirname "$0")"

HTTPS_PORT=""
HTTP_PORT=""
if [ -f .env ]; then
    HTTPS_PORT="$(sed -n 's/^HTTPS_PORT=//p' .env | tail -n1)"
    HTTP_PORT="$(sed -n 's/^HTTP_PORT=//p' .env | tail -n1)"
fi
HTTPS_PORT="${HTTPS_PORT:-443}"
HTTP_PORT="${HTTP_PORT:-80}"

HTTPS_BASE="${1:-https://localhost:${HTTPS_PORT}}"
HTTP_BASE="${2:-http://localhost:${HTTP_PORT}}"

# -k 忽略自签名证书
CURL=(curl -k -s -S --max-time 15)
# 每次运行用不同的用户名，避免上次运行留下的锁定影响本次检查
RUN_ID="$$"
LOGIN_USER="smoke-user-${RUN_ID}"
LOCK_USER="smoke-lockout-${RUN_ID}"
WRONG_PASSWORD="smoke-wrong-password"

fail() {
    echo "失败：$*" >&2
    exit 1
}

pass() {
    echo "通过：$*"
}

# 1. 首页返回 HTML
content_type="$("${CURL[@]}" -o /dev/null -w '%{content_type}' "$HTTPS_BASE/")"
[ "${content_type%%;*}" = "text/html" ] \
    || fail "GET $HTTPS_BASE/ 不是 HTML（Content-Type: $content_type）"
pass "GET $HTTPS_BASE/ 返回 HTML"

# 2. OpenAPI 文档可访问
code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$HTTPS_BASE/api/v1/openapi.json")"
[ "$code" = "200" ] || fail "GET /api/v1/openapi.json 返回 $code，期望 200"
pass "GET /api/v1/openapi.json 返回 200"

# 3. 错误密码登录返回 401 JSON，且响应头有 X-Request-Id
headers="$(mktemp)"
body="$(mktemp)"
code="$("${CURL[@]}" -D "$headers" -o "$body" -w '%{http_code}' \
    -X POST "$HTTPS_BASE/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"${LOGIN_USER}\",\"password\":\"${WRONG_PASSWORD}\"}")"
[ "$code" = "401" ] || fail "错误密码登录返回 $code，期望 401"
grep -qi '^content-type: *application/json' "$headers" || fail "登录错误响应不是 JSON"
request_id="$(grep -i '^x-request-id:' "$headers" | tail -n1 | cut -d: -f2- | tr -d ' \r')"
[ -n "$request_id" ] || fail "登录错误响应缺少 X-Request-Id 头"
grep -q '"code"' "$body" || fail "登录错误响应体不是统一错误格式"
rm -f "$headers" "$body"
pass "错误密码登录返回 401 JSON，响应头带 X-Request-Id"

# 4. 伪造 X-Real-IP：连续失败 5 次后锁定，换伪造值仍锁定
#    （若 nginx 没覆盖该头，换 IP 后会是 401 而不是 429）
for i in 1 2 3 4 5; do
    code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
        -X POST "$HTTPS_BASE/api/v1/auth/login" \
        -H 'Content-Type: application/json' \
        -H 'X-Real-IP: 1.2.3.4' \
        -d "{\"username\":\"${LOCK_USER}\",\"password\":\"${WRONG_PASSWORD}\"}")"
    [ "$code" = "401" ] || fail "第 $i 次伪造 IP 登录返回 $code，期望 401"
done
code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
    -X POST "$HTTPS_BASE/api/v1/auth/login" \
    -H 'Content-Type: application/json' \
    -H 'X-Real-IP: 5.6.7.8' \
    -d "{\"username\":\"${LOCK_USER}\",\"password\":\"${WRONG_PASSWORD}\"}")"
[ "$code" = "429" ] \
    || fail "换伪造 IP 后返回 $code，期望 429（nginx 没有用 \$remote_addr 覆盖 X-Real-IP？）"
pass "伪造 X-Real-IP 失败 5 次后锁定，换伪造值仍锁定"

# 5. http 跳转到 https
code="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$HTTP_BASE/")"
[ "$code" = "301" ] || fail "http 访问返回 $code，期望 301"
location="$("${CURL[@]}" -o /dev/null -w '%{redirect_url}' "$HTTP_BASE/")"
case "$location" in
    https://*) ;;
    *) fail "http 跳转目标不是 https：$location" ;;
esac
pass "http 跳转到 https（$location）"

echo "冒烟检查全部通过。"
