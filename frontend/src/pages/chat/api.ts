import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";

import { client, fetchWithRefresh, unwrap } from "../../api/client";
import { ApiError, readErrorBody } from "../../api/errors";
import type { components } from "../../api/schema";
import type { ChatMessage } from "../../components/chat/ChatPanel";

/** 终端用户“我的应用”列表项（P4-05）。 */
export type MyApp = components["schemas"]["MyAppOut"];
/** 对话页应用信息（P4-09）：开场白、推荐问题、是否开启下载。 */
export type ChatAppInfo = components["schemas"]["AppChatInfoOut"];
export type Conversation = components["schemas"]["ConversationOut"];
export type Turn = components["schemas"]["TurnOut"];
export type Answer = components["schemas"]["AnswerOut"];
export type Citation = components["schemas"]["CitationOut"];
export type CitationPreview = components["schemas"]["CitationPreviewOut"];
export type Feedback = components["schemas"]["FeedbackOut"];
export type FeedbackIn = components["schemas"]["FeedbackIn"];
export type FeedbackRating = components["schemas"]["FeedbackRating"];
export type FeedbackReason = components["schemas"]["FeedbackReason"];

/** 点踩原因选项（原型 #v-chat；需求 5.4.5）。 */
export const FEEDBACK_REASON_OPTIONS: { value: FeedbackReason; label: string }[] = [
  { value: "wrong_answer", label: "答案错误" },
  { value: "not_answered", label: "没有回答问题" },
  { value: "wrong_citation", label: "引用不对" },
  { value: "other", label: "其他" },
];

/** 点赞 / 点踩的请求体：点赞不带原因与纠错，点踩带选中的原因和纠错内容。 */
export function buildFeedbackBody(
  rating: FeedbackRating,
  reasons: FeedbackReason[] = [],
  correction = "",
): FeedbackIn {
  if (rating === "up") {
    return { rating: "up", reasons: [], correction: null };
  }
  const text = correction.trim();
  return { rating: "down", reasons, correction: text.length > 0 ? text : null };
}

export interface ConversationGroup {
  key: "today" | "earlier";
  label: string;
  items: Conversation[];
}

/** 按最后活动时间把会话分成“今天 / 更早”（本地时区）。 */
export function groupConversations(
  items: Conversation[],
  now: Date = new Date(),
): ConversationGroup[] {
  const today = dayjs(now).format("YYYY-MM-DD");
  const groups: ConversationGroup[] = [
    { key: "today", label: "今天", items: [] },
    { key: "earlier", label: "更早", items: [] },
  ];
  for (const item of items) {
    const index = dayjs(item.last_active_at).format("YYYY-MM-DD") === today ? 0 : 1;
    groups[index]?.items.push(item);
  }
  return groups.filter((group) => group.items.length > 0);
}

/** 新建会话后的默认标题（后端在第一次提问后改成问题前缀）。 */
export function conversationPath(appId: number, conversationId?: number): string {
  return conversationId == null ? `/chat/${appId}` : `/chat/${appId}/${conversationId}`;
}

/** 把服务端的轮次转成对话组件的历史消息；id 用负数，避免与组件内部自增 id 冲突。 */
export function turnsToMessages(turns: Turn[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  let id = 0;
  for (const turn of turns) {
    id -= 1;
    messages.push({
      id,
      role: "user",
      content: turn.question,
      reasoning: "",
      citations: [],
      suggestions: [],
      steps: [],
      trace: null,
      status: "done",
      error: null,
      stats: null,
      answerId: null,
    });
    const answer =
      turn.answers.find((item) => item.id === turn.current_answer_id) ?? turn.answers.at(-1);
    if (!answer) {
      continue;
    }
    id -= 1;
    messages.push({
      id,
      role: "assistant",
      content: answer.content,
      reasoning: answer.reasoning,
      citations: answer.citations,
      suggestions: answer.suggestions,
      steps: [],
      trace: answer.trace,
      status: answer.status,
      error: answer.error_message,
      stats:
        answer.status === "done"
          ? {
              firstTokenMs: answer.first_token_ms,
              totalMs: answer.total_ms,
              inputTokens: answer.input_tokens,
              outputTokens: answer.output_tokens,
            }
          : null,
      answerId: answer.id,
    });
  }
  return messages;
}

// ---------------------------------------------------------------------------
// 查询
// ---------------------------------------------------------------------------

export function useMyApps() {
  return useQuery({
    queryKey: ["my-apps"],
    queryFn: async () => unwrap(await client.GET("/api/v1/my/apps")),
  });
}

export function useChatApp(appId: number | null) {
  return useQuery({
    queryKey: ["chat-app", appId],
    enabled: appId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/chat/apps/{id}", {
          params: { path: { id: appId as number } },
        }),
      ),
  });
}

