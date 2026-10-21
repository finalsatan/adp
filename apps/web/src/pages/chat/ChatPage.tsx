import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BookOpen,
  History,
  Layers,
  LayoutDashboard,
  MessageSquareText,
  PanelLeft,
  Plus,
  Quote,
  ShieldCheck,
  X,
} from 'lucide-react';
import { appApi } from '../../api/client';
import type { ChatMessage } from '../../components/chat/useChatSession';
import type { Citation } from '../../api/types';
import { useApi, useDocumentTitle } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  BrandMark,
  Button,
  Callout,
  LoadingState,
  Spinner,
} from '../../components/ui';
import {
  AssistantEmpty,
  ChatComposer,
  ChatHistoryPanel,
  ChatMessageList,
  CitationDialog,
  FeedbackModal,
} from '../../components/chat/ChatParts';
import { friendlyChatError, useChatSession } from '../../components/chat/useChatSession';
import { errorMessage, initials } from '../../utils/format';

export function ChatPage() {
  const { id = '' } = useParams();
  const { user, branding, initialized, can, loading: authLoading } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [feedbackTarget, setFeedbackTarget] = useState<ChatMessage | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const appState = useApi(() => appApi.published(id), [id], { enabled: Boolean(id) });
  const published = appState.data;
  const chat = useChatSession({ appId: id, mode: 'public', enabled: Boolean(published) });

  useDocumentTitle(published?.name ?? '知识问答');

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
  }, [chat.messages, chat.stage]);

  const openFeedback = (message: ChatMessage, rating: 1 | -1) => {
    if (rating === 1) {
      void submitFeedback(message, 1, '');
      return;
    }
    setFeedbackTarget(message);
    setFeedbackOpen(true);
  };

  const submitFeedback = async (message: ChatMessage, rating: 1 | -1, correction: string) => {
    try {
      await chat.submitFeedback(message.id, rating, correction || undefined);
      toast.success('感谢反馈，已记录到应用观测');
    } catch (error) {
      toast.error(errorMessage(error));
      throw error;
    }
  };

  const openConversation = (conversationId: string) => {
    setHistoryOpen(false);
    void chat.loadConversation(conversationId);
  };

  const loginHref = `/login?redirect=${encodeURIComponent(`/chat/${id}`)}`;

  if (authLoading || (appState.loading && !appState.data)) {
    return (
      <div className="chat-boot">
        <BrandMark size="lg" />
        <Spinner size={22} label="正在加载应用…" />
      </div>
    );
  }

  if (appState.error) {
    const status = (appState.error as { status?: number }).status;
    if (status === 401) {
      return (
        <div className="chat-standalone-error">
          <div className="chat-error-card">
            <BrandMark size="lg" />
            <h1>登录后使用「{published?.name ?? '知识问答'}」</h1>
            <p>该应用需要公司账号登录。普通 API Key 只能在服务端调用，不能代替浏览器会话。</p>
            <div className="chat-error-actions">
              {initialized ? (
                <Link to={loginHref} className="btn btn-primary btn-lg">
                  去登录
                </Link>
              ) : (
                <Link to="/setup" className="btn btn-primary btn-lg">
                  初始化平台
                </Link>
              )}
              {user && (
                <Link to="/" className="btn btn-secondary btn-lg">
                  返回工作台
                </Link>
              )}
            </div>
          </div>
        </div>
      );
    }
    return (
      <div className="chat-standalone-error">
        <div className="chat-error-card">
          <BrandMark size="lg" />
          <h1>应用暂时不可用</h1>
          <p>{errorMessage(appState.error)}</p>
          <p className="chat-error-hint">
            请确认应用已发布、你拥有访问权限，或联系应用所有者检查可见范围与发布状态。
          </p>
          <div className="chat-error-actions">
            <Button variant="secondary" onClick={appState.reload}>
              重试
            </Button>
            {user && (
              <Link to="/" className="btn btn-secondary btn-lg">
                返回工作台
              </Link>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (!published) {
    return (
      <div className="chat-standalone-error">
        <div className="chat-error-card">
          <BrandMark size="lg" />
          <h1>应用不存在</h1>
          <p>该对话地址无效，或应用未被发布。</p>
        </div>
      </div>
    );
  }

  const showTrace = can('observe:read');

  return (
    <div className="chat-page-root">
      <div className="chat-app">
        <aside className={`chat-sidebar${historyOpen ? ' is-open' : ''}`}>
          <div className="chat-sidebar-brand">
            <BrandMark size="sm" />
            <div>
              <strong>{branding.name}</strong>
              <span>{branding.subtitle}</span>
            </div>
            <button
              type="button"
              className="chat-sidebar-close"
              aria-label="关闭历史会话"
              onClick={() => setHistoryOpen(false)}
            >
              <X size={17} />
            </button>
          </div>
          <ChatHistoryPanel
            conversations={chat.conversations}
            activeId={chat.activeConversationId}
            loading={chat.loadingConversations}
            error={chat.historyError}
            onSelect={openConversation}
            onNew={() => {
              chat.newConversation();
              setHistoryOpen(false);
            }}
            onReload={() => void chat.loadConversations()}
          />
          <div className="chat-sidebar-foot">
            <ShieldCheck size={13} />
            <span>会话仅本人可见 · 引用不提供原文件下载</span>
          </div>
        </aside>
        <button
          type="button"
          className={`chat-history-backdrop${historyOpen ? ' is-visible' : ''}`}
          onClick={() => setHistoryOpen(false)}
          aria-label="关闭历史会话"
        />

        <main className="chat-main">
          <header className="chat-header">
            <div className="chat-header-left">
              <button
                type="button"
                className="chat-mobile-history"
                onClick={() => setHistoryOpen(true)}
                aria-label="打开历史会话"
              >
                <PanelLeft size={19} />
              </button>
              <span className="chat-app-mark">
                <MessageSquareText size={18} />
              </span>
              <div className="chat-app-title">
                <h1>{published.name}</h1>
                <div className="chat-app-sub">
                  <Badge tone="green">v{published.version}</Badge>
                  {published.demo && <Badge tone="amber">本地演示</Badge>}
                  <span>{branding.name} 提供</span>
                </div>
              </div>
            </div>
            <div className="chat-header-actions">
              <button
                type="button"
                className="chat-header-btn"
                onClick={() => {
                  chat.newConversation();
                  setHistoryOpen(false);
                }}
                title="新对话"
              >
                <Plus size={16} />
                <span>新对话</span>
              </button>
              <button
                type="button"
                className="chat-header-btn chat-history-toggle"
                onClick={() => setHistoryOpen(true)}
                title="历史会话"
              >
                <History size={16} />
                <span>历史会话</span>
              </button>
              {user ? (
                <div className="chat-user" title={`${user.name} · ${user.email}`}>
                  <span className="chat-user-avatar">{initials(user.name)}</span>
                  <span className="chat-user-name">{user.name}</span>
                </div>
              ) : (
                <Link to={loginHref} className="btn btn-secondary btn-sm">
                  登录
                </Link>
              )}
              {user && can('apps:read') && (
                <Link to="/apps" className="chat-header-icon" title="返回工作台" aria-label="返回工作台">
                  <LayoutDashboard size={17} />
                </Link>
              )}
            </div>
          </header>

          <div className="chat-body">
            <div className="chat-scroll" ref={scrollRef}>
              <div className="chat-content">
                {published.demo && (
                  <Callout tone="warn">
                    当前应用使用「本地演示」模型，回答为检索片段摘录，不代表真实大模型效果。
                  </Callout>
                )}
                {chat.loadingMessages ? (
                  <div className="chat-loading-messages">
                    <LoadingState label="正在读取历史消息…" />
                  </div>
                ) : chat.messages.length === 0 ? (
                  <div className="chat-welcome">
                    <AssistantEmpty
                      title={published.name}
                      description={published.description || '可以询问这个应用所关联知识库中的内容。'}
                      demo={published.demo}
                    />
                    <div className="chat-value-list">
                      <div>
                        <BookOpen size={16} />
                        <strong>基于知识库</strong>
                        <span>回答只使用应用中配置的知识库内容。</span>
                      </div>
                      <div>
                        <Quote size={16} />
                        <strong>引用可查看</strong>
                        <span>点击引用编号查看被引用的片段与位置。</span>
                      </div>
                      <div>
                        <Layers size={16} />
                        <strong>支持多轮</strong>
                        <span>会话会自动保存，可在左侧历史中继续。</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <ChatMessageList
                    messages={chat.messages}
                    stage={chat.stage}
                    showFeedback
                    onCitation={setCitation}
                    onFeedback={openFeedback}
                    onCorrect={(message) => {
                      setFeedbackTarget(message);
                      setFeedbackOpen(true);
                    }}
                    onTrace={showTrace ? (traceId) => navigate(`/traces/${traceId}`) : undefined}
                    assistantName={published.name}
                    demo={published.demo}
                  />
                )}
              </div>
            </div>

            <div className="chat-composer-wrap">
              <div className="chat-composer-inner">
                {chat.error != null && (
                  <Callout tone="danger" title="回答失败">
                    {friendlyChatError(chat.error)}
                  </Callout>
                )}
                {chat.notice && <Callout tone="warn">{chat.notice}</Callout>}
                {chat.historyError != null && !chat.loadingConversations && (
                  <Callout tone="warn" title="历史会话读取失败">
                    当前仍可继续提问，但无法加载历史记录：{errorMessage(chat.historyError)}
                  </Callout>
                )}
                <ChatComposer
                  onSend={chat.send}
                  sending={chat.sending}
                  onStop={chat.stop}
                  placeholder={`向「${published.name}」提问…`}
                  hint="Enter 发送 · Shift + Enter 换行"
                  autoFocus
                />
              </div>
            </div>
          </div>
        </main>
      </div>

      <CitationDialog citation={citation} appId={id} onClose={() => setCitation(null)} />
      <FeedbackModal
        open={feedbackOpen}
        message={feedbackTarget}
        onClose={() => {
          setFeedbackOpen(false);
          setFeedbackTarget(null);
        }}
        onSubmit={(rating, correction) => {
          if (!feedbackTarget) return Promise.reject(new Error('没有可反馈的回答'));
          return submitFeedback(feedbackTarget, rating, correction);
        }}
      />

      {!user && (
        <div className="chat-auth-float">
          <span>如需保存会话与反馈，请先登录</span>
          <Link to={loginHref} className="btn btn-primary btn-sm">
            登录
          </Link>
        </div>
      )}

      <Link to="/" className="chat-back-home" aria-label="返回工作台" title="返回工作台">
        <ArrowLeft size={15} />
        <span>工作台</span>
      </Link>
    </div>
  );
}
