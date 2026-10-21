import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, appApi, streamChat } from '../../api/client';
import type { Citation, Conversation, Message } from '../../api/types';
import { errorMessage } from '../../utils/format';

export type ChatMode = 'public' | 'draft';

export interface ChatMessage extends Message {
  pending?: boolean;
  local?: boolean;
  stopped?: boolean;
}

interface Options {
  appId: string;
  mode: ChatMode;
  enabled?: boolean;
}

export function useChatSession({ appId, mode, enabled = true }: Options) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [historyError, setHistoryError] = useState<unknown>(null);
  const [sending, setSending] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastTraceId, setLastTraceId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const messagesRef = useRef<ChatMessage[]>([]);
  messagesRef.current = messages;

  const loadConversations = useCallback(async () => {
    if (!enabled) return;
    setLoadingConversations(true);
    setHistoryError(null);
    try {
      const items = await appApi.conversations(appId, mode === 'public');
      setConversations(items);
    } catch (err) {
      setHistoryError(err);
      if (!(err instanceof ApiError && err.status === 401)) {
        setConversations([]);
      }
    } finally {
      setLoadingConversations(false);
    }
  }, [appId, enabled, mode]);

  useEffect(() => {
    if (!enabled) return;
    void loadConversations();
  }, [enabled, loadConversations]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const loadConversation = useCallback(
    async (id: string) => {
      if (sendingRef.current) return;
      setLoadingMessages(true);
      setError(null);
      setNotice(null);
      setStage(null);
      try {
        const items = await appApi.messages(id);
        setMessages(items);
        setConversationId(id);
        setActiveConversationId(id);
      } catch (err) {
        setError(err);
      } finally {
        setLoadingMessages(false);
      }
    },
    [],
  );

  const newConversation = useCallback(() => {
    if (sendingRef.current) return;
    setMessages([]);
    setConversationId(null);
    setActiveConversationId(null);
    setError(null);
    setNotice(null);
    setStage(null);
    setLastTraceId(null);
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const send = useCallback(
    async (rawText: string) => {
      const text = rawText.trim();
      if (!text || sendingRef.current || !enabled) return;
      sendingRef.current = true;
      setSending(true);
      setError(null);
      setNotice(null);
      setStage(mode === 'draft' ? '正在检索知识库并生成回答…' : '正在检索知识库…');
      setLastTraceId(null);

      const userMessage: ChatMessage = {
        id: `local-user-${Date.now()}`,
        role: 'user',
        content: text,
        citations: [],
        createdAt: new Date().toISOString(),
        local: true,
      };
      const assistantId = `local-assistant-${Date.now()}`;
      const assistantPlaceholder: ChatMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        citations: [],
        createdAt: new Date().toISOString(),
        pending: true,
        local: true,
      };
      setMessages((current) => [...current, userMessage, assistantPlaceholder]);

      const controller = new AbortController();
      abortRef.current = controller;
      let streamedText = '';
      let citations: Citation[] = [];
      let completed = false;

      const updateAssistant = (patch: Partial<ChatMessage>) => {
        setMessages((current) =>
          current.map((message) => (message.id === assistantId ? { ...message, ...patch } : message)),
        );
      };

      try {
        await streamChat(
          appId,
          {
            message: text,
            conversationId: conversationId ?? undefined,
            draft: mode === 'draft' ? true : undefined,
          },
          {
            onRetrieval: () => {
              setStage('已检索知识库，正在组织回答…');
            },
            onDelta: (delta) => {
              streamedText += delta;
              setStage(null);
              updateAssistant({ content: streamedText, pending: true });
            },
            onCitations: (next) => {
              citations = next;
              updateAssistant({ citations: next });
            },
            onDone: (response) => {
              completed = true;
              if (response?.conversationId) {
                setConversationId(response.conversationId);
                setActiveConversationId(response.conversationId);
              }
              if (response?.message) {
                const finalMessage: ChatMessage = {
                  ...response.message,
                  citations: response.message.citations?.length ? response.message.citations : citations,
                  local: false,
                  pending: false,
                };
                setMessages((current) =>
                  current.map((message) => (message.id === assistantId ? finalMessage : message)),
                );
                if (response.traceId) setLastTraceId(response.traceId);
                else if (response.message.traceId) setLastTraceId(response.message.traceId);
              } else if (streamedText) {
                updateAssistant({ content: streamedText, citations, pending: false, stopped: true });
              }
            },
            onError: (message) => {
              setError(new Error(message));
            },
          },
          controller.signal,
        );

        if (!completed && streamedText) {
          updateAssistant({ content: streamedText, citations, pending: false, stopped: true });
          setNotice('回答连接已结束，但未收到最终确认；该回答可能未保存到会话记录。');
        } else if (!completed && !streamedText) {
          setMessages((current) => current.filter((message) => message.id !== assistantId));
          setError(new Error('未收到回答内容，请稍后重试。'));
        }
        if (conversationId || completed) void loadConversations();
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          if (streamedText) {
            updateAssistant({
              content: streamedText,
              citations,
              pending: false,
              stopped: true,
            });
            setNotice('已停止生成。当前回答未完整保存，如需反馈请重新提问。');
          } else {
            setMessages((current) => current.filter((message) => message.id !== assistantId));
            setNotice('已停止生成。');
          }
        } else {
          setError(err);
          if (!streamedText) {
            setMessages((current) => current.filter((message) => message.id !== assistantId));
          } else {
            updateAssistant({ content: streamedText, citations, pending: false, stopped: true });
          }
        }
      } finally {
        sendingRef.current = false;
        setSending(false);
        setStage(null);
        abortRef.current = null;
      }
    },
    [appId, conversationId, enabled, loadConversations, mode],
  );

  const submitFeedback = useCallback(
    async (messageId: string, rating: 1 | -1, correction?: string) => {
      if (!messageId || messageId.startsWith('local-')) {
        throw new Error('这条回答尚未保存，暂时无法提交反馈。');
      }
      await appApi.feedback(messageId, { rating, correction });
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? { ...message, feedback: { rating, correction: correction || undefined } }
            : message,
        ),
      );
    },
    [],
  );

  return {
    messages,
    conversations,
    conversationId,
    activeConversationId,
    loadingConversations,
    loadingMessages,
    historyError,
    sending,
    stage,
    error,
    notice,
    lastTraceId,
    setError,
    loadConversations,
    loadConversation,
    newConversation,
    send,
    stop,
    submitFeedback,
  };
}

export function friendlyChatError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return '登录状态已失效，请重新登录后继续。';
  }
  return errorMessage(error);
}
