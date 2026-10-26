import { CopyOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Input, Modal, Space, Tag, Typography } from "antd";
import { useRef, useState, type ReactNode } from "react";
import type { Components } from "react-markdown";

import type { components } from "../../api/schema";
import { getErrorMessage } from "../../api/errors";
import type { ChatSseEvent } from "../../lib/sse";
import { SafeMarkdown } from "../SafeMarkdown";
import {
  citedNumbers,
  toCitationMarkdown,
  CITATION_HREF_PREFIX,
  type Citation,
} from "./citation";

export type StepName = components["schemas"]["StepName"];
export type TraceOut = components["schemas"]["TraceOut"];
export type TraceStepOut = components["schemas"]["TraceStepOut"];
export type AnswerStatus = components["schemas"]["AnswerStatus"];
export type DebugMessageIn = components["schemas"]["DebugMessageIn"];

export interface ChatStats {
  firstTokenMs: number | null;
  totalMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
}

export interface ChatStep {
  name: StepName;
  durationMs: number | null;
  trace: TraceStepOut | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  reasoning: string;
  citations: Citation[];
  suggestions: string[];
  steps: ChatStep[];
  trace: TraceOut | null;
  status: AnswerStatus;
  error: string | null;
  stats: ChatStats | null;
  /** 后端回答 ID；新会话的第一问由 `RUN_STARTED` 补上，历史消息由服务端加载时带上。 */
  answerId: number | null;
}

export interface ChatRunRequest {
  question: string;
  /** 前端带上的历史（最多 20 轮），由 ChatPanel 从已有消息生成。 */
  history: DebugMessageIn[];
  signal: AbortSignal;
  onEvent: (event: ChatSseEvent) => void;
  /** 终端用户对话页据此区分“提问”和“重新生成”（重新生成走 `turns:regenerate`）。 */
  mode?: "ask" | "regenerate";
}

export interface ChatPanelProps {
  /** 发起一次请求；调试用调试接口，终端用户用会话接口（P4-16 复用）。 */
  startRun: (request: ChatRunRequest) => Promise<void>;
  /** 调试模式：多“处理过程”展开区与“清空”。 */
  debug?: boolean;
  opening?: string;
  openingQuestions?: string[];
  placeholder?: string;
  header?: ReactNode;
  /** 已有会话的历史消息（终端用户对话页打开会话时传入，按负数 id 避免与内部自增 id 冲突）。 */
  initialMessages?: ChatMessage[];
  /** 点击引用角标（调试里据此打开分块位置预览；对话页据此取回答 ID 打开引用抽屉）。 */
  onCitationClick?: (citation: Citation, answerId: number | null) => void;
  /** 点击处理过程里的召回分块，打开分块位置预览。 */
  onChunkPreview?: (chunkId: number) => void;
  /** 每条回答下方的额外操作区（终端用户对话页的点赞 / 点踩）。 */
  renderAnswerActions?: (message: ChatMessage) => ReactNode;
  emptyHint?: ReactNode;
}

export const STEP_LABELS: Record<StepName, string> = {
  intent: "意图识别",
  rewrite: "问题改写",
  retrieve: "文档库召回",
  rerank: "重排",
  generate: "模型调用",
  suggest: "追问建议",
};

const MAX_HISTORY_TURNS = 20;

/** 调试预览带上的历史：最多最近 20 轮（40 条），空内容不计（应用技术方案 12.1）。 */
export function buildDebugHistory(
  messages: { role: "user" | "assistant"; content: string }[],
): DebugMessageIn[] {
  return messages
    .filter((message) => message.content.trim().length > 0)
    .slice(-MAX_HISTORY_TURNS * 2)
    .map((message) => ({ role: message.role, content: message.content }));
}

/** 从处理过程的输入 / 输出里收集分块 ID（点击查看原文位置用）。 */
export function collectChunkIds(output: unknown): number[] {
  const ids = new Set<number>();
  const walk = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        if (key === "chunk_id" && typeof child === "number") {
          ids.add(child);
        } else if (key === "chunk_ids" && Array.isArray(child)) {
          child.forEach((item) => typeof item === "number" && ids.add(item));
        } else {
          walk(child);
        }
      }
    }
  };
  walk(output);
  return [...ids];
}

