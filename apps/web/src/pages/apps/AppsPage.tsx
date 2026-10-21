import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Bot,
  ExternalLink,
  FileText,
  Layers,
  Plus,
  Rocket,
  Sparkles,
} from 'lucide-react';
import type { AppItem, KnowledgeBase, RetrievalConfig } from '../../api/types';
import { appApi, knowledgeApi, modelApi } from '../../api/client';
import { useApi, useDocumentTitle } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Modal,
  PageHeader,
  PermissionDenied,
  SearchInput,
  Select,
  StatusBadge,
  Tabs,
  Textarea,
  VisibilityBadge,
} from '../../components/ui';
import { DEFAULT_RETRIEVAL_CONFIG } from '../../utils/permissions';
import { errorMessage, formatDateTime, formatNumber } from '../../utils/format';

type AppFilter = 'all' | 'published' | 'draft';

const DEFAULT_PROMPT =
  '你是企业知识库问答助手。请仅依据提供的知识片段回答；如果依据不足，请明确说明无法回答，并提示用户补充信息。引用来源时保留编号。';

export function AppsPage() {
  useDocumentTitle('知识问答应用');
  const { can } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<AppFilter>('all');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<AppItem['visibility']>('organization');
  const [chatModelId, setChatModelId] = useState('');
  const [kbIds, setKbIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);

  const appsState = useApi(() => appApi.list(), []);
  const modelsState = useApi(() => modelApi.list().catch(() => []), []);
  const kbsState = useApi(() => knowledgeApi.list().catch(() => []), []);

  const apps = appsState.data ?? [];
  const models = modelsState.data ?? [];
  const kbs = kbsState.data ?? [];
  const chatModels = models.filter((model) => model.kind === 'chat' && model.enabled);

  const counts = useMemo(
    () => ({
      all: apps.length,
      published: apps.filter((app) => app.status === 'published').length,
      draft: apps.filter((app) => app.status !== 'published').length,
    }),
    [apps],
  );

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return apps.filter((app) => {
      if (filter === 'published' && app.status !== 'published') return false;
      if (filter === 'draft' && app.status === 'published') return false;
      if (!keyword) return true;
      return app.name.toLowerCase().includes(keyword) || (app.description ?? '').toLowerCase().includes(keyword);
    });
  }, [apps, filter, search]);

  if (!can('apps:read')) {
    return (
      <div className="page">
        <PageHeader title="知识问答" description="基于知识库的可发布问答应用。" />
        <Card>
          <PermissionDenied permission="apps:read" />
        </Card>
      </div>
    );
  }

  const openCreate = () => {
    setName('');
    setDescription('');
    setVisibility('organization');
    setChatModelId(chatModels[0]?.id ?? '');
    setKbIds([]);
    setFormError(null);
    setCreateOpen(true);
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!name.trim()) {
      setFormError(new Error('请填写应用名称。'));
      return;
    }
    if (!chatModelId) {
      setFormError(new Error('请选择一个可用的对话模型。若没有，请先到模型管理接入。'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    const retrievalConfig: RetrievalConfig = { ...DEFAULT_RETRIEVAL_CONFIG };
    try {
      const created = await appApi.create({
        name: name.trim(),
        description: description.trim(),
        visibility,
        chatModelId,
        kbIds,
        systemPrompt: DEFAULT_PROMPT,
        retrievalConfig,
      });
      toast.success('应用已创建，可以继续配置与调试');
      setCreateOpen(false);
      navigate(`/apps/${created.id}`);
    } catch (error) {
      setFormError(error);
    } finally {
      setSubmitting(false);
    }
  };

  const modelName = (id: string) => models.find((model) => model.id === id)?.name ?? id;

  return (
    <div className="page">
      <PageHeader
        eyebrow="应用开发"
        title="知识问答"
        description="选择知识库与模型，配置检索策略，草稿调试后发布为不可变版本，并获取对话页与嵌入代码。"
        actions={
          can('apps:write') ? (
            <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
              新建知识问答
            </Button>
          ) : undefined
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><Bot size={17} /></div>
          <div className="stat-copy"><span>全部应用</span><strong>{formatNumber(counts.all)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Rocket size={17} /></div>
          <div className="stat-copy"><span>已发布</span><strong>{formatNumber(counts.published)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><FileText size={17} /></div>
          <div className="stat-copy"><span>草稿 / 未发布</span><strong>{formatNumber(counts.draft)}</strong></div>
        </div>
      </div>

      {!modelsState.loading && modelsState.data && chatModels.length === 0 && (
        <Callout
          tone="warn"
          title="没有可用的对话模型"
          action={
            can('models:manage') ? (
              <Link to="/models" className="btn btn-secondary btn-sm">
                去模型管理
              </Link>
            ) : undefined
          }
        >
          知识问答需要至少一个启用的 chat 模型。若只有本地演示模型，也可以跑通流程，但回答仅摘录原文，不能代表真实模型效果。
        </Callout>
      )}

      <div className="toolbar toolbar-wrap">
        <Tabs
          tabs={[
            { value: 'all', label: '全部', count: counts.all },
            { value: 'published', label: '已发布', count: counts.published },
            { value: 'draft', label: '草稿', count: counts.draft },
          ]}
          value={filter}
          onChange={setFilter}
        />
        <SearchInput value={search} onChange={setSearch} placeholder="搜索应用名称或描述" className="grow-search" />
      </div>

      {appsState.loading && !appsState.data ? (
        <LoadingState label="正在读取应用…" />
      ) : appsState.error ? (
        <ErrorState error={appsState.error} onRetry={appsState.reload} />
      ) : filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Bot size={24} />}
            title={apps.length === 0 ? '还没有知识问答应用' : '没有匹配的应用'}
            description={
              apps.length === 0
                ? '从一个已就绪的知识库开始，配置检索与回答策略，再发布给终端用户。'
                : '尝试更换筛选条件或搜索关键词。'
            }
            action={
              apps.length === 0 && can('apps:write') ? (
                <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
                  新建知识问答
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <div className="app-grid">
          {filtered.map((app) => (
            <article key={app.id} className="app-card">
              <div className="app-card-top">
                <span className="app-icon">
                  <Bot size={19} />
                </span>
                <StatusBadge status={app.status} />
                <VisibilityBadge visibility={app.visibility} />
              </div>
              <div className="app-card-body">
                <h3>
                  <Link to={`/apps/${app.id}`}>{app.name}</Link>
                </h3>
                <p>{app.description || '暂无描述'}</p>
              </div>
              <div className="app-card-meta">
                <span><Layers size={13} /> {app.kbIds?.length ?? 0} 个知识库</span>
                <span><Sparkles size={13} /> {modelName(app.chatModelId)}</span>
                {app.publishedVersion ? <Badge tone="green">v{app.publishedVersion} 已发布</Badge> : <Badge tone="amber">草稿</Badge>}
              </div>
              <div className="app-card-foot">
                <span className="muted-text">{formatDateTime(app.createdAt)}</span>
                <div className="app-card-actions">
                  {app.status === 'published' && (
                    <Link to={`/chat/${app.id}`} className="btn btn-ghost btn-sm" target="_blank" rel="noreferrer">
                      <ExternalLink size={13} />
                      <span>打开对话</span>
                    </Link>
                  )}
                  <Link to={`/apps/${app.id}`} className="btn btn-secondary btn-sm">
                    <span>配置与调试</span>
                    <ArrowRight size={13} />
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="新建知识问答"
        description="创建后进入编辑器，继续配置系统提示词、检索参数与知识库范围。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={submitting} onClick={() => submit()} icon={<Plus size={15} />}>
              创建并进入编辑
            </Button>
          </>
        }
      >
        <form
          className="modal-form"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="form-grid two">
            <Field label="应用名称" htmlFor="app-name" required>
              <Input
                id="app-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：HR 制度问答"
                autoFocus
                maxLength={80}
              />
            </Field>
            <Field label="可见范围" htmlFor="app-visibility" hint="应用发布后仍受此范围约束。">
              <Select
                id="app-visibility"
                value={visibility}
                onChange={(event) => setVisibility(event.target.value as AppItem['visibility'])}
              >
                <option value="private">仅自己</option>
                <option value="organization">本组织</option>
                <option value="platform">全平台</option>
              </Select>
            </Field>
          </div>
          <Field label="描述" htmlFor="app-description">
            <Textarea
              id="app-description"
              rows={2}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="告诉终端用户可以问什么、不能问什么"
            />
          </Field>
          <Field
            label="对话模型"
            htmlFor="app-model"
            required
            hint={
              chatModels.length === 0
                ? '暂无可用 chat 模型，请先接入。'
                : '回答生成使用该模型；本地演示模型会明确标注。'
            }
          >
            <Select
              id="app-model"
              value={chatModelId}
              onChange={(event) => setChatModelId(event.target.value)}
            >
              {chatModels.length === 0 && <option value="">暂无可用模型</option>}
              {chatModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} · {model.model}
                  {model.provider === 'demo' ? '（本地演示）' : ''}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="知识库（可多选）" hint="应用只会检索被选中的知识库，终端用户无需知识库管理权限。">
            {kbs.length === 0 ? (
              <Callout tone="warn" title="没有可选知识库">
                先创建并完成文档入库，再回来选择知识库。
              </Callout>
            ) : (
              <div className="kb-picker">
                {kbs.map((kb) => (
                  <KbCheckbox
                    key={kb.id}
                    kb={kb}
                    checked={kbIds.includes(kb.id)}
                    onChange={(checked) =>
                      setKbIds((current) =>
                        checked ? [...current, kb.id] : current.filter((id) => id !== kb.id),
                      )
                    }
                  />
                ))}
              </div>
            )}
          </Field>
          {formError != null && (
            <Callout tone="danger" title="创建失败">
              {errorMessage(formError)}
            </Callout>
          )}
        </form>
      </Modal>
    </div>
  );
}

function KbCheckbox({
  kb,
  checked,
  onChange,
}: {
  kb: KnowledgeBase;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`kb-check${checked ? ' is-checked' : ''}`}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="kb-check-body">
        <span className="kb-check-title">
          {kb.name}
          <VisibilityBadge visibility={kb.visibility} />
        </span>
        <span className="kb-check-meta">
          {formatNumber(kb.documentCount)} 文档 · {formatNumber(kb.chunkCount)} 切片
        </span>
      </span>
    </label>
  );
}
