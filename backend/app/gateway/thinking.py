"""按供应商把 `thinking` 开关映射成 LiteLLM 能识别的参数（P2-05 补充）。

LiteLLM 的统一 `thinking` 参数主要面向 Anthropic；OpenAI、DashScope、DeepSeek
三家的开关方式不同，这里按 `litellm_model` 的供应商前缀映射：

- `dashscope/`：Qwen 的 OpenAI 兼容接口用 `extra_body.enable_thinking`。
- `deepseek/`：DeepSeek 的思考模式用 `thinking={"type": "enabled"|"disabled"}`。
- `openai/`：推理模型用 `reasoning_effort`；关闭时没有对应取值，直接不传（用模型默认）。
- 其他供应商：回落到 LiteLLM 的统一 `thinking` 参数。
"""


def thinking_params(litellm_model: str, thinking: bool) -> dict:
    """返回要合并进调用参数里的供应商相关字段。"""
    provider = litellm_model.split("/", 1)[0].lower()
    if provider == "dashscope":
        return {"extra_body": {"enable_thinking": thinking}}
    if provider == "deepseek":
        return {"thinking": {"type": "enabled" if thinking else "disabled"}}
    if provider == "openai":
        return {"reasoning_effort": "medium"} if thinking else {}
    return {"thinking": thinking}