export function useConversations(appId: number | null) {
  return useQuery({
    queryKey: ["chat-conversations", appId],
    enabled: appId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/chat/apps/{id}/conversations", {
          params: { path: { id: appId as number }, query: { page: 1, page_size: 100 } },
        }),
      ),
  });
}

export function useTurns(conversationId: number | null) {
  return useQuery({
    queryKey: ["chat-turns", conversationId],
    enabled: conversationId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/conversations/{id}/turns", {
          params: { path: { id: conversationId as number } },
        }),
      ),
  });
}

// ---------------------------------------------------------------------------
// 变更
// ---------------------------------------------------------------------------

function useInvalidateConversations(appId: number) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["chat-conversations", appId] });
  };
}

export function useCreateConversation(appId: number) {
  const invalidate = useInvalidateConversations(appId);
  return useMutation({
    mutationFn: async (title?: string) =>
      unwrap(
        await client.POST("/api/v1/chat/apps/{id}/conversations", {
          params: { path: { id: appId } },
          body: { title: title ?? null },
        }),
      ),
    onSuccess: () => invalidate(),
  });
}

export function useRenameConversation(appId: number) {
  const invalidate = useInvalidateConversations(appId);
  return useMutation({
    mutationFn: async ({ id, title }: { id: number; title: string }) =>
      unwrap(
        await client.PATCH("/api/v1/conversations/{id}", {
          params: { path: { id } },
          body: { title },
        }),
      ),
    onSuccess: () => invalidate(),
  });
}

export function useDeleteConversation(appId: number) {
  const invalidate = useInvalidateConversations(appId);
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.DELETE("/api/v1/conversations/{id}", { params: { path: { id } } })),
    onSuccess: () => invalidate(),
  });
}

/** 点赞 / 点踩：再次提交即更新（应用技术方案 12.3）。 */
export async function putFeedback(answerId: number, body: FeedbackIn): Promise<Feedback> {
  return unwrap(
    await client.PUT("/api/v1/answers/{id}/feedback", {
      params: { path: { id: answerId } },
      body,
    }),
  );
}

/** 取消点赞 / 点踩。 */
export async function deleteFeedback(answerId: number): Promise<void> {
  await unwrap(
    await client.DELETE("/api/v1/answers/{id}/feedback", { params: { path: { id: answerId } } }),
  );
}

// ---------------------------------------------------------------------------
// 引用预览与下载
// ---------------------------------------------------------------------------

export function citationPreviewUrl(answerId: number, n: number): string {
  return `/api/v1/answers/${answerId}/citations/${n}/preview`;
}

/** 下载原文件的地址（`download=true` 让服务端带 attachment 头）。 */
export function citationFileUrl(answerId: number, n: number): string {
  return `/api/v1/answers/${answerId}/citations/${n}/file?download=true`;
}

export type CitationPreviewResult =
  | { kind: "pdf"; url: string }
  | { kind: "json"; preview: CitationPreview };

/**
 * 取一条引用的预览：PDF 返回只含被引用页的文件流地址，其余类型返回 JSON
 * （应用技术方案 8.3）。
 */
export async function fetchCitationPreview(
  answerId: number,
  n: number,
): Promise<CitationPreviewResult> {
  const url = citationPreviewUrl(answerId, n);
  const response = await fetchWithRefresh(
    new Request(url, { headers: { Accept: "application/json, application/pdf" } }),
  );
  if (!response.ok) {
    throw ApiError.from(response.status, await readErrorBody(response));
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/pdf")) {
    return { kind: "pdf", url };
  }
  return { kind: "json", preview: (await response.json()) as CitationPreview };
}
