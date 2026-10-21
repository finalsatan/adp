import { useMemo, useState, type FormEvent } from 'react';
import {
  Box,
  CheckCircle2,
  Cpu,
  FlaskConical,
  KeyRound,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  Server,
  Trash2,
  XCircle,
  Zap,
} from 'lucide-react';
import type { Model, ModelInput, ModelKind, ModelProvider } from '../../api/types';
import { modelApi } from '../../api/client';
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
  Select,
  Switch,
  useConfirm,
} from '../../components/ui';
import { formatDateTime, formatDuration, formatNumber, parseNumber } from '../../utils/format';

const KIND_LABELS: Record<ModelKind, string> = {
  chat: '对话模型',
  embedding: 'Embedding',
  rerank: 'Rerank',
  vision: '视觉 / OCR',
};

const KIND_TONE: Record<ModelKind, 'indigo' | 'green' | 'amber' | 'purple'> = {
  chat: 'indigo',
  embedding: 'green',
  rerank: 'amber',
  vision: 'purple',
};

interface ModelFormState {
  name: string;
  kind: ModelKind;
  provider: ModelProvider;
  baseUrl: string;
  model: string;
  scope: 'organization' | 'platform';
  enabled: boolean;
  apiKey: string;
  dimensions: string;
}

const EMPTY_FORM: ModelFormState = {
  name: '',
  kind: 'chat',
  provider: 'openai',
  baseUrl: 'https://api.openai.com/v1',
  model: '',
  scope: 'organization',
  enabled: true,
  apiKey: '',
  dimensions: '',
};

interface TestResult {
  ok: boolean;
  message: string;
  latencyMs: number;
}

