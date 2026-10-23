import { CloseOutlined } from "@ant-design/icons";
import { Button, Card, Collapse, Select, Space, Spin, Typography } from "antd";
import { useEffect, useRef } from "react";

import { SafeMarkdown } from "../../components/SafeMarkdown";
import { PlaygroundParamsPopover } from "./PlaygroundParamsPopover";
import {
  formatSeconds,
  type ChatMessage,
  type Model,
  type PlaygroundColumnState,
  type PlaygroundParamsState,
} from "./playground";

interface PlaygroundColumnProps {
  column: PlaygroundColumnState;
  models: Model[];
  canRemove: boolean;
  onModelChange: (key: number, modelId: number) => void;
  onParamsChange: (key: number, params: PlaygroundParamsState) => void;
  onRemove: (key: number) => void;
}

function AssistantBody({ message }: { message: ChatMessage }) {
  return (
    <>
      {message.reasoning.length > 0 ? (
        <Collapse
          size="small"
          ghost
          style={{ marginBottom: 4 }}
          items={[
            {
              key: "reasoning",
              label: "思考过程",
              children: (
                <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  <SafeMarkdown content={message.reasoning} />
                </Typography.Paragraph>
              ),
            },
          ]}
        />
      ) : null}
      {message.content.length > 0 ? <SafeMarkdown content={message.content} /> : null}
      {message.content.length === 0 && message.reasoning.length === 0 && message.error === null ? (
        <Spin size="small" />
      ) : null}
      {message.stats ? (
        <div data-testid="msg-stats">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            首字 {formatSeconds(message.stats.firstTokenMs)} · 总耗时{" "}
            {formatSeconds(message.stats.totalMs)} · 输入 {message.stats.inputTokens} / 输出{" "}
            {message.stats.outputTokens} tokens
          </Typography.Text>
        </div>
      ) : null}
      {message.error ? (
        <div data-testid="msg-error">
          <div>
            <Typography.Text type="danger">调用失败：{message.error}</Typography.Text>
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              可在模型配置中调整超时与重试次数
            </Typography.Text>
          </div>
        </div>
      ) : null}
    </>
  );
}

/** 模型体验页的一列：模型选择、参数、关闭和该列的对话。 */
export function PlaygroundColumn({
  column,
  models,
  canRemove,
  onModelChange,
  onParamsChange,
  onRemove,
}: PlaygroundColumnProps) {
  const model = models.find((item) => item.id === column.modelId) ?? null;
  const scrollRef = useRef<HTMLDivElement>(null);

  // 流式追加时保持在底部
  useEffect(() => {
    const node = scrollRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [column.messages]);

  return (
    <Card
      size="small"
      style={{ flex: "1 1 0", minWidth: 0, display: "flex", flexDirection: "column" }}
      styles={{ body: { display: "flex", flexDirection: "column", gap: 8, height: "100%" } }}
    >
      <Space.Compact style={{ width: "100%" }}>
        <Select
          style={{ flex: 1 }}
          placeholder="选择模型"
          value={column.modelId ?? undefined}
          onChange={(value: number) => onModelChange(column.key, value)}
          options={models.map((item) => ({ value: item.id, label: item.name }))}
        />
        <PlaygroundParamsPopover
          model={model}
          params={column.params}
          onChange={(params) => onParamsChange(column.key, params)}
        />
        <Button
          size="small"
          icon={<CloseOutlined />}
          title="移除"
          disabled={!canRemove}
          onClick={() => onRemove(column.key)}
        />
      </Space.Compact>

      <div
        ref={scrollRef}
        style={{
          flex: 1,
          minHeight: 320,
          maxHeight: "56vh",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: 4,
        }}
      >
        {column.messages.map((message, index) =>
          message.role === "user" ? (
            <div
              key={index}
              style={{
                alignSelf: "flex-end",
                maxWidth: "85%",
                background: "#e6f4ff",
                borderRadius: 8,
                padding: "6px 10px",
                whiteSpace: "pre-wrap",
              }}
            >
              {message.content}
            </div>
          ) : (
            <div
              key={index}
              style={{
                alignSelf: "flex-start",
                maxWidth: "100%",
                background: "#fafafa",
                borderRadius: 8,
                padding: "6px 10px",
              }}
            >
              <AssistantBody message={message} />
            </div>
          ),
        )}
      </div>
    </Card>
  );
}
