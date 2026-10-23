"""模型调用与流式事件。

模型调用的唯一入口在 `app.gateway.service`；其他模块不得直接 import litellm
（技术方案 15：以后要替换只改这一处）。

`LITELLM_LOCAL_MODEL_COST_MAP` 必须在 import litellm 之前设置，否则 LiteLLM 启动
时会联网拉取模型价格表（技术方案 9.3、15）。放在包的 `__init__` 里，保证任何
`app.gateway.*` 子模块被导入时环境变量都已就位。
"""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

_configured = False
_http_client = None


def configure_litellm() -> None:
    """关闭遥测和所有回调，并让 LiteLLM 不跟随重定向（技术方案 9.1、9.3）。"""
    global _configured, _http_client
    if _configured:
        return

    import httpx
    import litellm

    # 这个版本的 LiteLLM 已不再上报遥测，设置它是防御性的；属性不存在时直接赋值即可。
    litellm.telemetry = False
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm.callbacks = []
    litellm.turn_off_message_logging = True

    # openai SDK 默认跟随重定向（follow_redirects=True），LiteLLM 的
    # `_get_async_http_client` 会优先使用 `litellm.aclient_session`；用它把重定向关掉。
    _http_client = httpx.AsyncClient(follow_redirects=False)
    litellm.aclient_session = _http_client
    _configured = True