export function ModelsPage() {
  useDocumentTitle('模型管理');
  const { can, user } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Model | null>(null);
  const [form, setForm] = useState<ModelFormState>({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const modelsState = useApi(() => modelApi.list(), []);
  const models = modelsState.data ?? [];

  const stats = useMemo(
    () => ({
      total: models.length,
      chat: models.filter((model) => model.kind === 'chat').length,
      embedding: models.filter((model) => model.kind === 'embedding').length,
      enabled: models.filter((model) => model.enabled).length,
      demo: models.filter((model) => model.provider === 'demo').length,
    }),
    [models],
  );

  if (!can('models:manage')) {
    return (
      <div className="page">
        <PageHeader title="模型管理" description="统一接入对话、Embedding、Rerank 与视觉模型。" />
        <Card>
          <PermissionDenied permission="models:manage" />
        </Card>
      </div>
    );
  }

  const openCreate = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM });
    setFormError(null);
    setModalOpen(true);
  };

  const openEdit = (model: Model) => {
    setEditing(model);
    setForm({
      name: model.name,
      kind: model.kind,
      provider: model.provider,
      baseUrl: model.baseUrl ?? '',
      model: model.model ?? '',
      scope: model.scope,
      enabled: model.enabled,
      apiKey: '',
      dimensions: model.dimensions != null ? String(model.dimensions) : '',
    });
    setFormError(null);
    setModalOpen(true);
  };

  const changeProvider = (provider: ModelProvider) => {
    setForm((current) => {
      const demoModel =
        current.kind === 'embedding' ? 'demo-embedding' : current.kind === 'rerank' ? 'demo-rerank' : current.kind === 'vision' ? 'demo-vision' : 'demo-chat';
      return {
        ...current,
        provider,
        baseUrl: provider === 'demo' ? '' : current.baseUrl || 'https://api.openai.com/v1',
        model: provider === 'demo' && !current.model ? demoModel : current.model,
      };
    });
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!form.name.trim()) {
      setFormError(new Error('请填写模型名称。'));
      return;
    }
    if (!form.model.trim()) {
      setFormError(new Error('请填写模型标识，例如 gpt-4o-mini 或本地模型名称。'));
      return;
    }
    if (form.provider === 'openai' && !form.baseUrl.trim()) {
      setFormError(new Error('OpenAI 兼容模型需要 Base URL。'));
      return;
    }
    setSaving(true);
    setFormError(null);
    const payload: ModelInput = {
      name: form.name.trim(),
      kind: form.kind,
      provider: form.provider,
      baseUrl: form.baseUrl.trim(),
      model: form.model.trim(),
      scope: form.scope,
      enabled: form.enabled,
      dimensions: form.kind === 'embedding' ? parseNumber(form.dimensions, 0) || null : null,
    };
    if (form.apiKey.trim()) payload.apiKey = form.apiKey.trim();
    try {
      if (editing) {
        await modelApi.update(editing.id, payload);
        toast.success('模型已更新');
      } else {
        await modelApi.create(payload);
        toast.success('模型已添加');
      }
      setModalOpen(false);
      modelsState.reload();
    } catch (error) {
      setFormError(error);
    } finally {
      setSaving(false);
    }
  };

  const test = async (model: Model) => {
    setTestingId(model.id);
    try {
      const result = await modelApi.test(model.id);
      setTestResults((current) => ({ ...current, [model.id]: result }));
      if (result.ok) toast.success(`${model.name} 连接正常，耗时 ${formatDuration(result.latencyMs)}`);
      else toast.error(`${model.name} 测试失败：${result.message}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : '测试失败';
      setTestResults((current) => ({ ...current, [model.id]: { ok: false, message, latencyMs: 0 } }));
      toast.error(message);
    } finally {
      setTestingId(null);
    }
  };

  const remove = async (model: Model) => {
    if (
      !(await confirm({
        title: `删除模型「${model.name}」？`,
        description: '引用该模型的知识库、应用或评测可能无法继续运行。服务端会校验占用情况，密钥会随记录删除。',
        confirmLabel: '删除模型',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await modelApi.remove(model.id);
      toast.success('模型已删除');
      modelsState.reload();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '删除失败');
    }
  };

  const statCards = [
    { label: '全部模型', value: stats.total, icon: <Cpu size={17} /> },
    { label: '对话模型', value: stats.chat, icon: <Box size={17} /> },
    { label: 'Embedding', value: stats.embedding, icon: <Server size={17} /> },
    { label: '已启用', value: stats.enabled, icon: <Power size={17} /> },
    { label: '本地演示', value: stats.demo, icon: <FlaskConical size={17} /> },
  ];

  return (
    <div className="page">
      <PageHeader
        eyebrow="平台管理"
        title="模型管理"
        description="统一接入 OpenAI 兼容的对话、Embedding、Rerank 与视觉模型。密钥只写入，不在读取响应中返回明文。"
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
            添加模型
          </Button>
        }
      />

      <div className="stat-grid stat-grid-compact">
        {statCards.map((item) => (
          <div className="stat-card" key={item.label}>
            <div className="stat-icon">{item.icon}</div>
            <div className="stat-copy">
              <span>{item.label}</span>
              <strong>{formatNumber(item.value)}</strong>
            </div>
          </div>
        ))}
      </div>

      {stats.demo > 0 && (
        <Callout tone="warn" title="本地演示模型不计入效果验证">
          标有「本地演示」的模型不访问外网，回答为原文摘录，Embedding 也不是真实语义向量。它们只用于跑通流程，不能用于对外宣称模型能力或业务准确率。
        </Callout>
      )}

      <Card padded={false}>
        <div className="card-toolbar">
          <div className="card-toolbar-stat">
            <Cpu size={16} />
            <span>模型清单</span>
            <span className="muted-text">全平台共享模型与当前组织模型</span>
          </div>
          <div className="card-toolbar-actions">
            <Button
              size="sm"
              variant="secondary"
              icon={<RefreshCw size={14} />}
              onClick={modelsState.reload}
              loading={modelsState.loading}
            >
              刷新
            </Button>
          </div>
        </div>

        {modelsState.loading && !modelsState.data ? (
          <LoadingState label="正在读取模型…" />
        ) : modelsState.error ? (
          <div className="card-pad">
            <ErrorState error={modelsState.error} onRetry={modelsState.reload} compact />
          </div>
        ) : models.length === 0 ? (
          <EmptyState
            icon={<Cpu size={23} />}
            title="还没有登记模型"
            description="添加一个 OpenAI 兼容模型，或在初始数据中保留的本地演示模型上先验证流程。真实业务必须使用经过测试的模型。"
            action={
              <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
                添加第一个模型
              </Button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table model-table">
              <thead>
                <tr>
                  <th>模型</th>
                  <th>类型</th>
                  <th>Provider</th>
                  <th>范围</th>
                  <th>密钥</th>
                  <th>状态</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => {
                  const result = testResults[model.id];
                  return (
                    <tr key={model.id}>
                      <td>
                        <div className="model-name-cell">
                          <span className="model-icon">
                            <Cpu size={16} />
                          </span>
                          <div>
                            <strong>{model.name}</strong>
                            <span className="mono-text">{model.model}</span>
                            {model.baseUrl && <span className="model-base">{model.baseUrl}</span>}
                            {result && (
                              <span className={`model-test-result${result.ok ? ' is-ok' : ' is-fail'}`}>
                                {result.ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                                {result.ok ? `连通 · ${formatDuration(result.latencyMs)}` : result.message}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge tone={KIND_TONE[model.kind]}>{KIND_LABELS[model.kind] ?? model.kind}</Badge>
                        {model.kind === 'embedding' && model.dimensions ? (
                          <span className="cell-sub">{model.dimensions} 维</span>
                        ) : null}
                      </td>
                      <td>
                        <span className="cell-provider">
                          {model.provider === 'demo' ? '本地演示' : 'OpenAI 兼容'}
                        </span>
                      </td>
                      <td>
                        <Badge tone={model.scope === 'platform' ? 'purple' : 'neutral'}>
                          {model.scope === 'platform' ? '全平台' : '本组织'}
                        </Badge>
                      </td>
                      <td>
                        <span className="key-mask">
                          <KeyRound size={12} />
                          {model.apiKeyMasked || (model.provider === 'demo' ? '无需密钥' : '未配置')}
                        </span>
                      </td>
                      <td>
                        <Badge tone={model.enabled ? 'green' : 'neutral'} dot>
                          {model.enabled ? '启用' : '停用'}
                        </Badge>
                        <span className="cell-sub">{formatDateTime(model.createdAt)}</span>
                      </td>
                      <td>
                        <div className="row-actions row-actions-end">
                          <button
                            type="button"
                            className="row-action"
                            onClick={() => test(model)}
                            disabled={testingId === model.id}
                          >
                            <Zap size={13} />
                            {testingId === model.id ? '测试中…' : '测试'}
                          </button>
                          <button type="button" className="row-action" onClick={() => openEdit(model)}>
                            <Pencil size={13} /> 编辑
                          </button>
                          <button
                            type="button"
                            className="row-action row-action-danger"
                            onClick={() => remove(model)}
                            disabled={model.scope === 'platform' && !user?.isSystemAdmin}
                          >
                            <Trash2 size={13} /> 删除
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="card-foot-note">
          API Key 使用服务端加密存储；列表只返回掩码。测试会发起一次真实请求，失败原因不会被静默替换成演示模型。
        </div>
      </Card>

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? `编辑模型 · ${editing.name}` : '添加模型'}
        description="真实模型需要 OpenAI 兼容的 Base URL 与模型标识。编辑时密钥留空表示保留原密钥。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setModalOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={saving} onClick={() => submit()}>
              {editing ? '保存修改' : '添加模型'}
            </Button>
          </>
        }
      >
        <form className="modal-form" onSubmit={submit}>
          <div className="form-grid two">
            <Field label="显示名称" htmlFor="model-name" required hint="用于知识库和应用中选择。">
              <Input
                id="model-name"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="例如：GPT-4o mini"
                autoFocus
              />
            </Field>
            <Field label="模型类型" htmlFor="model-kind" required>
              <Select
                id="model-kind"
                value={form.kind}
                onChange={(event) => setForm({ ...form, kind: event.target.value as ModelKind })}
              >
                <option value="chat">对话模型 chat</option>
                <option value="embedding">Embedding</option>
                <option value="rerank">Rerank</option>
                <option value="vision">视觉 / OCR</option>
              </Select>
            </Field>
          </div>
          <div className="form-grid two">
            <Field label="Provider" htmlFor="model-provider" required>
              <Select
                id="model-provider"
                value={form.provider}
                onChange={(event) => changeProvider(event.target.value as ModelProvider)}
              >
                <option value="openai">OpenAI 兼容</option>
                <option value="demo">本地演示（不访问外网）</option>
              </Select>
            </Field>
            <Field label="作用范围" htmlFor="model-scope" hint="全平台模型仅系统管理员可维护。">
              <Select
                id="model-scope"
                value={form.scope}
                onChange={(event) =>
                  setForm({ ...form, scope: event.target.value as ModelFormState['scope'] })
                }
              >
                <option value="organization">本组织</option>
                {user?.isSystemAdmin && <option value="platform">全平台共享</option>}
              </Select>
            </Field>
          </div>
          {form.provider === 'openai' ? (
            <div className="form-grid two">
              <Field label="Base URL" htmlFor="model-baseurl" required hint="需包含 /v1 或兼容网关的路径前缀。">
                <Input
                  id="model-baseurl"
                  value={form.baseUrl}
                  onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
                  placeholder="https://api.openai.com/v1"
                />
              </Field>
              <Field label="模型标识" htmlFor="model-id" required hint="请求体中使用的 model 字段。">
                <Input
                  id="model-id"
                  value={form.model}
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                  placeholder="gpt-4o-mini"
                />
              </Field>
            </div>
          ) : (
            <Field label="模型标识" htmlFor="model-id-demo" hint="本地演示模型不调用外网，回答为摘录。">
              <Input
                id="model-id-demo"
                value={form.model}
                onChange={(event) => setForm({ ...form, model: event.target.value })}
                placeholder="demo-chat"
              />
            </Field>
          )}
          <Field
            label="API Key"
            htmlFor="model-apikey"
            hint={
              editing
                ? '留空表示保留原密钥；填写会覆盖。密钥不会在读取接口中返回。'
                : '密钥会加密存储。演示模型可留空。'
            }
          >
            <Input
              id="model-apikey"
              type="password"
              value={form.apiKey}
              onChange={(event) => setForm({ ...form, apiKey: event.target.value })}
              placeholder={editing ? '留空保留原密钥' : 'sk-…'}
              autoComplete="new-password"
            />
          </Field>
          {form.kind === 'embedding' && (
            <Field label="向量维度" htmlFor="model-dimensions" hint="与实际模型一致；0 表示由服务端自适应。">
              <Input
                id="model-dimensions"
                type="number"
                min={0}
                value={form.dimensions}
                onChange={(event) => setForm({ ...form, dimensions: event.target.value })}
                placeholder="1536"
              />
            </Field>
          )}
          <Switch
            checked={form.enabled}
            onChange={(enabled) => setForm({ ...form, enabled })}
            label="启用该模型（停用后不出现在知识库与应用的选择列表中）"
          />
          {form.provider === 'demo' && (
            <Callout tone="warn" title="本地演示模型的能力边界">
              不访问外网，Chat 仅返回检索片段摘录；Embedding 不是真实语义向量。用于跑通链路，不可用于业务验收或效果证明。
            </Callout>
          )}
          {formError != null && (
            <Callout tone="danger" title="保存失败">
              {formError instanceof Error ? formError.message : '请检查输入后重试。'}
            </Callout>
          )}
        </form>
      </Modal>
    </div>
  );
}
