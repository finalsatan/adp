import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Bot,
  ChevronDown,
  Code2,
  ExternalLink,
  FileText,
  Layers,
  Link2,
  Plus,
  Rocket,
  Save,
  Trash2,
} from 'lucide-react';
import type {
  AppItem,
  Citation,
  KnowledgeBase,
  RetrievalConfig,
} from '../../api/types';
import { appApi, knowledgeApi, modelApi, traceApi } from '../../api/client';
import { useApi, useDocumentTitle } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  CopyButton,
  DescriptionList,
  EmptyState,
  ErrorState,
  Field,
  Input,
  JsonBlock,
  LoadingState,
  PageHeader,
  PermissionDenied,
  SectionHeader,
  Select,
  StatusBadge,
  Tabs,
  Textarea,
  VisibilityBadge,
  useConfirm,
} from '../../components/ui';
import { RetrievalConfigFields } from '../../components/RetrievalConfigFields';
import {
  AssistantEmpty,
  ChatComposer,
  ChatMessageList,
  CitationDialog,
} from '../../components/chat/ChatParts';
import { friendlyChatError, useChatSession } from '../../components/chat/useChatSession';
import {
  DEFAULT_RETRIEVAL_CONFIG,
  RETRIEVAL_MODE_LABELS,
} from '../../utils/permissions';
import {
  errorMessage,
  formatDateTime,
  formatDuration,
  formatNumber,
} from '../../utils/format';
import type { PublishResult } from '../../api/types';

type EditorTab = 'config' | 'debug' | 'publish';

interface AppFormState {
  name: string;
  description: string;
  visibility: AppItem['visibility'];
  kbIds: string[];
  chatModelId: string;
  systemPrompt: string;
  retrievalConfig: RetrievalConfig;
}

