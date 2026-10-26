import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  List,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { fetchWithRefresh } from "../../api/client";
import { ApiError, getErrorMessage, readErrorBody } from "../../api/errors";
import { SafeMarkdown } from "../../components/SafeMarkdown";
import { ParseResultView } from "../kb/documents/ParseResultView";
import { AppHeader } from "./AppHeader";
import {
  LOG_FEEDBACK_OPTIONS,
  LOG_RANGE_OPTIONS,
  buildLogListParams,
  isForbidden,
  useApp,
  useLogConversation,
  useLogConversations,
  type LogAnswerOut,
  type LogCitationPreviewOut,
  type LogConversationOut,
  type LogFeedbackFilter,
  type LogRange,
  type LogTraceStepOut,
  type LogTurnOut,
} from "./api";

const ANSWER_KIND_LABELS: Record<string, string> = {
  generated: "生成回答",
  qa_direct: "直接回答",
  fallback: "兜底回复",
  chitchat: "闲聊",
};

const ANSWER_STATUS_LABELS: Record<string, string> = {
  generating: "生成中",
  done: "已完成",
  stopped: "已停止",
  failed: "失败",
};

/** 处理过程各步骤的中文名（与对话组件一致）。 */
const STEP_LABELS: Record<LogTraceStepOut["name"], string> = {
  intent: "意图识别",
  rewrite: "问题改写",
  retrieve: "文档库召回",
  rerank: "重排",
  generate: "模型调用",
  suggest: "追问建议",
};

