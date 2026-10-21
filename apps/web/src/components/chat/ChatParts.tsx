import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  ArrowUp,
  Bot,
  CheckCircle2,
  ExternalLink,
  FileText,
  Quote,
  RefreshCw,
  Square,
  ThumbsDown,
  ThumbsUp,
  User,
} from 'lucide-react';
import type { Citation, CitationDetail } from '../../api/types';
import { appApi } from '../../api/client';
import { RichText } from '../RichText';
import {
  Badge,
  Button,
  CopyButton,
  ErrorState,
  IconButton,
  LoadingState,
  Modal,
  Spinner,
  Textarea,
} from '../ui';
import { sourceRefLabel } from '../../utils/permissions';
import { formatDateTime } from '../../utils/format';
import type { ChatMessage } from './useChatSession';

interface MessageListProps {
  messages: ChatMessage[];
  stage?: string | null;
  showFeedback?: boolean;
  onCitation?: (citation: Citation) => void;
  onFeedback?: (message: ChatMessage, rating: 1 | -1) => void;
  onCorrect?: (message: ChatMessage) => void;
  onTrace?: (traceId: string) => void;
  assistantName?: string;
  demo?: boolean;
}

export function ChatMessageList({
  messages,
  stage,
  showFeedback = false,
  onCitation,
  onFeedback,
  onCorrect,
  onTrace,
  assistantName = '知识问答',
  demo = false,
}: MessageListProps) {
  return (
    <div className="message-list">
      {messages.map((message) =>
        message.role === 'user' ? (
          <div key={message.id} className="message message-user">
            <div className="message-avatar message-avatar-user">
              <User size={16} />
            </div>
            <div className="message-main">
              <div className="message-meta">
                <span>你</span>
                <time>{formatDateTime(message.createdAt)}</time>
              </div>
              <div className="message-bubble message-bubble-user">
                <p>{message.content}</p>
              </div>
            </div>
          </div>
        ) : (
          <div key={message.id} className="message message-assistant">
            <div className="message-avatar message-avatar-assistant">
              <Bot size={16} />
            </div>
            <div className="message-main">
              <div className="message-meta">
                <span>{assistantName}</span>
                {demo && <Badge tone="amber">本地演示</Badge>}
                <time>{message.pending ? '生成中' : formatDateTime(message.createdAt)}</time>
              </div>
              <div className="message-bubble message-bubble-assistant">
                {message.pending && !message.content ? (
                  <div className="thinking">
                    <Spinner size={16} />
                    <span>{stage ?? '正在组织回答…'}</span>
                  </div>
                ) : (
                  <>
                    <RichText content={message.content} citations={message.citations} onCitation={onCitation} />
                    {message.pending && <span className="stream-caret" aria-hidden />}
                  </>
                )}

                {message.citations?.length > 0 && (
                  <div className="citation-list">
                    <div className="citation-list-label">
                      <Quote size={13} />
                      引用来源
                    </div>
                    <div className="citation-chips">
                      {message.citations.map((citation) => (
                        <button
                          key={`${message.id}-${citation.chunkId}-${citation.index}`}
                          type="button"
                          className="citation-chip"
                          onClick={() => onCitation?.(citation)}
                          title={citation.content}
                        >
                          <span className="citation-chip-index">{citation.index}</span>
                          <FileText size={13} />
                          <span className="citation-chip-name">{citation.documentName}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {message.stopped && (
                  <div className="message-note">回答未完整生成，未保存为可反馈的正式回答。</div>
                )}

                {!message.pending && message.content && (
                  <div className="message-actions">
                    <CopyButton value={message.content} label="复制" />
                    {showFeedback && !message.local && message.id && (
                      <>
                        <button
                          type="button"
                          className={`message-action${message.feedback?.rating === 1 ? ' is-active' : ''}`}
                          onClick={() => onFeedback?.(message, 1)}
                        >
                          <ThumbsUp size={14} />
                          <span>有帮助</span>
                        </button>
                        <button
                          type="button"
                          className={`message-action${message.feedback?.rating === -1 ? ' is-active' : ''}`}
                          onClick={() => onFeedback?.(message, -1)}
                        >
                          <ThumbsDown size={14} />
                          <span>没帮助</span>
                        </button>
                        <button
                          type="button"
                          className={`message-action${message.feedback?.correction ? ' is-active' : ''}`}
                          onClick={() => onCorrect?.(message)}
                        >
                          <span>纠错</span>
                        </button>
                      </>
                    )}
                    {message.traceId && onTrace && (
                      <button
                        type="button"
                        className="message-action"
                        onClick={() => onTrace(message.traceId as string)}
                      >
                        <ExternalLink size={13} />
                        <span>调用过程</span>
                      </button>
                    )}
                  </div>
                )}
                {message.feedback?.correction && (
                  <div className="message-correction">
                    <CheckCircle2 size={14} />
                    <span>已提交纠错：{message.feedback.correction}</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        ),
      )}
    </div>
  );
}

interface ComposerProps {
  onSend: (text: string) => void | Promise<void>;
  disabled?: boolean;
  sending?: boolean;
  onStop?: () => void;
  placeholder?: string;
  hint?: string;
  autoFocus?: boolean;
}

export function ChatComposer({
  onSend,
  disabled,
  sending,
  onStop,
  placeholder = '输入问题，查看知识库中的依据…',
  hint = 'Enter 发送 · Shift + Enter 换行',
  autoFocus = false,
}: ComposerProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus]);

  const resize = () => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  };

  const submit = () => {
    const text = value.trim();
    if (!text || disabled || sending) return;
    setValue('');
    requestAnimationFrame(resize);
    void onSend(text);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !composingRef.current) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer">
      <div className="composer-box">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
            resize();
          }}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          placeholder={placeholder}
          rows={1}
          disabled={disabled}
          aria-label="输入问题"
        />
        {sending ? (
          <button type="button" className="composer-send composer-stop" onClick={onStop} aria-label="停止生成">
            <Square size={15} fill="currentColor" />
          </button>
        ) : (
          <button
            type="button"
            className="composer-send"
            onClick={submit}
            disabled={disabled || !value.trim()}
            aria-label="发送"
          >
            <ArrowUp size={18} />
          </button>
        )}
      </div>
      <div className="composer-hint">
        <span>{hint}</span>
        <span>回答由知识库检索生成，请核对引用原文</span>
      </div>
    </div>
  );
}

interface CitationDialogProps {
  citation: Citation | null;
  appId: string;
  onClose: () => void;
}

export function CitationDialog({ citation, appId, onClose }: CitationDialogProps) {
  const [detail, setDetail] = useState<CitationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!citation) {
      setDetail(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    appApi
      .citation(citation.chunkId, appId)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [citation, appId]);

  if (!citation) return null;

  const chunk = (detail as { chunk?: Partial<CitationDetail> } | null)?.chunk;
  const merged = { ...citation, ...(chunk ?? {}), ...(detail ?? {}) } as Citation & CitationDetail;
  const headingPath = Array.isArray(merged.headingPath) ? merged.headingPath : [];
  const content = merged.content || citation.content;

  return (
    <Modal
      open
      onClose={onClose}
      title="引用原文"
      description="仅展示被引用的知识片段，不提供原文件下载。"
      size="lg"
      footer={
        <>
          <span className="modal-footer-note">
            文档：{merged.documentName || citation.documentName}
          </span>
          <Button variant="secondary" onClick={onClose}>
            关闭
          </Button>
        </>
      }
    >
      <div className="citation-dialog">
        <div className="citation-source-head">
          <div className="citation-source-icon">
            <FileText size={18} />
          </div>
          <div>
            <strong>{merged.documentName || citation.documentName}</strong>
            <div className="citation-source-path">
              {headingPath.length > 0 ? headingPath.join(' › ') : '未提供标题路径'}
            </div>
            <div className="citation-source-ref">
              {sourceRefLabel(merged.sourceRef ?? citation.sourceRef)}
              {citation.index ? ` · 引用 [${citation.index}]` : ''}
            </div>
          </div>
        </div>
        {loading ? (
          <LoadingState label="正在读取引用片段…" />
        ) : error ? (
          <>
            <ErrorState error={error} compact />
            <div className="citation-fallback">
              <div className="citation-fallback-label">会话中保存的引用片段</div>
              <div className="citation-content">{content}</div>
            </div>
          </>
        ) : (
          <div className="citation-content">
            <RichText content={content} compact />
          </div>
        )}
      </div>
    </Modal>
  );
}

interface FeedbackModalProps {
  open: boolean;
  message: ChatMessage | null;
  onClose: () => void;
  onSubmit: (rating: 1 | -1, correction: string) => Promise<void>;
}

export function FeedbackModal({ open, message, onClose, onSubmit }: FeedbackModalProps) {
  const [rating, setRating] = useState<1 | -1>(-1);
  const [correction, setCorrection] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!open) return;
    setRating(message?.feedback?.rating ?? -1);
    setCorrection(message?.feedback?.correction ?? '');
    setError(null);
  }, [open, message]);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(rating, correction.trim());
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="反馈回答质量"
      description="反馈会进入应用观测，点踩与纠错可加入评测集。"
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" loading={submitting} onClick={submit}>
            提交反馈
          </Button>
        </>
      }
    >
      <div className="feedback-form">
        <div className="feedback-rating">
          <button
            type="button"
            className={`feedback-choice${rating === 1 ? ' is-active' : ''}`}
            onClick={() => setRating(1)}
          >
            <ThumbsUp size={18} />
            有帮助
          </button>
          <button
            type="button"
            className={`feedback-choice${rating === -1 ? ' is-active' : ''}`}
            onClick={() => setRating(-1)}
          >
            <ThumbsDown size={18} />
            没帮助
          </button>
        </div>
        <label className="field-label" htmlFor="feedback-correction">
          纠错或补充说明（可选）
        </label>
        <Textarea
          id="feedback-correction"
          value={correction}
          onChange={(event) => setCorrection(event.target.value)}
          rows={4}
          placeholder="例如：正确的住宿标准是 600 元，回答引用了旧版本制度。"
        />
        {error != null && <div className="inline-error">{error instanceof Error ? error.message : '提交失败'}</div>}
        <p className="field-hint">请勿填写密钥、个人敏感信息或与问题无关的内容。</p>
      </div>
    </Modal>
  );
}

