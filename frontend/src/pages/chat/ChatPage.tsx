import { DislikeOutlined, LikeOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Input, Result, Space, Spin, Tag, Typography } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, getErrorMessage, isApiError } from "../../api/errors";
import { ChatPanel, type ChatRunRequest } from "../../components/chat/ChatPanel";
import type { Citation } from "../../components/chat/citation";
import { notifyError } from "../../lib/feedback";
import { streamSSE, type ChatSseEvent } from "../../lib/sse";
import { CitationPreviewDrawer } from "./CitationPreviewDrawer";
import { ConversationList } from "./ConversationList";
import {
  FEEDBACK_REASON_OPTIONS,
  buildFeedbackBody,
  conversationPath,
  deleteFeedback,
  putFeedback,
  turnsToMessages,
  useChatApp,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
  useRenameConversation,
  useTurns,
  type FeedbackRating,
  type FeedbackReason,
} from "./api";

function parseConversationId(value: string | undefined): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/** 终端用户对话页（原型 #v-chat）：左侧会话列表 + 共用对话组件 + 引用预览抽屉。 */
export default function ChatPage() {
  const { appId: appIdParam, conversationId: convIdParam } = useParams();
  const appId = Number(appIdParam);
  const validAppId = Number.isFinite(appId) ? appId : null;
  const paramConvId = parseConversationId(convIdParam);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [sessionKey, setSessionKey] = useState<number | "new">(paramConvId ?? "new");
  const [activeConvId, setActiveConvId] = useState<number | null>(paramConvId);
  const activeConvIdRef = useRef<number | null>(paramConvId);
  const lastTurnIdRef = useRef<number | null>(null);
  // “新建会话”时递增，强制重挂对话面板：第一次提问用 replaceState 改写地址后，
  // react-router 的位置仍是 `/chat/{appId}`，sessionKey 不变，仅靠路由无法重置。
  const [newSeq, setNewSeq] = useState(0);
  const [preview, setPreview] = useState<{ answerId: number; citation: Citation } | null>(null);

  useEffect(() => {
    setSessionKey(paramConvId ?? "new");
    setActiveConvId(paramConvId);
    activeConvIdRef.current = paramConvId;
  }, [paramConvId]);

  const appQuery = useChatApp(validAppId);
  const conversationsQuery = useConversations(validAppId);
  const turnsQuery = useTurns(sessionKey === "new" ? null : sessionKey);

  const createConversation = useCreateConversation(appId);
  const renameConversation = useRenameConversation(appId);
  const deleteConversation = useDeleteConversation(appId);

  useEffect(() => {
    const last = turnsQuery.data?.at(-1);
    lastTurnIdRef.current = last ? last.id : null;
  }, [turnsQuery.data]);

  const initialMessages = useMemo(() => turnsToMessages(turnsQuery.data ?? []), [turnsQuery.data]);

  const startRun = async ({ question, signal, onEvent, mode }: ChatRunRequest) => {
    const emit = (event: ChatSseEvent) => {
      if (event.type === "RUN_STARTED") {
        lastTurnIdRef.current = event.turn_id;
        if (activeConvIdRef.current !== event.conversation_id) {
          activeConvIdRef.current = event.conversation_id;
          setActiveConvId(event.conversation_id);
          // 新会话第一次提问后改写地址，不重新加载页面（应用技术方案 15）。
          window.history.replaceState(null, "", conversationPath(appId, event.conversation_id));
        }
        void queryClient.invalidateQueries({ queryKey: ["chat-conversations", appId] });
      }
      onEvent(event);
    };

    if (mode === "regenerate") {
      const turnId = lastTurnIdRef.current;
      if (turnId == null) {
        throw new ApiError(400, { message: "没有可重新生成的回答" });
      }
      await streamSSE<ChatSseEvent>(`/api/v1/turns/${turnId}:regenerate`, undefined, {
        signal,
        onEvent: emit,
      });
      return;
    }

    let conversationId = activeConvIdRef.current;
    if (conversationId == null) {
      const created = await createConversation.mutateAsync();
      conversationId = created.id;
    }
    await streamSSE<ChatSseEvent>(
      `/api/v1/conversations/${conversationId}/turns`,
      { question },
      { signal, onEvent: emit },
    );
  };

  if (appQuery.error) {
    const error = appQuery.error;
    const code = isApiError(error) ? error.code : undefined;
    if (code === "APP_NOT_IN_SCOPE") {
      return <Result status="403" title="你没有使用该应用的权限" />;
    }
    if (code === "APP_NOT_PUBLISHED" || code === "APP_DISABLED") {
      return <Result status="warning" title="应用暂不可用" />;
    }
    return <Result status="error" title="加载失败" subTitle={getErrorMessage(error)} />;
  }

  const app = appQuery.data;
  if (appQuery.isLoading || !app) {
    return (
      <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }

  const startNewConversation = () => {
    activeConvIdRef.current = null;
    lastTurnIdRef.current = null;
    setActiveConvId(null);
    setSessionKey("new");
    setNewSeq((value) => value + 1);
    navigate(conversationPath(appId));
  };

  return (
    <div style={{ display: "flex", gap: 16, height: "calc(100vh - 112px)" }}>
      <div style={{ width: 260, flexShrink: 0 }}>
        <ConversationList
          conversations={conversationsQuery.data?.items ?? []}
          activeId={activeConvId}
          loading={conversationsQuery.isLoading}
          onNew={startNewConversation}
          onSelect={(id) => navigate(conversationPath(appId, id))}
          onRename={(conversation, title) =>
            renameConversation.mutate({ id: conversation.id, title })
          }
          onDelete={(conversation) =>
            deleteConversation.mutate(conversation.id, {
              onSuccess: () => {
                if (conversation.id === activeConvId) {
                  navigate(conversationPath(appId));
                }
              },
            })
          }
        />
      </div>

      <div style={{ flex: 1, minWidth: 0, height: "100%", overflow: "auto" }}>
        {sessionKey !== "new" && turnsQuery.isLoading ? (
          <Spin />
        ) : (
          <ChatPanel
            key={sessionKey === "new" ? `new-${newSeq}` : String(sessionKey)}
            header={app.name}
            opening={app.opening}
            openingQuestions={app.opening_questions}
            initialMessages={initialMessages}
            startRun={startRun}
            onCitationClick={(citation, answerId) => {
              if (answerId != null) {
                setPreview({ answerId, citation });
              }
            }}
            renderAnswerActions={(message) => <AnswerFeedback answerId={message.answerId} />}
            emptyHint="输入问题开始对话。"
          />
        )}
      </div>

      <CitationPreviewDrawer
        open={preview !== null}
        answerId={preview?.answerId ?? null}
        citation={preview?.citation ?? null}
        allowDownload={app.allow_download}
        onClose={() => setPreview(null)}
      />
    </div>
  );
}

interface AnswerFeedbackProps {
  answerId: number | null;
}

/** 一条回答的点赞 / 点踩与纠错（需求 5.4.5，原型 #v-chat）。 */
export function AnswerFeedback({ answerId }: AnswerFeedbackProps) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);
  const [correction, setCorrection] = useState("");
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  if (answerId == null) {
    return null;
  }

  const handleUp = () => {
    void (async () => {
      setPending(true);
      try {
        if (rating === "up") {
          await deleteFeedback(answerId);
          setRating(null);
        } else {
          await putFeedback(answerId, buildFeedbackBody("up"));
          setRating("up");
          setOpen(false);
        }
      } catch (error) {
        notifyError(getErrorMessage(error));
      } finally {
        setPending(false);
      }
    })();
  };

  const handleSubmitDown = () => {
    void (async () => {
      setPending(true);
      try {
        await putFeedback(answerId, buildFeedbackBody("down", reasons, correction));
        setRating("down");
        setOpen(false);
      } catch (error) {
        notifyError(getErrorMessage(error));
      } finally {
        setPending(false);
      }
    })();
  };

  const handleCancelDown = () => {
    void (async () => {
      if (rating === "down") {
        try {
          await deleteFeedback(answerId);
          setRating(null);
        } catch (error) {
          notifyError(getErrorMessage(error));
        }
      }
      setOpen(false);
    })();
  };

  const toggleReason = (reason: FeedbackReason, checked: boolean) => {
    setReasons((current) =>
      checked ? [...current, reason] : current.filter((item) => item !== reason),
    );
  };

  return (
    <div style={{ marginTop: 4 }}>
      <Space size="small">
        <Button
          type={rating === "up" ? "primary" : "text"}
          size="small"
          icon={<LikeOutlined />}
          aria-label="点赞"
          loading={pending && rating !== "down"}
          onClick={handleUp}
        />
        <Button
          type={rating === "down" ? "primary" : "text"}
          size="small"
          danger={rating === "down"}
          icon={<DislikeOutlined />}
          aria-label="点踩"
          onClick={() => setOpen(true)}
        />
      </Space>

      {open ? (
        <div
          data-testid="feedback-panel"
          style={{ marginTop: 8, padding: 12, background: "#fafafa", borderRadius: 6 }}
        >
          <Typography.Text strong>哪里不对？</Typography.Text>
          <div style={{ marginTop: 8 }}>
            {FEEDBACK_REASON_OPTIONS.map((option) => (
              <Tag.CheckableTag
                key={option.value}
                checked={reasons.includes(option.value)}
                onChange={(checked) => toggleReason(option.value, checked)}
              >
                {option.label}
              </Tag.CheckableTag>
            ))}
          </div>
          <Input.TextArea
            value={correction}
            placeholder="选填：你认为正确的答案是什么？（纠错）"
            autoSize={{ minRows: 2, maxRows: 4 }}
            style={{ marginTop: 8 }}
            onChange={(event) => setCorrection(event.target.value)}
          />
          <Space style={{ marginTop: 8 }}>
            <Button size="small" onClick={handleCancelDown}>
              取消
            </Button>
            <Button
              size="small"
              type="primary"
              loading={pending}
              data-testid="feedback-submit"
              onClick={handleSubmitDown}
            >
              提交
            </Button>
          </Space>
        </div>
      ) : null}
    </div>
  );
}
