import { useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  Bot,
  Check,
  Copy,
  KeyRound,
  Plus,
  ShieldOff,
  Trash2,
} from 'lucide-react';
import type { ApiKey, ApiKeyCreated } from '../../api/types';
import { apiKeyApi, appApi } from '../../api/client';
import { useApi, useDocumentTitle, useCopy } from '../../hooks';
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
  useConfirm,
} from '../../components/ui';
import { errorMessage, formatDateTime, formatNumber } from '../../utils/format';

export function ApiKeysPage() {
  useDocumentTitle('API Key');
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const { copied, copy } = useCopy();
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [appId, setAppId] = useState('');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const keysState = useApi(() => apiKeyApi.list(), []);
  const appsState = useApi(() => appApi.list().catch(() => []), []);
  const keys = keysState.data ?? [];
  const apps = appsState.data ?? [];
  const publishedApps = apps.filter(
    (app) => app.status === 'published' && (app.access ? app.access.edit : true),
  );

  const stats = useMemo(
    () => ({
      active: keys.filter((key) => !key.revokedAt).length,
      revoked: keys.filter((key) => key.revokedAt).length,
      apps: new Set(keys.filter((key) => !key.revokedAt).map((key) => key.appId)).size,
    }),
    [keys],
  );

  const appName = (id: string) => apps.find((app) => app.id === id)?.name ?? id;

  if (!can('keys:manage')) {
    return (
      <div className="page">
        <PageHeader title="API Key" description="为已发布应用创建开放 API 密钥。" />
        <Card>
          <PermissionDenied permission="keys:manage" />
        </Card>
      </div>
    );
  }

  const openCreate = () => {
    setName('');
    setAppId(publishedApps[0]?.id ?? '');
    setFormError(null);
    setCreateOpen(true);
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!name.trim()) {
      setFormError(new Error('请填写密钥名称。'));
      return;
    }
    if (!appId) {
      setFormError(new Error('请选择要绑定的已发布应用。'));
      return;
    }
    setCreating(true);
    setFormError(null);
    try {
      const result = await apiKeyApi.create({ name: name.trim(), appId });
      setCreated(result);
      setCreateOpen(false);
      setName('');
      keysState.reload();
    } catch (error) {
      setFormError(error);
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (key: ApiKey) => {
    if (
      !(await confirm({
        title: `吊销「${key.name}」？`,
        description: '吊销后使用该密钥的开放 API 请求会立即失败，且无法恢复。绑定应用的现有密钥不会自动轮换。',
        confirmLabel: '吊销密钥',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await apiKeyApi.revoke(key.id);
      toast.success('密钥已吊销');
      keysState.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="平台管理"
        title="API Key"
        description="为已发布应用创建开放 API 密钥。明文只在创建时返回一次，之后只显示前缀。"
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate} disabled={appsState.loading}>
            创建 API Key
          </Button>
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><KeyRound size={17} /></div>
          <div className="stat-copy"><span>有效密钥</span><strong>{formatNumber(stats.active)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Bot size={17} /></div>
          <div className="stat-copy"><span>绑定应用</span><strong>{formatNumber(stats.apps)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><ShieldOff size={17} /></div>
          <div className="stat-copy"><span>已吊销</span><strong>{formatNumber(stats.revoked)}</strong></div>
        </div>
      </div>

      {!appsState.loading && publishedApps.length === 0 && (
        <Callout
          tone="warn"
          title="没有可用于绑定的已发布应用"
          action={
            can('apps:read') ? (
              <Link to="/apps" className="btn btn-secondary btn-sm">
                去知识问答
              </Link>
            ) : undefined
          }
        >
          开放 API Key 只能绑定已发布应用。请先完成应用配置与发布，再创建密钥。
        </Callout>
      )}

      <Card padded={false}>
        <div className="card-toolbar">
          <div className="card-toolbar-stat">
            <KeyRound size={16} />
            <span>密钥列表</span>
            <span className="muted-text">普通资源接口不接受应用 Key</span>
          </div>
          <div className="card-toolbar-actions">
            <Button size="sm" variant="secondary" onClick={keysState.reload} loading={keysState.loading}>
              刷新
            </Button>
          </div>
        </div>

        {keysState.loading && !keysState.data ? (
          <LoadingState label="正在读取 API Key…" />
        ) : keysState.error ? (
          <div className="card-pad">
            <ErrorState error={keysState.error} onRetry={keysState.reload} compact />
          </div>
        ) : keys.length === 0 ? (
          <EmptyState
            icon={<KeyRound size={23} />}
            title="还没有 API Key"
            description="创建后会立即显示一次完整密钥。请只保存到服务端密钥管理或受控配置中，不要写入前端代码。"
            action={
              <Button
                variant="primary"
                icon={<Plus size={15} />}
                onClick={openCreate}
                disabled={publishedApps.length === 0}
              >
                创建第一个 API Key
              </Button>
            }
          />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>密钥前缀</th>
                  <th>绑定应用</th>
                  <th>创建时间</th>
                  <th>状态</th>
                  <th className="align-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {keys.map((key) => (
                  <tr key={key.id} className={key.revokedAt ? 'row-muted' : ''}>
                    <td>
                      <strong>{key.name}</strong>
                    </td>
                    <td>
                      <code className="key-prefix">{key.prefix}…</code>
                    </td>
                    <td>
                      <span className="cell-provider">{appName(key.appId)}</span>
                    </td>
                    <td className="muted-cell">{formatDateTime(key.createdAt)}</td>
                    <td>
                      {key.revokedAt ? (
                        <Badge tone="red" dot>已吊销 · {formatDateTime(key.revokedAt)}</Badge>
                      ) : (
                        <Badge tone="green" dot>有效</Badge>
                      )}
                    </td>
                    <td className="align-right">
                      {!key.revokedAt ? (
                        <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => revoke(key)}>
                          吊销
                        </Button>
                      ) : (
                        <span className="muted-text">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="card-foot-note">
          开放 API 使用 <code>Authorization: Bearer adp_…</code>。密钥仅能访问绑定的已发布应用，吊销立即生效。
        </div>
      </Card>

      <Card>
        <div className="api-usage-grid">
          <div>
            <h3>调用方式</h3>
            <p>服务端调用开放 API 时，在请求头中携带密钥。不要在浏览器页面或移动端客户端中暴露密钥。</p>
            <pre className="code-snippet">{`Authorization: Bearer adp_...`}</pre>
          </div>
          <div>
            <h3>权限边界</h3>
            <ul className="plain-list">
              <li>密钥绑定单个已发布应用，不能访问知识库管理接口。</li>
              <li>吊销后无法恢复；需要重新创建并更新调用方配置。</li>
              <li>创建时完整密钥只显示一次，服务端只保留可识别前缀。</li>
            </ul>
          </div>
        </div>
      </Card>

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="创建 API Key"
        description="密钥绑定一个已发布应用，创建后立即显示一次完整值。"
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={creating} onClick={() => submit()} disabled={publishedApps.length === 0}>
              创建密钥
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
          <Field label="密钥名称" htmlFor="key-name" required hint="例如：官网问答服务端。">
            <Input
              id="key-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：HR 问答生产环境"
              autoFocus
              maxLength={80}
            />
          </Field>
          <Field label="绑定应用" htmlFor="key-app" required hint="仅展示已发布应用。">
            <Select
              id="key-app"
              value={appId}
              onChange={(event) => setAppId(event.target.value)}
              disabled={publishedApps.length === 0}
            >
              {publishedApps.length === 0 && <option value="">暂无已发布应用</option>}
              {publishedApps.map((app) => (
                <option key={app.id} value={app.id}>
                  {app.name} · v{app.publishedVersion ?? '—'}
                </option>
              ))}
            </Select>
          </Field>
          {formError != null && (
            <Callout tone="danger" title="创建失败">
              {errorMessage(formError)}
            </Callout>
          )}
        </form>
      </Modal>

      <Modal
        open={created != null}
        onClose={() => setCreated(null)}
        title="API Key 已创建"
        description="完整密钥只显示这一次。关闭后无法再次查看，只能吊销并重建。"
        size="lg"
        footer={
          <Button variant="primary" onClick={() => setCreated(null)}>
            我已保存，关闭
          </Button>
        }
      >
        {created && (
          <div className="secret-panel">
            <Callout tone="warn" title="请立即复制并保存到受控位置">
              不要将密钥提交到代码仓库、工单、聊天记录或浏览器前端。服务端只保留前缀，无法找回完整值。
            </Callout>
            <div className="secret-key-row">
              <code className="secret-key">{created.key}</code>
              <Button
                variant="secondary"
                icon={copied ? <Check size={15} /> : <Copy size={15} />}
                onClick={() => void copy(created.key)}
              >
                {copied ? '已复制' : '复制密钥'}
              </Button>
            </div>
            <div className="secret-meta">
              <span>前缀 <code>{created.prefix}</code></span>
              <span>ID <code>{created.id}</code></span>
            </div>
            <div className="secret-warning">
              <AlertTriangle size={15} />
              使用该密钥的调用方需要更新配置后重新验证连通性。
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
