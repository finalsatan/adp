import { useState, type FormEvent } from 'react';
import { AlertTriangle, Save, Trash2 } from 'lucide-react';
import type { KnowledgeBase, Model } from '../../api/types';
import { knowledgeApi } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import {
  Badge,
  Button,
  Callout,
  Card,
  DescriptionList,
  Field,
  Input,
  SectionHeader,
  Select,
  Textarea,
  VisibilityBadge,
  useConfirm,
} from '../../components/ui';
import { formatDateTime, formatNumber, errorMessage } from '../../utils/format';
import { STRATEGY_LABELS, RETRIEVAL_MODE_LABELS } from '../../utils/permissions';

export function KnowledgeSettingsTab({
  kb,
  models,
  canWrite,
  canDelete,
  onSaved,
  onDeleted,
}: {
  kb: KnowledgeBase;
  models: Model[];
  canWrite: boolean;
  canDelete: boolean;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [name, setName] = useState(kb.name);
  const [description, setDescription] = useState(kb.description ?? '');
  const [visibility, setVisibility] = useState<KnowledgeBase['visibility']>(kb.visibility);
  const [embeddingModelId, setEmbeddingModelId] = useState(kb.embeddingModelId ?? '');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const embeddingModels = models.filter((model) => model.kind === 'embedding');
  const hasDocuments = (kb.documentCount ?? 0) > 0;
  const embeddingChanged = !hasDocuments && (kb.embeddingModelId ?? '') !== embeddingModelId;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      setError(new Error('知识库名称不能为空。'));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload: Partial<KnowledgeBase> = {
        name: name.trim(),
        description: description.trim(),
        visibility,
      };
      if (!hasDocuments) payload.embeddingModelId = embeddingModelId || null;
      await knowledgeApi.update(kb.id, payload);
      toast.success(
        embeddingChanged
          ? '设置已保存。Embedding 模型已变更，请重新处理文档以生成新向量。'
          : '知识库设置已保存',
      );
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (
      !(await confirm({
        title: `删除知识库「${kb.name}」？`,
        description: '知识库、文档、切片、授权与向量索引都会删除，引用该知识库的应用可能无法回答。此操作不可撤销。',
        confirmLabel: '确认删除',
        danger: true,
      }))
    ) {
      return;
    }
    setDeleting(true);
    try {
      await knowledgeApi.remove(kb.id);
      toast.success('知识库已删除');
      onDeleted();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="tab-stack">
      <Card>
        <SectionHeader title="基础信息" description="名称、描述和可见范围会影响协作者与终端用户的访问。" />
        {canWrite ? (
          <form className="form-stack" onSubmit={save}>
            <div className="form-grid two">
              <Field label="知识库名称" htmlFor="settings-name" required>
                <Input
                  id="settings-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={80}
                />
              </Field>
              <Field label="可见范围" htmlFor="settings-visibility" hint="下级组织不会自动继承本组织的资源。">
                <Select
                  id="settings-visibility"
                  value={visibility}
                  onChange={(event) => setVisibility(event.target.value as KnowledgeBase['visibility'])}
                >
                  <option value="private">仅自己</option>
                  <option value="organization">本组织</option>
                  <option value="platform">全平台</option>
                </Select>
              </Field>
            </div>
            <Field label="描述" htmlFor="settings-description">
              <Textarea
                id="settings-description"
                rows={3}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="说明文档范围、维护人和更新方式"
              />
            </Field>
            <Field
              label="Embedding 模型"
              htmlFor="settings-embedding"
              hint={
                hasDocuments
                  ? '有文档后模型固定；需新建知识库使用其他模型。'
                  : '知识库还没有文档，可先确定 Embedding 模型；创建文档后不可再更换。'
              }
            >
              <Select
                id="settings-embedding"
                value={embeddingModelId}
                onChange={(event) => setEmbeddingModelId(event.target.value)}
                disabled={hasDocuments}
              >
                <option value="">未指定</option>
                {embeddingModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name} · {model.model}
                    {model.provider === 'demo' ? '（本地演示）' : ''}
                  </option>
                ))}
              </Select>
            </Field>
            {hasDocuments && (
              <Callout tone="info" title="Embedding 模型已固定">
                知识库已有 {formatNumber(kb.documentCount)} 个文档。为保证向量空间一致，首版禁止更换 Embedding 模型；如需使用其他模型，请新建知识库并重新入库文档。
              </Callout>
            )}
            {embeddingChanged && (
              <Callout tone="warn" title="Embedding 模型将变更">
                当前知识库还没有文档，可以保存新模型；入库后将固定。
              </Callout>
            )}
            {error != null && (
              <Callout tone="danger" title="保存失败">
                {errorMessage(error)}
              </Callout>
            )}
            <div className="form-actions">
              <Button type="submit" variant="primary" icon={<Save size={15} />} loading={saving}>
                保存设置
              </Button>
            </div>
          </form>
        ) : (
          <DescriptionList
            items={[
              { label: '名称', value: name },
              { label: '描述', value: description || '—' },
              { label: '可见范围', value: <VisibilityBadge visibility={visibility} /> },
              {
                label: 'Embedding 模型',
                value: embeddingModels.find((model) => model.id === embeddingModelId)?.name ?? embeddingModelId ?? '未指定',
              },
            ]}
          />
        )}
      </Card>

      <Card>
        <SectionHeader title="知识库信息" description="服务端标识与配置摘要，用于排查与审计。" />
        <DescriptionList
          columns={2}
          items={[
            { label: '知识库 ID', value: <code className="code-text">{kb.id}</code> },
            { label: '所属组织 ID', value: <code className="code-text">{kb.orgId}</code> },
            { label: '创建人 ID', value: <code className="code-text">{kb.ownerId}</code> },
            { label: '创建时间', value: formatDateTime(kb.createdAt) },
            {
              label: '分块配置',
              value: (
                <span>
                  {STRATEGY_LABELS[kb.chunkConfig?.strategy ?? 'structure'] ?? kb.chunkConfig?.strategy} ·{' '}
                  {kb.chunkConfig?.maxChars ?? '—'} 字符 / 重叠 {kb.chunkConfig?.overlap ?? '—'}
                </span>
              ),
            },
            {
              label: '检索配置',
              value: (
                <span>
                  {RETRIEVAL_MODE_LABELS[kb.retrievalConfig?.mode ?? 'hybrid']} · topK{' '}
                  {kb.retrievalConfig?.topK ?? '—'} · 阈值 {kb.retrievalConfig?.threshold ?? '—'}
                </span>
              ),
            },
            { label: '文档数', value: `${formatNumber(kb.documentCount)} 个` },
            { label: '切片数', value: `${formatNumber(kb.chunkCount)} 个` },
          ]}
        />
      </Card>

      <Card className="danger-card">
        <SectionHeader
          title="危险操作"
          description="删除后不可恢复。若只是暂时不想被检索，可禁用相关分块而不是删除知识库。"
        />
        <div className="danger-row">
          <div>
            <strong>删除知识库</strong>
            <p>同时删除文档、切片、授权与处理任务。</p>
          </div>
          <Button
            variant="danger"
            icon={<Trash2 size={15} />}
            onClick={remove}
            loading={deleting}
            disabled={!canDelete}
          >
            删除知识库
          </Button>
        </div>
        {!canDelete && (
          <div className="danger-note">
            <AlertTriangle size={14} />
            当前角色缺少删除权限。资源删除由服务端权限校验，不依赖前端隐藏按钮。
          </div>
        )}
        <div className="danger-badges">
          <Badge tone="neutral">服务端鉴权</Badge>
          <Badge tone="neutral">操作写入审计</Badge>
        </div>
      </Card>
    </div>
  );
}