/** 一步处理过程的简短说明；结构未定，按常见字段拼，缺字段时不显示。 */
export function stepSummary(step: ChatStep): string {
  const trace = step.trace;
  const output = trace?.output;
  if (output == null) {
    return "";
  }
  if (typeof output === "string") {
    return output;
  }
  if (typeof output === "object") {
    const record = output as Record<string, unknown>;
    const parts: string[] = [];
    for (const key of ["intent", "question", "rewritten", "summary", "count", "kept"]) {
      const value = record[key];
      if (typeof value === "string" || typeof value === "number") {
        parts.push(`${key}: ${value}`);
      }
    }
    if (parts.length > 0) {
      return parts.join(" · ");
    }
    return JSON.stringify(output).slice(0, 120);
  }
  return String(output);
}

/** 回答下方的引用列表：只显示实际引用的条目（应用技术方案 8.1）。 */
function citedCitationList(
  message: ChatMessage,
  onCitationClick?: (citation: Citation, answerId: number | null) => void,
): ReactNode {
  if (message.role !== "assistant") {
    return null;
  }
  const cited = new Set(citedNumbers(message.content));
  const shown = message.citations.filter((citation) => cited.has(citation.n));
  if (shown.length === 0) {
    return null;
  }
  return (
    <Space wrap size={4} style={{ marginTop: 4 }}>
      {shown.map((citation) => (
        <Button
          key={citation.n}
          type="link"
          size="small"
          data-testid={`answer-citation-${citation.n}`}
          onClick={() => onCitationClick?.(citation, message.answerId)}
        >
          [{citation.n}] {citation.doc_name ?? citation.kb_name}
        </Button>
      ))}
    </Space>
  );
}

function formatDuration(ms: number | null): string {
  return ms == null ? "" : `${(ms / 1000).toFixed(1)}s`;
}

interface ChatPanelState {
  messages: ChatMessage[];
}

let messageSeq = 0;

function nextId(): number {
  messageSeq += 1;
  return messageSeq;
}

function newAssistant(): ChatMessage {
  return {
    id: nextId(),
    role: "assistant",
    content: "",
    reasoning: "",
    citations: [],
    suggestions: [],
    steps: [],
    trace: null,
    status: "generating",
    error: null,
    stats: null,
    answerId: null,
  };
}

/** 把一条 SSE 事件叠加到回答消息上（累积全文，角标在渲染时按全文解析）。 */
export function applyChatEvent(message: ChatMessage, event: ChatSseEvent): ChatMessage {
  switch (event.type) {
    case "TEXT_DELTA":
      return { ...message, content: message.content + event.delta };
    case "REASONING_DELTA":
      return { ...message, reasoning: message.reasoning + event.delta };
    case "CITATIONS":
      return { ...message, citations: event.citations };
    case "SUGGESTIONS":
      return { ...message, suggestions: event.suggestions };
    case "STEP_STARTED":
      return {
        ...message,
        steps: [
          ...message.steps.filter((step) => step.name !== event.step),
          { name: event.step, durationMs: null, trace: null },
        ],
      };
    case "STEP_FINISHED":
      return {
        ...message,
        steps: message.steps.map((step) =>
          step.name === event.step ? { ...step, durationMs: event.duration_ms } : step,
        ),
      };
    case "TRACE":
      return {
        ...message,
        trace: event.trace,
        steps: event.trace.steps.map((step) => ({
          name: step.name,
          durationMs: step.duration_ms,
          trace: step,
        })),
      };
    case "RUN_FINISHED":
      return {
        ...message,
        status: event.status,
        stats: {
          firstTokenMs: event.first_token_ms,
          totalMs: event.total_ms,
          inputTokens: event.input_tokens,
          outputTokens: event.output_tokens,
        },
      };
    case "RUN_ERROR":
      return { ...message, status: "failed", error: event.message };
    case "RUN_STARTED":
      return { ...message, answerId: event.answer_id };
  }
}

/**
 * 共用的对话组件（原型 #v-app 右侧 / #v-chat）。
 * 读取 SSE、按累积全文渲染引用角标、停止、复制、重新生成；debug 模式多“处理过程”。
 */
