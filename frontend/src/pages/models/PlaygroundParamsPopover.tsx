import { Button, InputNumber, Popover, Slider, Space, Switch, Typography } from "antd";
import { useState } from "react";

import type { Model, PlaygroundParamsState } from "./playground";

interface PlaygroundParamsPopoverProps {
  model: Model | null;
  params: PlaygroundParamsState;
  disabled?: boolean;
  onChange: (params: PlaygroundParamsState) => void;
}

/** 每列顶部的“参数”浮层：温度、Top P、最大输出、深度思考。 */
export function PlaygroundParamsPopover({
  model,
  params,
  disabled,
  onChange,
}: PlaygroundParamsPopoverProps) {
  const [open, setOpen] = useState(false);
  const supportsThinking = model?.cap_thinking ?? false;

  const content = (
    <div style={{ width: 240, display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <Typography.Text>温度 {params.temperature.toFixed(1)}</Typography.Text>
        <Slider
          min={0}
          max={2}
          step={0.1}
          value={params.temperature}
          onChange={(value) => onChange({ ...params, temperature: value })}
        />
      </div>
      <div>
        <Typography.Text>Top P {params.topP.toFixed(2)}</Typography.Text>
        <Slider
          min={0}
          max={1}
          step={0.05}
          value={params.topP}
          onChange={(value) => onChange({ ...params, topP: value })}
        />
      </div>
      <Space>
        <Typography.Text>最大输出</Typography.Text>
        <InputNumber
          min={1}
          value={params.maxTokens ?? undefined}
          onChange={(value) => onChange({ ...params, maxTokens: value ?? null })}
        />
      </Space>
      <Space>
        <Typography.Text>深度思考</Typography.Text>
        <Switch
          size="small"
          checked={params.thinking}
          disabled={!supportsThinking}
          onChange={(checked) => onChange({ ...params, thinking: checked })}
        />
        {!supportsThinking ? <Typography.Text type="secondary">模型不支持</Typography.Text> : null}
      </Space>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        默认值取自模型配置，仅对本次体验生效。
      </Typography.Text>
    </div>
  );

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      content={content}
      trigger="click"
      placement="bottomLeft"
    >
      <Button size="small" disabled={disabled || !model}>
        参数
      </Button>
    </Popover>
  );
}