export function AppEditorPage() {
  const { id = '' } = useParams();
  const { currentOrg, can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const orgId = currentOrg?.id;
  const [tab, setTab] = useState<EditorTab>('config');
  const [form, setForm] = useState<AppFormState | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [publishResult, setPublishResult] = useState<PublishResult | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<unknown>(null);
  const [deleting, setDeleting] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement>(null);

  const appState = useApi(() => appApi.get(id), [id, orgId], { enabled: Boolean(id) });
  const modelsState = useApi(() => modelApi.list().catch(() => []), [orgId]);
  const kbsState = useApi(() => knowledgeApi.list().catch(() => []), [orgId]);
  const app = appState.data;
  const models = modelsState.data ?? [];
  const kbs = kbsState.data ?? [];
  const chatModels = models.filter((model) => model.kind === 'chat' && model.enabled);
  const canEdit = app?.access ? app.access.edit : can('apps:write');
  const canPublish = app?.access ? app.access.publish : can('apps:publish');

  const chat = useChatSession({ appId: id, mode: 'draft', enabled: Boolean(app) && canEdit });
  const lastTraceId = chat.lastTraceId;
  const traceState = useApi(() => traceApi.get(lastTraceId as string), [lastTraceId], {
    enabled: Boolean(lastTraceId),
  });
  const publishedState = useApi(() => appApi.published(id), [id, app?.status, app?.publishedVersion], {
    enabled: Boolean(id) && app?.status === 'published',
  });

  useDocumentTitle(app?.name ?? '知识问答配置');

  useEffect(() => {
    if (!app) return;
    setForm({
      name: app.name,
      description: app.description ?? '',
      visibility: app.visibility,
      kbIds: [...(app.kbIds ?? [])],
      chatModelId: app.chatModelId,
      systemPrompt: app.systemPrompt ?? '',
      retrievalConfig: app.retrievalConfig ?? { ...DEFAULT_RETRIEVAL_CONFIG },
    });
    setDirty(false);
    setSaveError(null);
  }, [app?.id, orgId]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  useEffect(() => {
    const element = chatScrollRef.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
  }, [chat.messages, chat.stage]);

  const patchForm = (patch: Partial<AppFormState>) => {
    setForm((current) => (current ? { ...current, ...patch } : current));
    setDirty(true);
  };

  const save = async (): Promise<boolean> => {
    if (!form) return false;
    if (!form.name.trim()) {
      setSaveError(new Error('应用名称不能为空。'));
      return false;
    }
    if (!form.chatModelId) {
      setSaveError(new Error('请选择对话模型。'));
      return false;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await appApi.update(id, {
        name: form.name.trim(),
        description: form.description.trim(),
        visibility: form.visibility,
        kbIds: form.kbIds,
        chatModelId: form.chatModelId,
        systemPrompt: form.systemPrompt,
        retrievalConfig: form.retrievalConfig,
      });
      setDirty(false);
      toast.success('配置已保存');
      appState.reload();
      return true;
    } catch (error) {
      setSaveError(error);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const submitConfig = (event: FormEvent) => {
    event.preventDefault();
    void save();
  };

  const publish = async () => {
    if (!app) return;
    if (
      !(await confirm({
        title: `发布「${app.name}」？`,
        description:
          '发布会把当前已保存配置固化为一个不可变版本，终端用户将使用该版本。后续修改需要重新发布。',
        confirmLabel: '发布新版本',
      }))
    ) {
      return;
    }
    setPublishing(true);
    setPublishError(null);
    try {
      const result = await appApi.publish(id);
      setPublishResult(result);
      toast.success(`已发布 v${result.version}`);
      appState.reload();
      publishedState.reload();
    } catch (error) {
      setPublishError(error);
    } finally {
      setPublishing(false);
    }
  };

  const removeApp = async () => {
    if (!app) return;
    if (
      !(await confirm({
        title: `删除应用「${app.name}」？`,
        description: '应用配置、会话与发布入口将失效，API Key 也会失去绑定的应用。此操作不可撤销。',
        confirmLabel: '删除应用',
        danger: true,
      }))
    ) {
      return;
    }
    setDeleting(true);
    try {
      await appApi.remove(id);
      toast.success('应用已删除');
      navigate('/apps');
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setDeleting(false);
    }
  };

  if (!can('apps:read')) {
    return (
      <div className="page">
        <PageHeader title="知识问答配置" />
        <Card>
          <PermissionDenied permission="apps:read" />
        </Card>
      </div>
    );
  }

  if (appState.loading && !app) {
    return (
      <div className="page">
        <LoadingState label="正在读取应用配置…" />
      </div>
    );
  }

  if (appState.error || !app || !form) {
    return (
      <div className="page">
        <PageHeader
          title="知识问答"
          description="无法读取该应用。"
          back={
            <Link to="/apps" className="back-link">
              <ArrowLeft size={15} /> 返回应用列表
            </Link>
          }
        />
        <ErrorState error={appState.error ?? new Error('应用不存在')} onRetry={appState.reload} />
      </div>
    );
  }

  const selectedModel = models.find((model) => model.id === form.chatModelId);
  const demoModel = selectedModel?.provider === 'demo';
  const publishUrl = publishResult?.url ?? `/chat/${app.id}`;
  const absoluteUrl = publishUrl.startsWith('http') ? publishUrl : `${window.location.origin}${publishUrl}`;
  const embedCode =
    publishResult?.embedCode ??
    `<iframe\n  src="${absoluteUrl}"\n  title="${app.name}"\n  width="100%"\n  height="720"\n  style="border:0;border-radius:12px;overflow:hidden"\n  allow="clipboard-write"\n></iframe>`;

  return (
    <div className="page app-editor-page">
      <PageHeader
        back={
          <Link to="/apps" className="back-link">
            <ArrowLeft size={15} /> 返回知识问答
          </Link>
        }
        eyebrow={<span className="breadcrumb">应用开发 / 知识问答</span>}
        title={
          <span className="title-with-badge">
            {app.name}
            <StatusBadge status={app.status} />
            {app.publishedVersion ? <Badge tone="green">已发布 v{app.publishedVersion}</Badge> : <Badge tone="amber">草稿</Badge>}
            {dirty && <Badge tone="amber">未保存</Badge>}
          </span>
        }
        description={
          <span className="kb-meta-line">
            <VisibilityBadge visibility={app.visibility} /> · {form.kbIds.length} 个知识库 ·{' '}
            {selectedModel?.name ?? (form.chatModelId || '未选择模型')} · 创建于 {formatDateTime(app.createdAt)}
          </span>
        }
        actions={
          <>
            {app.status === 'published' && (
              <Link to={`/chat/${app.id}`} target="_blank" rel="noreferrer" className="btn btn-secondary btn-md">
                <ExternalLink size={15} />
                <span>打开对话页</span>
              </Link>
            )}
            {canEdit && (
              <Button variant="primary" icon={<Save size={15} />} loading={saving} onClick={() => save()} disabled={!dirty && !saveError}>
                保存配置
              </Button>
            )}
          </>
        }
      />

      {saveError != null && (
        <Callout tone="danger" title="配置保存失败">
          {errorMessage(saveError)}
        </Callout>
      )}

      <div className="kb-tabbar">
        <Tabs
          tabs={[
            { value: 'config', label: '配置' },
            { value: 'debug', label: '草稿调试' },
            { value: 'publish', label: '发布与嵌入' },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {tab === 'config' && (
        <form className="editor-layout" onSubmit={submitConfig}>
          <div className="editor-main">
            <Card>
              <SectionHeader title="基础信息" description="名称和描述会显示在终端对话页与历史会话中。" />
              <div className="form-grid two">
                <Field label="应用名称" htmlFor="editor-name" required>
                  <Input
                    id="editor-name"
                    value={form.name}
                    onChange={(event) => patchForm({ name: event.target.value })}
                    disabled={!canEdit}
                    maxLength={80}
                  />
                </Field>
                <Field label="可见范围" htmlFor="editor-visibility" hint="发布后仍遵守此范围，子组织不自动继承。">
                  <Select
                    id="editor-visibility"
                    value={form.visibility}
                    onChange={(event) =>
                      patchForm({ visibility: event.target.value as AppItem['visibility'] })
                    }
                    disabled={!canEdit}
                  >
                    <option value="private">仅自己</option>
                    <option value="organization">本组织</option>
                    <option value="platform">全平台</option>
                  </Select>
                </Field>
              </div>
              <Field label="应用描述" htmlFor="editor-description">
                <Textarea
                  id="editor-description"
                  rows={2}
                  value={form.description}
                  onChange={(event) => patchForm({ description: event.target.value })}
                  disabled={!canEdit}
                  placeholder="告诉终端用户这个应用能回答什么"
                />
              </Field>
            </Card>

            <Card>
              <SectionHeader
                title="知识库范围"
                description="应用只会检索这里选中的知识库。终端用户不需要知识库管理权限。"
              />
              {kbs.length === 0 ? (
                <EmptyState
                  compact
                  icon={<Layers size={20} />}
                  title="没有可用的知识库"
                  description="先创建知识库并完成文档入库，再回来选择。"
                  action={
                    can('knowledge:write') ? (
                      <Link to="/knowledge" className="btn btn-secondary btn-sm">
                        去知识库
                      </Link>
                    ) : undefined
                  }
                />
              ) : (
                <div className="kb-picker">
                  {kbs.map((kb) => (
                    <KbPickerRow
                      key={kb.id}
                      kb={kb}
                      checked={form.kbIds.includes(kb.id)}
                      disabled={!canEdit}
                      onChange={(checked) =>
                        patchForm({
                          kbIds: checked
                            ? [...form.kbIds, kb.id]
                            : form.kbIds.filter((kbId) => kbId !== kb.id),
                        })
                      }
                    />
                  ))}
                </div>
              )}
              {form.kbIds.length === 0 && (
                <Callout tone="warn">
                  没有选择知识库时，应用无法检索依据，可能只会拒答或给出无引用的回答。
                </Callout>
              )}
            </Card>

            <Card>
              <SectionHeader title="生成模型" description="回答生成使用该模型；检索参数可单独覆盖知识库默认值。" />
              <Field
                label="对话模型"
                htmlFor="editor-model"
                hint={chatModels.length === 0 ? '暂无可用 chat 模型，请先到模型管理接入。' : undefined}
              >
                <Select
                  id="editor-model"
                  value={form.chatModelId}
                  onChange={(event) => patchForm({ chatModelId: event.target.value })}
                  disabled={!canEdit}
                >
                  {!selectedModel && form.chatModelId && (
                    <option value={form.chatModelId}>{form.chatModelId}（当前模型不可用）</option>
                  )}
                  {chatModels.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name} · {model.model}
                      {model.provider === 'demo' ? '（本地演示）' : ''}
                    </option>
                  ))}
                </Select>
              </Field>
              {demoModel && (
                <Callout tone="warn" title="当前使用本地演示模型">
                  回答为检索片段摘录，不调用外部大模型。可以用于验证引用链路，但不能用于效果验收。
                </Callout>
              )}
              <Field
                label="系统提示词"
                htmlFor="editor-prompt"
                hint="用户内容作为不可信引用传给模型；请勿在提示词中写入密钥或敏感信息。"
              >
                <Textarea
                  id="editor-prompt"
                  rows={8}
                  value={form.systemPrompt}
                  onChange={(event) => patchForm({ systemPrompt: event.target.value })}
                  disabled={!canEdit}
                  placeholder="你是企业知识库问答助手…"
                />
              </Field>
            </Card>

            <Card>
              <SectionHeader
                title="检索配置"
                description="覆盖知识库默认检索参数。此配置在发布时会写入版本快照。"
              />
              <RetrievalConfigFields
                value={form.retrievalConfig}
                onChange={(value) => patchForm({ retrievalConfig: value })}
                models={models}
              />
            </Card>

            {canEdit && (
              <Card className="danger-card">
                <SectionHeader title="危险操作" description="删除应用后，发布入口与绑定 API Key 都将失效。" />
                <div className="danger-row">
                  <div>
                    <strong>删除知识问答应用</strong>
                    <p>会话记录与发布版本不会被保留。</p>
                  </div>
                  <Button
                    variant="danger"
                    icon={<Trash2 size={15} />}
                    onClick={removeApp}
                    loading={deleting}
                    disabled={saving}
                  >
                    删除应用
                  </Button>
                </div>
              </Card>
            )}
          </div>

          <aside className="editor-aside">
            <Card className="editor-summary">
              <SectionHeader title="配置摘要" />
              <DescriptionList
                columns={1}
                items={[
                  { label: '状态', value: <StatusBadge status={app.status} /> },
                  { label: '当前发布版本', value: app.publishedVersion ? `v${app.publishedVersion}` : '尚未发布' },
                  { label: '知识库', value: `${form.kbIds.length} 个` },
                  {
                    label: '检索',
                    value: `${RETRIEVAL_MODE_LABELS[form.retrievalConfig.mode]} · topK ${form.retrievalConfig.topK}`,
                  },
                  { label: '阈值', value: String(form.retrievalConfig.threshold) },
                  {
                    label: '重排',
                    value: form.retrievalConfig.rerankModelId
                      ? models.find((model) => model.id === form.retrievalConfig.rerankModelId)?.name ?? '已配置'
                      : '未启用',
                  },
                  {
                    label: '改写',
                    value: form.retrievalConfig.rewriteModelId
                      ? models.find((model) => model.id === form.retrievalConfig.rewriteModelId)?.name ?? '已配置'
                      : '未启用',
                  },
                ]}
              />
              <div className="aside-actions">
                <Button variant="secondary" block onClick={() => setTab('debug')} icon={<Bot size={15} />}>
                  去草稿调试
                </Button>
                <Button variant="secondary" block onClick={() => setTab('publish')} icon={<Rocket size={15} />}>
                  发布与嵌入
                </Button>
              </div>
            </Card>
            <Callout tone="info" title="草稿与已发布版本分开">
              编辑只影响草稿。终端用户始终使用最近一次发布的不可变版本，直到你再次发布。
            </Callout>
            {!canEdit && <Callout tone="warn">当前角色只能查看，不能修改应用配置。</Callout>}
          </aside>
        </form>
      )}

      {tab === 'debug' && (
        <div className="debug-layout">
          <Card padded={false} className="debug-chat-card">
            <div className="debug-chat-head">
              <div>
                <strong>草稿调试</strong>
                <span>调用未发布的当前草稿配置，不产生正式终端会话。</span>
              </div>
              <div className="debug-chat-actions">
                {dirty && <Badge tone="amber">有未保存修改</Badge>}
                <Button size="sm" variant="secondary" icon={<Plus size={14} />} onClick={chat.newConversation}>
                  新对话
                </Button>
              </div>
            </div>
            {dirty && (
              <div className="debug-save-bar">
                <AlertTriangle size={15} />
                <span>当前调试使用已保存配置。先保存修改，调试结果才会反映最新设置。</span>
                <Button size="sm" variant="primary" loading={saving} onClick={() => save()}>
                  保存配置
                </Button>
              </div>
            )}
            {!canEdit ? (
              <EmptyState
                icon={<Bot size={22} />}
                title="没有编辑权限"
                description="草稿调试仅对应用编辑者开放。"
              />
            ) : (
              <>
                <div className="debug-scroll" ref={chatScrollRef}>
                  {chat.loadingMessages ? (
                    <LoadingState label="正在读取调试会话…" />
                  ) : chat.messages.length === 0 ? (
                    <AssistantEmpty
                      title={`调试「${app.name}」`}
                      description="提问后可以查看回答、引用，以及右侧的本次调用过程。调试会话可多轮继续。"
                      demo={demoModel}
                    />
                  ) : (
                    <ChatMessageList
                      messages={chat.messages}
                      stage={chat.stage}
                      assistantName={app.name}
                      demo={demoModel}
                      onCitation={setCitation}
                      onTrace={() => undefined}
                    />
                  )}
                </div>
                <div className="debug-composer">
                  {chat.error != null && (
                    <Callout tone="danger" title="本次回答失败">
                      {friendlyChatError(chat.error)}
                    </Callout>
                  )}
                  {chat.notice && <Callout tone="warn">{chat.notice}</Callout>}
                  <ChatComposer
                    onSend={chat.send}
                    sending={chat.sending}
                    onStop={chat.stop}
                    placeholder="向草稿应用提问…"
                    hint="Enter 发送 · Shift + Enter 换行 · 调试回答不计入正式终端会话"
                  />
                </div>
              </>
            )}
          </Card>

          <aside className="debug-trace-panel">
            <Card padded={false} className="trace-aside-card">
              <div className="card-toolbar">
                <div className="card-toolbar-stat">
                  <Activity size={16} />
                  <span>本次调用过程</span>
                </div>
                <div className="card-toolbar-actions">
                  {lastTraceId && (
                    <Link to={`/traces/${lastTraceId}`} className="btn btn-ghost btn-sm">
                      <ExternalLink size={13} />
                      完整链路
                    </Link>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={traceState.reload}
                    disabled={!lastTraceId}
                  >
                    刷新
                  </Button>
                </div>
              </div>
              {!lastTraceId ? (
                <EmptyState
                  compact
                  icon={<Activity size={20} />}
                  title="等待一次提问"
                  description="回答完成后，这里会显示改写、召回、融合、重排与模型调用的输入输出和耗时。"
                />
              ) : traceState.loading && !traceState.data ? (
                <LoadingState label="正在读取链路…" />
              ) : traceState.error ? (
                <div className="card-pad">
                  <ErrorState error={traceState.error} onRetry={traceState.reload} compact />
                </div>
              ) : traceState.data ? (
                <>
                  <div className="trace-aside-meta">
                    <span>Trace <code>{traceState.data.id.slice(0, 12)}</code></span>
                    <span>{formatDuration(traceState.data.durationMs)}</span>
                    <span>{formatNumber(traceState.data.tokenCount ?? 0)} tokens</span>
                  </div>
                  <div className="trace-mini-list">
                    {traceState.data.spans?.length ? (
                      traceState.data.spans.map((span) => (
                        <details key={span.id} className="trace-mini-span">
                          <summary>
                            <span className="trace-mini-name">{span.name}</span>
                            <span className="trace-mini-kind">{span.kind}</span>
                            <span className="trace-mini-duration">{formatDuration(span.durationMs)}</span>
                            <ChevronDown size={14} />
                          </summary>
                          <div className="trace-mini-body">
                            <div className="trace-mini-label">输入</div>
                            <JsonBlock value={span.input} maxHeight={180} />
                            <div className="trace-mini-label">输出</div>
                            <JsonBlock value={span.output} maxHeight={180} />
                          </div>
                        </details>
                      ))
                    ) : (
                      <EmptyState compact title="该 trace 暂无 span" description="刷新或打开完整链路查看。" />
                    )}
                  </div>
                </>
              ) : null}
            </Card>
          </aside>
        </div>
      )}

      {tab === 'publish' && (
        <div className="publish-layout">
          <div className="publish-main">
            <Card>
              <SectionHeader
                title="发布不可变版本"
                description="发布将当前已保存配置固化为版本快照。编辑草稿不会影响线上，直到再次发布。"
              />
              <div className="publish-status">
                <div className="publish-status-icon">
                  <Rocket size={22} />
                </div>
                <div>
                  <strong>
                    {app.publishedVersion ? `当前线上版本 v${app.publishedVersion}` : '尚未发布'}
                  </strong>
                  <p>
                    {app.status === 'published'
                      ? '终端用户正在使用最近一次发布的版本。'
                      : '配置完成并完成草稿调试后，再发布给终端用户。'}
                  </p>
                </div>
                <StatusBadge status={app.status} />
              </div>
              {dirty && (
                <Callout tone="warn" title="有未保存修改">
                  发布使用已保存的配置。请先保存，再发布新版本。
                  <Button size="sm" variant="primary" loading={saving} onClick={() => save()} className="inline-block-button">
                    保存配置
                  </Button>
                </Callout>
              )}
              {publishError != null && (
                <Callout tone="danger" title="发布失败">
                  {errorMessage(publishError)}
                </Callout>
              )}
              <div className="publish-actions">
                <Button
                  variant="primary"
                  size="lg"
                  icon={<Rocket size={16} />}
                  loading={publishing}
                  disabled={!canPublish || dirty}
                  onClick={publish}
                >
                  发布新版本
                </Button>
                {app.publishedVersion && (
                  <Link to={`/chat/${app.id}`} target="_blank" rel="noreferrer" className="btn btn-secondary btn-lg">
                    <ExternalLink size={16} />
                    <span>体验已发布版本</span>
                  </Link>
                )}
              </div>
              {!canPublish && <p className="field-hint">发布需要「apps:publish」权限。</p>}
              {app.status === 'published' && publishedState.error != null && (
                <Callout tone="info" title="发布应用接口校验">
                  无法读取已发布应用信息（{errorMessage(publishedState.error)}）。请确认应用可见范围与当前登录状态。
                </Callout>
              )}
              {publishedState.data && (
                <div className="publish-verify">
                  <Badge tone={publishedState.data.demo ? 'amber' : 'green'} dot>
                    {publishedState.data.demo ? '已发布 · 本地演示' : '已发布 · 可访问'}
                  </Badge>
                  <span>
                    {publishedState.data.name} · v{publishedState.data.version}
                  </span>
                </div>
              )}
            </Card>

            <Card>
              <SectionHeader title="终端入口" description="Web 对话页适配手机浏览器；引用只展示片段，不提供原文件下载。" />
              <Field label="对话页地址" hint="可将此地址发给终端用户，或嵌入到内部系统。">
                <div className="copy-row">
                  <Input value={absoluteUrl} readOnly onFocus={(event) => event.target.select()} />
                  <CopyButton value={absoluteUrl} />
                  <Link to={publishUrl} target="_blank" rel="noreferrer" className="btn btn-secondary btn-sm">
                    <ExternalLink size={14} />
                    <span>打开</span>
                  </Link>
                </div>
              </Field>
              <Field label="嵌入代码" hint={publishResult?.embedCode ? '由发布接口返回。' : '当前页面根据对话地址生成通用 iframe 代码；重新发布可获取服务端返回的嵌入代码。'}>
                <div className="embed-code">
                  <pre>{embedCode}</pre>
                  <CopyButton value={embedCode} label="复制嵌入代码" />
                </div>
              </Field>
            </Card>
          </div>

          <aside className="publish-aside">
            <Card>
              <SectionHeader title="发布检查" />
              <ul className="checklist">
                <ChecklistItem done={form.kbIds.length > 0} text={`已选择 ${form.kbIds.length} 个知识库`} />
                <ChecklistItem done={Boolean(form.chatModelId)} text="已选择对话模型" />
                <ChecklistItem done={Boolean(form.systemPrompt.trim())} text="已填写系统提示词" />
                <ChecklistItem done={app.status === 'published'} text="已发布可访问版本" />
              </ul>
              <div className="aside-actions">
                <Button variant="secondary" block onClick={() => setTab('config')} icon={<Layers size={15} />}>
                  返回配置
                </Button>
                <Button variant="secondary" block onClick={() => setTab('debug')} icon={<Bot size={15} />}>
                  继续调试
                </Button>
              </div>
            </Card>
            <Card>
              <SectionHeader title="API 与嵌入" />
              <p className="muted-text">
                开放 API 使用绑定应用的 API Key 鉴权，明文只在创建时显示一次。普通资源接口不接受应用 Key。
              </p>
              <Link to="/keys" className="btn btn-secondary btn-block btn-md">
                <Link2 size={15} />
                <span>管理 API Key</span>
              </Link>
              <div className="publish-note">
                <Code2 size={14} />
                嵌入页面会继承应用可见范围与发布版本。
              </div>
            </Card>
          </aside>
        </div>
      )}

      <CitationDialog citation={citation} appId={id} onClose={() => setCitation(null)} />
    </div>
  );
}

function KbPickerRow({
  kb,
  checked,
  disabled,
  onChange,
}: {
  kb: KnowledgeBase;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`kb-check${checked ? ' is-checked' : ''}${disabled ? ' is-disabled' : ''}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="kb-check-body">
        <span className="kb-check-title">
          {kb.name}
          <VisibilityBadge visibility={kb.visibility} />
        </span>
        <span className="kb-check-meta">
          <FileText size={12} /> {formatNumber(kb.documentCount)} 文档 · <Layers size={12} />{' '}
          {formatNumber(kb.chunkCount)} 切片
        </span>
      </span>
    </label>
  );
}

function ChecklistItem({ done, text }: { done: boolean; text: string }) {
  return (
    <li className={done ? 'is-done' : ''}>
      <span className="check-dot">{done ? '✓' : ''}</span>
      {text}
    </li>
  );
}