export function ChatPanel({
  startRun,
  debug = false,
  opening,
  openingQuestions = [],
  placeholder = "输入问题，Ctrl+Enter 发送",
  header,
  initialMessages,
  onCitationClick,
  onChunkPreview,
  renderAnswerActions,
  emptyHint,
}: ChatPanelProps) {
  const [{ messages }, setState] = useState<ChatPanelState>(() => ({
    messages: initialMessages ?? [],
  }));
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [promptStep, setPromptStep] = useState<TraceStepOut | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const setMessages = (next: ChatMessage[]) => setState({ messages: next });
  const updateMessage = (id: number, updater: (message: ChatMessage) => ChatMessage) =>
    setState((state) => ({
      messages: state.messages.map((message) => (message.id === id ? updater(message) : message)),
    }));

  const run = async (
    question: string,
    base: ChatMessage[],
    mode: "ask" | "regenerate" = "ask",
  ) => {
    const history = buildDebugHistory(base);
    const userMessage: ChatMessage = {
      id: nextId(),
      role: "user",
      content: question,
      reasoning: "",
      citations: [],
      suggestions: [],
      steps: [],
      trace: null,
      status: "done",
      error: null,
      stats: null,
      answerId: null,
    };
    const assistant = newAssistant();
    setMessages([...base, userMessage, assistant]);
    setPending(true);

    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      await startRun({
        question,
        history,
        signal: controller.signal,
        mode,
        onEvent: (event) => updateMessage(assistant.id, (message) => applyChatEvent(message, event)),
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        updateMessage(assistant.id, (message) => ({
          ...message,
          status: "failed",
          error: getErrorMessage(error),
        }));
      }
    } finally {
      setPending(false);
      controllerRef.current = null;
    }
  };

  const handleSend = (text = input) => {
    const question = text.trim();
    if (question === "" || pending) {
      return;
    }
    setInput("");
    void run(question, messages);
  };

  const handleStop = () => {
    controllerRef.current?.abort();
    setPending(false);
  };

  const handleRegenerate = () => {
    if (pending) {
      return;
    }
    let lastUserIndex = -1;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index]?.role === "user") {
        lastUserIndex = index;
        break;
      }
    }
    if (lastUserIndex < 0) {
      return;
    }
    const question = messages[lastUserIndex]?.content ?? "";
    void run(question, messages.slice(0, lastUserIndex), "regenerate");
  };

  const handleCopy = (content: string) => {
    void navigator.clipboard?.writeText(content).catch(() => {});
  };

  const citationComponents = (message: ChatMessage): Components => ({
    a: ({ href, children }) => {
      if (href?.startsWith(CITATION_HREF_PREFIX)) {
        const n = Number(href.slice(CITATION_HREF_PREFIX.length));
        const citation = message.citations.find((item) => item.n === n);
        return (
          <sup
            data-testid={`citation-${n}`}
            style={{ cursor: "pointer", color: "#1677ff", margin: "0 1px" }}
            onClick={() => citation && onCitationClick?.(citation, message.answerId)}
          >
            {n}
          </sup>
        );
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    },
  });

  return (
    <Card
      title={header ?? (debug ? "预览调试" : "对话")}
      extra={
        debug ? (
          <Button size="small" disabled={messages.length === 0} onClick={() => setMessages([])}>
            清空
          </Button>
        ) : null
      }
      style={{ height: "100%" }}
    >
      {debug ? (
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          使用当前草稿配置
        </Typography.Paragraph>
      ) : null}

      <div data-testid="chat-messages" style={{ minHeight: 120, marginBottom: 12 }}>
        {opening && messages.length === 0 ? (
          <div style={{ marginBottom: 12 }}>
            <SafeMarkdown content={opening} />
          </div>
        ) : null}
        {openingQuestions.length > 0 && messages.length === 0 ? (
          <Space wrap style={{ marginBottom: 12 }}>
            {openingQuestions.map((question) => (
              <Tag
                key={question}
                style={{ cursor: "pointer" }}
                onClick={() => setInput(question)}
              >
                {question}
              </Tag>
            ))}
          </Space>
        ) : null}
        {messages.length === 0 && !opening ? (
          <Typography.Text type="secondary">{emptyHint ?? "输入问题开始对话。"}</Typography.Text>
        ) : null}

        {messages.map((message) => (
          <div key={message.id} style={{ marginBottom: 12 }}>
            {message.role === "user" ? (
              <div style={{ textAlign: "right" }}>
                <Tag color="blue">{message.content}</Tag>
              </div>
            ) : (
              <div data-testid="chat-answer">
                {message.content ? (
                  <SafeMarkdown
                    content={toCitationMarkdown(message.content, message.citations)}
                    components={citationComponents(message)}
                  />
                ) : message.status === "generating" ? (
                  <Typography.Text type="secondary">正在生成…</Typography.Text>
                ) : null}

                {message.error ? (
                  <Alert type="error" showIcon message={message.error} style={{ marginTop: 8 }} />
                ) : null}

                {debug && message.steps.length > 0 ? (
                  <ProcessingSteps
                    steps={message.steps}
                    stats={message.stats}
                    onShowPrompt={setPromptStep}
                    onChunkPreview={onChunkPreview}
                  />
                ) : null}

                {citedCitationList(message, onCitationClick)}

                <Space size="small" style={{ marginTop: 4 }}>
                  {message.content ? (
                    <Button
                      type="link"
                      size="small"
                      icon={<CopyOutlined />}
                      onClick={() => handleCopy(message.content)}
                    >
                      复制
                    </Button>
                  ) : null}
                </Space>

                {renderAnswerActions ? renderAnswerActions(message) : null}
              </div>
            )}
          </div>
        ))}
      </div>

      <Space.Compact style={{ width: "100%" }}>
        <Input.TextArea
          value={input}
          placeholder={placeholder}
          autoSize={{ minRows: 1, maxRows: 4 }}
          onChange={(event) => setInput(event.target.value)}
          onPressEnter={(event) => {
            if (event.ctrlKey || event.metaKey) {
              event.preventDefault();
              handleSend();
            }
          }}
        />
        {pending ? (
          <Button danger onClick={handleStop}>
            停止生成
          </Button>
        ) : (
          <Button type="primary" onClick={() => handleSend()}>
            发送
          </Button>
        )}
      </Space.Compact>

      <Space size="small" style={{ marginTop: 8 }}>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          disabled={pending || messages.every((message) => message.role !== "user")}
          onClick={handleRegenerate}
        >
          重新生成
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          预览对话的模型用量来源记为“应用调试”。
        </Typography.Text>
      </Space>

      <Modal
        open={promptStep !== null}
        title="完整提示词"
        footer={null}
        width={720}
        onCancel={() => setPromptStep(null)}
      >
        <pre style={{ whiteSpace: "pre-wrap", maxHeight: 480, overflow: "auto" }}>
          {promptStep ? JSON.stringify(promptStep.input, null, 2) : ""}
        </pre>
      </Modal>
    </Card>
  );
}

