"""用 tiktoken `cl100k_base` 估算 token 数（技术方案 9.6）。

只在拿不到上游用量时使用（如调用被取消）。编码文件离线加载：构建镜像时预下载到
`TIKTOKEN_CACHE_DIR` 指向的目录，测试环境同样设置该变量。
"""

import functools

import tiktoken


@functools.lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_text(text: str) -> int:
    """一段文本的 token 数。"""
    if not text:
        return 0
    return len(_encoding().encode(text))


def _content_text(content: object) -> str:
    """消息 content 可能是字符串，也可能是分块数组（视觉），统一转成文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return "" if content is None else str(content)


def count_messages(messages: list[dict]) -> int:
    """按角色名和内容估算一组对话消息的 token 数。"""
    total = 0
    for message in messages:
        total += count_text(str(message.get("role", "")))
        total += count_text(_content_text(message.get("content")))
    return total
