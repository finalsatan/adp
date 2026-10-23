-- 续约一个并发租约（P2-04，技术方案 9.3）：成员还在时把到期时间往后推。
-- 当前时间用 Redis 的 TIME 取；`XX` 保证成员已被删除时不会重新加回来。
--
-- KEYS[1] 并发有序集合键
-- ARGV[1] member
-- ARGV[2] lease_seconds

local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[2]), ARGV[1])