function formatDuration(ms: number | null): string {
  return ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`;
}

/** 从处理过程的输出里收集分块 ID（日志里点击查看位置用）。 */
function collectChunkIds(output: unknown): number[] {
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
function stepSummary(step: LogTraceStepOut): string {
  const output = step.output;
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

/** 分块 ID 对应到该回答引用清单里的编号（日志的预览接口按引用编号取）。 */
function citationNumberForChunk(answer: LogAnswerOut, chunkId: number): number | null {
  const citation = answer.citations.find((item) => (item.chunk_ids ?? []).includes(chunkId));
  return citation ? citation.n : null;
}

/** 对话日志页（原型 #v-applog）。纠错加入问答库属第六期，本页不做。 */
export default function AppLogsPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const appId = Number.isFinite(parsedId) ? parsedId : null;

  const [range, setRange] = useState<LogRange>("7d");
  const [feedback, setFeedback] = useState<LogFeedbackFilter>("all");
  const [keyword, setKeyword] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const [preview, setPreview] = useState<{ answerId: number; n: number } | null>(null);

  const convParam = Number(searchParams.get("conv"));
  const selectedConv = Number.isFinite(convParam) && convParam > 0 ? convParam : null;

  const appQuery = useApp(appId);
  const listQuery = useLogConversations(
    appId,
    buildLogListParams(range, feedback, 1, 50, keyword),
  );
  const detailQuery = useLogConversation(appId, selectedConv);

  if (isForbidden(appQuery.error) || isForbidden(listQuery.error) || isForbidden(detailQuery.error)) {
    return (
      <Alert
        type="error"
        showIcon
        data-testid="logs-forbidden"
        message="没有权限查看对话日志"
        description="对话日志包含终端用户的提问，只有应用的“编辑”权限可以查看。"
      />
    );
  }

  const app = appQuery.data;
  if (appQuery.isLoading || !app) {
    return null;
  }

  const conversations = listQuery.data?.items ?? [];

  const selectConversation = (conversationId: number) => {
    const next = new URLSearchParams(searchParams);
    next.set("conv", String(conversationId));
    setSearchParams(next);
  };

  return (
    <div>
      <AppHeader app={app} active="logs" />

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <Card size="small" title="会话" style={{ width: 340, flexShrink: 0 }}>
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Select
              aria-label="时间范围"
              style={{ width: "100%" }}
              value={range}
              options={LOG_RANGE_OPTIONS}
              onChange={(value) => setRange(value)}
            />
            <Radio.Group
              size="small"
              optionType="button"
              value={feedback}
              onChange={(event) => setFeedback(event.target.value as LogFeedbackFilter)}
            >
              {LOG_FEEDBACK_OPTIONS.map((option) => (
                <Radio.Button key={option.value} value={option.value}>
                  {option.label}
                </Radio.Button>
              ))}
            </Radio.Group>
            <Input.Search
              allowClear
              placeholder="搜索用户或会话标题"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
          </Space>

          <List
            style={{ marginTop: 8 }}
            loading={listQuery.isLoading}
            dataSource={conversations}
            locale={{ emptyText: "没有会话" }}
            renderItem={(item) => (
              <List.Item
                data-testid={`log-conv-${item.conversation_id}`}
                onClick={() => selectConversation(item.conversation_id)}
                style={{
                  cursor: "pointer",
                  background: item.conversation_id === selectedConv ? "#e6f4ff" : undefined,
                }}
              >
                <Space direction="vertical" size={0} style={{ width: "100%" }}>
                  <Space wrap>
                    <b>{item.user.name}</b>
                    <span>{item.title}</span>
                    {item.has_down_feedback ? <Tag color="red">👎</Tag> : null}
                    {item.user_deleted ? <Tag>用户已删除</Tag> : null}
                  </Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {item.org.name} · {dayjs(item.started_at).format("MM-DD HH:mm")} ·{" "}
                    {item.turn_count} 轮
                    {item.version_no != null ? ` · v${item.version_no}` : ""}
                  </Typography.Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>

        <div style={{ flex: 1, minWidth: 0 }}>
          {selectedConv === null ? (
            <Empty description="从左侧选择一个会话" />
          ) : detailQuery.isLoading ? (
            <Spin />
          ) : detailQuery.data ? (
            <ConversationDetail conversation={detailQuery.data} onPreview={setPreview} />
          ) : detailQuery.error ? (
            <Alert type="error" showIcon message={getErrorMessage(detailQuery.error)} />
          ) : null}
        </div>
      </div>

      <LogCitationPreviewModal
        appId={app.id}
        target={preview}
        onClose={() => setPreview(null)}
      />
    </div>
  );
}

interface ConversationDetailProps {
  conversation: LogConversationOut;
  onPreview: (target: { answerId: number; n: number }) => void;
}

function ConversationDetail({ conversation, onPreview }: ConversationDetailProps) {
  return (
    <Card
      title={
        <Space wrap>
          <span>
            {conversation.user.name} · {conversation.title}
          </span>
          {conversation.user_deleted ? <Tag>用户已删除</Tag> : null}
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">
        {conversation.org.name} · {dayjs(conversation.created_at).format("YYYY-MM-DD HH:mm")} ·{" "}
        {conversation.turns.length} 轮
      </Typography.Paragraph>
      {conversation.turns.map((turn) => (
        <TurnView key={turn.id} turn={turn} onPreview={onPreview} />
      ))}
    </Card>
  );
}

interface TurnViewProps {
  turn: LogTurnOut;
  onPreview: (target: { answerId: number; n: number }) => void;
}

function TurnView({ turn, onPreview }: TurnViewProps) {
  return (
    <div data-testid={`log-turn-${turn.id}`} style={{ marginBottom: 24 }}>
      <Typography.Title level={5} style={{ marginBottom: 8 }}>
        问：{turn.question}
      </Typography.Title>
      {turn.answers.map((answer) => (
        <AnswerView
          key={answer.id}
          answer={answer}
          current={answer.id === turn.current_answer_id}
          onPreview={onPreview}
        />
      ))}
    </div>
  );
}

interface AnswerViewProps {
  answer: LogAnswerOut;
  current: boolean;
  onPreview: (target: { answerId: number; n: number }) => void;
}

function AnswerView({ answer, current, onPreview }: AnswerViewProps) {
  return (
    <div
      data-testid={`log-answer-${answer.id}`}
      style={{ border: "1px solid #f0f0f0", borderRadius: 6, padding: 12, marginBottom: 12 }}
    >
      <Space wrap style={{ marginBottom: 8 }}>
        {!current ? <Tag color="orange">已重新生成</Tag> : null}
        <Tag>{ANSWER_KIND_LABELS[answer.kind] ?? answer.kind}</Tag>
        {answer.feedback ? (
          <Tag color={answer.feedback.rating === "up" ? "green" : "red"}>
            {answer.feedback.rating === "up" ? "点赞" : "点踩"}
          </Tag>
        ) : null}
        {answer.status !== "done" ? (
          <Tag color="red">{ANSWER_STATUS_LABELS[answer.status] ?? answer.status}</Tag>
        ) : null}
      </Space>

      <SafeMarkdown content={answer.content} />

      {answer.citations.length > 0 ? (
        <Space wrap size={4} style={{ marginTop: 8 }}>
          {answer.citations.map((citation) => (
            <Button
              key={citation.n}
              type="link"
              size="small"
              data-testid={`log-citation-${answer.id}-${citation.n}`}
              onClick={() => onPreview({ answerId: answer.id, n: citation.n })}
            >
              [{citation.n}] {citation.doc_name ?? citation.kb_name}
            </Button>
          ))}
        </Space>
      ) : null}

      {answer.error_message ? (
        <Alert type="error" showIcon style={{ marginTop: 8 }} message={answer.error_message} />
      ) : null}

      <AnswerTrace answer={answer} onPreview={onPreview} />
    </div>
  );
}

interface AnswerTraceProps {
  answer: LogAnswerOut;
  onPreview: (target: { answerId: number; n: number }) => void;
}

/** 一条回答的只读处理过程（原型 #v-applog 的“处理过程 · 总耗时”）。 */
function AnswerTrace({ answer, onPreview }: AnswerTraceProps) {
  const [promptStep, setPromptStep] = useState<LogTraceStepOut | null>(null);
  const trace = answer.trace;

  if (!trace || trace.steps.length === 0) {
    return null;
  }

  return (
    <details data-testid={`log-trace-${answer.id}`} style={{ marginTop: 8 }}>
      <summary>处理过程 · 总耗时 {formatDuration(answer.total_ms)}</summary>
      <ul style={{ marginTop: 8 }}>
        {trace.steps.map((step, index) => {
          const summary = stepSummary(step);
          const chunkIds = collectChunkIds(step.output);
          return (
            <li key={`${step.name}-${index}`} data-testid={`log-trace-step-${step.name}`}>
              <b>{STEP_LABELS[step.name] ?? step.name}</b>
              {summary ? `：${summary}` : ""}
              {step.duration_ms != null ? ` ${formatDuration(step.duration_ms)}` : ""}
              {step.name === "generate" ? (
                <Button type="link" size="small" onClick={() => setPromptStep(step)}>
                  查看完整提示词
                </Button>
              ) : null}
              {chunkIds.map((chunkId) => {
                const n = citationNumberForChunk(answer, chunkId);
                if (n == null) {
                  return null;
                }
                return (
                  <Button
                    key={chunkId}
                    type="link"
                    size="small"
                    onClick={() => onPreview({ answerId: answer.id, n })}
                  >
                    查看位置 #{n}
                  </Button>
                );
              })}
            </li>
          );
        })}
      </ul>

      <Modal
        open={promptStep !== null}
        title="完整提示词"
        footer={null}
        width={720}
        onCancel={() => setPromptStep(null)}
      >
        <pre style={{ whiteSpace: "pre-wrap" }}>
          {promptStep ? JSON.stringify(promptStep.input, null, 2) : ""}
        </pre>
      </Modal>
    </details>
  );
}

interface LogCitationPreviewModalProps {
  appId: number;
  target: { answerId: number; n: number } | null;
  onClose: () => void;
}

/** 日志里点击引用分块查看位置：复用 P3-18 的解析结果预览（PDF 直接内嵌返回的页）。 */
function LogCitationPreviewModal({ appId, target, onClose }: LogCitationPreviewModalProps) {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<LogCitationPreviewOut | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const answerId = target?.answerId ?? null;
  const n = target?.n ?? null;

  useEffect(() => {
    if (answerId === null || n === null) {
      setPreview(null);
      setPdfUrl(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPreview(null);
    setPdfUrl(null);
    const url = `/api/v1/apps/${appId}/logs/answers/${answerId}/citations/${n}/preview`;
    fetchWithRefresh(new Request(url, { headers: { Accept: "application/json" } }))
      .then(async (response) => {
        if (cancelled) {
          return;
        }
        if (!response.ok) {
          setError(getErrorMessage(ApiError.from(response.status, await readErrorBody(response))));
          return;
        }
        const contentType = response.headers.get("content-type") ?? "";
        if (contentType.includes("application/json")) {
          setPreview((await response.json()) as LogCitationPreviewOut);
        } else {
          setPdfUrl(url);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("引用预览加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [appId, answerId, n]);

  const elements = (preview?.elements ?? []).map((element) => ({
    ref: element.ref,
    text: element.text,
    type: element.type,
    bbox: null,
    page_no: null,
  }));
  const highlightRefs = (preview?.elements ?? [])
    .filter((element) => element.highlight)
    .map((element) => element.ref);

  return (
    <Modal open={target !== null} title="分块位置" footer={null} width={720} onCancel={onClose}>
      {loading ? <Spin /> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {pdfUrl ? (
        <iframe title="引用 PDF 预览" src={pdfUrl} style={{ width: "100%", height: 520 }} />
      ) : null}
      {preview ? (
        <>
          <Typography.Paragraph type="secondary">
            {preview.doc_name}（{preview.kb_name}）
            {preview.stale ? " · 原文已更新，只显示快照片段" : ""}
            {preview.edited ? " · 人工修改内容" : ""}
          </Typography.Paragraph>
          {preview.content ? (
            <ParseResultView
              result={{
                markdown: preview.content,
                elements,
                edited_at: null,
                edited_by: null,
                engine: "builtin",
                engine_version: null,
                kind: "original",
                ocr_pages: null,
                pages: null,
              }}
              highlightRefs={highlightRefs}
            />
          ) : null}
        </>
      ) : null}
    </Modal>
  );
}