interface ProcessingStepsProps {
  steps: ChatStep[];
  stats: ChatStats | null;
  onShowPrompt: (step: TraceStepOut) => void;
  onChunkPreview?: (chunkId: number) => void;
}

/** 调试模式的处理过程（原型 #v-app 的 `处理过程 · 总耗时`）。 */
function ProcessingSteps({ steps, stats, onShowPrompt, onChunkPreview }: ProcessingStepsProps) {
  return (
    <details data-testid="chat-trace" style={{ marginTop: 8 }}>
      <summary style={{ cursor: "pointer" }}>
        处理过程
        {stats?.totalMs != null ? ` · 总耗时 ${(stats.totalMs / 1000).toFixed(1)}s` : ""}
      </summary>
      <ol style={{ margin: "8px 0 0", paddingLeft: 20 }}>
        {steps.map((step, index) => {
          const chunkIds = collectChunkIds(step.trace?.output);
          return (
            <li key={`${step.name}-${index}`} data-testid={`trace-step-${step.name}`}>
              <b>{STEP_LABELS[step.name]}</b>
              {stepSummary(step) ? `：${stepSummary(step)}` : ""}
              {step.durationMs != null ? `（${formatDuration(step.durationMs)}）` : ""}
              {step.name === "generate" && step.trace ? (
                <>
                  {step.trace.input_tokens != null || step.trace.output_tokens != null
                    ? ` · 输入 ${step.trace.input_tokens ?? "-"} / 输出 ${step.trace.output_tokens ?? "-"} tokens`
                    : ""}{" "}
                  <Button
                    type="link"
                    size="small"
                    onClick={() => onShowPrompt(step.trace as TraceStepOut)}
                  >
                    查看完整提示词
                  </Button>
                </>
              ) : null}
              {chunkIds.length > 0 && onChunkPreview ? (
                <>
                  {" "}
                  {chunkIds.map((chunkId) => (
                    <Button
                      key={chunkId}
                      type="link"
                      size="small"
                      onClick={() => onChunkPreview(chunkId)}
                    >
                      查看位置 #{chunkId}
                    </Button>
                  ))}
                </>
              ) : null}
            </li>
          );
        })}
      </ol>
    </details>
  );
}
