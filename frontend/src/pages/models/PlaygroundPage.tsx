import { Alert, App, Button, Card, Input, Space } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { getErrorMessage } from "../../api/errors";
import { PageHeader } from "../../components/PageHeader";
import { streamSSE } from "../../lib/sse";
import { useModels, type Model } from "./api";
import { PlaygroundColumn } from "./PlaygroundColumn";
import {
  assistantMessage,
  createColumn,
  defaultParams,
  toRequestMessages,
  toRequestParams,
  userMessage,
  type ChatRequestMessage,
  type PlaygroundColumnState,
  type PlaygroundParamsState,
} from "./playground";

const MAX_COLUMNS = 4;

/** 模型体验页（原型 #v-play）：最多 4 个对话模型并排对比。 */
export default function PlaygroundPage() {
  const { message } = App.useApp();
  const { data: modelsPage } = useModels({ type: "chat", status: "enabled", page_size: 100 });
  const chatModels = useMemo<Model[]>(() => modelsPage?.items ?? [], [modelsPage]);

  const nextKeyRef = useRef(3);
  const controllersRef = useRef(new Map<number, AbortController>());
  const initializedRef = useRef(false);

  const [columns, setColumns] = useState<PlaygroundColumnState[]>(() => [
    createColumn(1),
    createColumn(2),
  ]);
  const [input, setInput] = useState("");

  const isStreaming = columns.some((column) => column.streaming);

  // 首次拿到模型列表时给空列填上默认模型
  useEffect(() => {
    if (initializedRef.current || chatModels.length === 0) {
      return;
    }
    initializedRef.current = true;
    setColumns((prev) => {
      const taken = new Set(
        prev.map((column) => column.modelId).filter((id): id is number => id !== null),
      );
      const available = chatModels.filter((model) => !taken.has(model.id));
      let cursor = 0;
      return prev.map((column) => {
        if (column.modelId !== null) {
          return column;
        }
        const model = available[cursor];
        if (!model) {
          return column;
        }
        cursor += 1;
        return { ...column, modelId: model.id, params: defaultParams(model) };
      });
    });
  }, [chatModels]);

  // 离开页面时中止进行中的请求
  useEffect(() => {
    const controllers = controllersRef.current;
    return () => {
      controllers.forEach((controller) => controller.abort());
      controllers.clear();
    };
  }, []);

  const updateColumn = (
    key: number,
    updater: (column: PlaygroundColumnState) => PlaygroundColumnState,
  ) => {
    setColumns((prev) =>
      prev.map((column) => (column.key === key ? updater(column) : column)),
    );
  };

  const updateAssistant = (
    key: number,
    index: number,
    updater: (message: PlaygroundColumnState["messages"][number]) => PlaygroundColumnState["messages"][number],
  ) => {
    updateColumn(key, (column) => ({
      ...column,
      messages: column.messages.map((item, i) => (i === index ? updater(item) : item)),
    }));
  };

  const startStream = (
    column: PlaygroundColumnState,
    messages: ChatRequestMessage[],
    assistantIndex: number,
  ) => {
    if (column.modelId === null) {
      return;
    }
    const controller = new AbortController();
    controllersRef.current.set(column.key, controller);

    streamSSE(
      `/api/v1/models/${column.modelId}/chat`,
      { messages, params: toRequestParams(column.params) },
      {
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "TEXT_DELTA") {
            updateAssistant(column.key, assistantIndex, (item) => ({
              ...item,
              content: item.content + event.delta,
            }));
          } else if (event.type === "REASONING_DELTA") {
            updateAssistant(column.key, assistantIndex, (item) => ({
              ...item,
              reasoning: item.reasoning + event.delta,
            }));
          } else if (event.type === "RUN_FINISHED") {
            updateAssistant(column.key, assistantIndex, (item) => ({
              ...item,
              stats: {
                firstTokenMs: event.first_token_ms,
                totalMs: event.total_ms,
                inputTokens: event.input_tokens,
                outputTokens: event.output_tokens,
              },
            }));
          } else if (event.type === "RUN_ERROR") {
            updateAssistant(column.key, assistantIndex, (item) => ({
              ...item,
              error: event.message,
            }));
          }
        },
      },
    )
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return; // 用户点了停止
        }
        updateAssistant(column.key, assistantIndex, (item) => ({
          ...item,
          error: getErrorMessage(error),
        }));
      })
      .finally(() => {
        controllersRef.current.delete(column.key);
        updateColumn(column.key, (column) => ({ ...column, streaming: false }));
      });
  };

  const handleSend = () => {
    const text = input.trim();
    if (text.length === 0 || isStreaming) {
      return;
    }
    const targets = columns.filter((column) => column.modelId !== null);
    if (targets.length === 0) {
      void message.warning("请先选择模型");
      return;
    }
    setInput("");
    for (const column of targets) {
      const history = [...column.messages, userMessage(text)];
      const assistantIndex = column.messages.length + 1;
      setColumns((prev) =>
        prev.map((item) =>
          item.key === column.key
            ? { ...item, messages: [...history, assistantMessage()], streaming: true }
            : item,
        ),
      );
      startStream(column, toRequestMessages(history), assistantIndex);
    }
  };

  const handleStop = () => {
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
  };

  const handleClear = () => {
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
    setColumns((prev) =>
      prev.map((column) => ({ ...column, messages: [], streaming: false })),
    );
  };

  const handleAdd = () => {
    if (columns.length >= MAX_COLUMNS) {
      return;
    }
    const key = nextKeyRef.current;
    nextKeyRef.current += 1;
    setColumns((prev) => [...prev, createColumn(key)]);
  };

  const handleRemove = (key: number) => {
    controllersRef.current.get(key)?.abort();
    controllersRef.current.delete(key);
    setColumns((prev) =>
      prev.length <= 1 ? prev : prev.filter((column) => column.key !== key),
    );
  };

  const handleModelChange = (key: number, modelId: number) => {
    const model = chatModels.find((item) => item.id === modelId) ?? null;
    updateColumn(key, (column) => ({
      ...column,
      modelId,
      params: model ? defaultParams(model) : column.params,
    }));
  };

  const handleParamsChange = (key: number, params: PlaygroundParamsState) => {
    updateColumn(key, (column) => ({ ...column, params }));
  };

  return (
    <>
      <PageHeader
        title="模型体验"
        description="选择模型在线对话，最多同时对比 4 个模型。可选任何已启用的对话模型。"
        extra={
          <>
            <Button onClick={handleAdd} disabled={columns.length >= MAX_COLUMNS}>
              添加对比模型（{columns.length}/{MAX_COLUMNS}）
            </Button>
            <Button onClick={handleClear}>清空对话</Button>
          </>
        }
      />

      <Alert
        type="info"
        showIcon
        message="对话记录不保存，离开页面即清空。本页产生的调用计入用量统计。"
        style={{ marginBottom: 16 }}
      />

      <div style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
        {columns.map((column) => (
          <PlaygroundColumn
            key={column.key}
            column={column}
            models={chatModels}
            canRemove={columns.length > 1}
            onModelChange={handleModelChange}
            onParamsChange={handleParamsChange}
            onRemove={handleRemove}
          />
        ))}
      </div>

      <Card size="small" style={{ marginTop: 16 }}>
        <Input.TextArea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="输入内容，回车发送，Shift+回车换行；同时发送给所有已选模型"
          autoSize={{ minRows: 2, maxRows: 6 }}
          onPressEnter={(event) => {
            if (event.shiftKey) {
              return;
            }
            event.preventDefault();
            handleSend();
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <Space>
            {isStreaming ? (
              <Button danger onClick={handleStop}>
                停止生成
              </Button>
            ) : (
              <Button
                type="primary"
                onClick={handleSend}
                disabled={columns.every((column) => column.modelId === null)}
              >
                发送
              </Button>
            )}
          </Space>
        </div>
      </Card>
    </>
  );
}