export function AssistantEmpty({
  title,
  description,
  demo,
}: {
  title: string;
  description?: string;
  demo?: boolean;
}) {
  return (
    <div className="assistant-empty">
      <div className="assistant-empty-mark">
        <Bot size={26} />
      </div>
      <h2>{title}</h2>
      <p>{description || '可以询问这个应用所关联知识库中的内容，回答会标注引用来源。'}</p>
      {demo && (
        <Badge tone="amber">
          当前应用使用本地演示模型，回答为原文摘录，不能代表真实模型效果。
        </Badge>
      )}
    </div>
  );
}

export function ChatHistoryPanel({
  conversations,
  activeId,
  loading,
  error,
  onSelect,
  onNew,
  onReload,
}: {
  conversations: Array<{ id: string; title: string; updatedAt: string }>;
  activeId: string | null;
  loading: boolean;
  error: unknown;
  onSelect: (id: string) => void;
  onNew: () => void;
  onReload: () => void;
}) {
  return (
    <aside className="chat-history">
      <div className="chat-history-head">
        <span>历史会话</span>
        <IconButton label="刷新历史会话" onClick={onReload}>
          <RefreshCw size={14} />
        </IconButton>
      </div>
      <Button variant="secondary" block icon={<span className="plus-mark">+</span>} onClick={onNew}>
        新对话
      </Button>
      <div className="chat-history-list">
        {loading ? (
          <div className="chat-history-loading">
            <Spinner size={16} />
            读取会话…
          </div>
        ) : error ? (
          <ErrorState error={error} compact onRetry={onReload} />
        ) : conversations.length === 0 ? (
          <div className="chat-history-empty">暂无历史会话，提问后会自动保存。</div>
        ) : (
          conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              className={`history-item${conversation.id === activeId ? ' is-active' : ''}`}
              onClick={() => onSelect(conversation.id)}
            >
              <span className="history-title">{conversation.title || '未命名会话'}</span>
              <span className="history-time">{formatDateTime(conversation.updatedAt)}</span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
