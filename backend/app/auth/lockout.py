"""登录失败锁定：键名与管理员解锁（技术方案 8.1）。

- login_fail:{username}:{ip}  失败计数，首次写入时设过期
- login_lock:{username}:{ip}  该 IP 的锁定键
- login_locked:{username}     被锁定的 IP 集合，用户列表据此显示“已锁定”

不用 KEYS / SCAN 按模式查键：解锁时从集合里读出 IP 再删对应键。
"""

from redis.asyncio import Redis


def fail_key(username: str, ip: str) -> str:
    return f"login_fail:{username}:{ip}"


def lock_key(username: str, ip: str) -> str:
    return f"login_lock:{username}:{ip}"


def locked_set_key(username: str) -> str:
    return f"login_locked:{username}"


async def is_locked(redis: Redis, username: str) -> bool:
    """该用户名是否至少有一个 IP 处于锁定状态。"""
    return bool(await redis.scard(locked_set_key(username)))


async def unlock(redis: Redis, username: str) -> None:
    """管理员解锁：删除集合，以及集合中每个 IP 的锁定键和计数键。"""
    key = locked_set_key(username)
    ips = await redis.smembers(key)
    pipe = redis.pipeline()
    pipe.delete(key)
    for ip in ips:
        pipe.delete(lock_key(username, ip))
        pipe.delete(fail_key(username, ip))
    await pipe.execute()
