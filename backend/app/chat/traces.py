"""回答流程的处理过程记录器（应用技术方案 9.1、10）。

只记录，不落库（落库在 P4-09 的 `answer_traces`）。每一步记下步骤名、开始时间、
耗时、输入、输出、模型、token 数与提示词版本，`build()` 返回 P4-01 契约的 `TraceOut`。

意图识别与问题改写并行，两步的 `started_at` 可能交错，`build()` 按 `started_at`
排序，保证处理过程里的顺序稳定。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic

from app.chat.schemas import StepName, TraceOut, TraceStepOut
from app.models.tables import Model


@dataclass
class _OpenStep:
    """已开始、还没结束的一步。"""

    name: StepName
    started_at: datetime
    started: float


@dataclass
class TraceRecorder:
    """按顺序累积处理过程的各步骤；`model` 传 P2 的 ORM 模型对象。"""

    app_id: int
    answer_id: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    _open: dict[StepName, _OpenStep] = field(default_factory=dict)
    _steps: list[TraceStepOut] = field(default_factory=list)

    def start_step(self, name: StepName) -> None:
        """记录一步开始（同一名字重复开始时覆盖上一次）。"""
        step = StepName(name)
        self._open[step] = _OpenStep(name=step, started_at=datetime.now(UTC), started=monotonic())

    def finish_step(
        self,
        name: StepName,
        *,
        input: object = None,
        output: object = None,
        model: Model | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        prompt_version: str | None = None,
    ) -> TraceStepOut:
        """记录一步结束，返回这一步的结构。"""
        step = StepName(name)
        open_step = self._open.pop(step, None)
        if open_step is None:
            raise ValueError(f"步骤 {step} 还没有开始")
        duration_ms = max(0, int(round((monotonic() - open_step.started) * 1000)))
        record = TraceStepOut(
            name=step,
            started_at=open_step.started_at,
            duration_ms=duration_ms,
            input=input,
            output=output,
            model_id=model.id if model is not None else None,
            model_name=model.name if model is not None else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_version=prompt_version,
        )
        self._steps.append(record)
        return record

    def build(self) -> TraceOut:
        """返回完整处理过程；未结束的步骤不包含。"""
        steps = sorted(self._steps, key=lambda item: item.started_at)
        return TraceOut(
            answer_id=self.answer_id,
            app_id=self.app_id,
            created_at=self.created_at,
            steps=steps,
        )
