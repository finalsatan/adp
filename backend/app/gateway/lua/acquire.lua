-- 原子获取模型调用额度（P2-04，技术方案 9.3）。
--
-- 先检查全部键都未超限，再一起增加；任一超限则什么都不改。
-- 当前时间用 Redis 的 TIME 取，避免多副本时钟不一致。
--
-- KEYS[1..nq]        QPS 键前缀（脚本再拼接当前秒）
-- KEYS[nq+1..nq+nc]  并发有序集合键
-- ARGV[1] nq                QPS 检查数
-- ARGV[2] nc                并发检查数
-- ARGV[3] member            本次请求 id
-- ARGV[4] lease_seconds     租约时长（秒）
-- ARGV[5 .. 4+nq]           每个 QPS 键的上限
-- ARGV[5+nq .. 4+nq+nc]     每个并发键的上限
--
-- 返回 {1, 'ok', 0} 或 {0, 'qps'|'conc', 失败检查的序号}。

local nq = tonumber(ARGV[1])
local nc = tonumber(ARGV[2])
local member = ARGV[3]
local lease = tonumber(ARGV[4])

local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local sec = t[1]

-- 检查
for i = 1, nq do
  local limit = tonumber(ARGV[4 + i])
  local key = KEYS[i] .. sec
  local cur = tonumber(redis.call('GET', key) or '0')
  if cur + 1 > limit then
    return {0, 'qps', i}
  end
end
for j = 1, nc do
  local limit = tonumber(ARGV[4 + nq + j])
  local key = KEYS[nq + j]
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  local cur = redis.call('ZCARD', key)
  if cur + 1 > limit then
    return {0, 'conc', j}
  end
end

-- 增加
for i = 1, nq do
  local key = KEYS[i] .. sec
  local v = redis.call('INCR', key)
  if v == 1 then
    redis.call('EXPIRE', key, 2)
  end
end
for j = 1, nc do
  redis.call('ZADD', KEYS[nq + j], now + lease, member)
end
return {1, 'ok', 0}
