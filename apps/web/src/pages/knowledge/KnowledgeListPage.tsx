import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  BookOpen,
  Calendar,
  FileText,
  FolderOpen,
  Layers,
  Plus,
  Sparkles,
  Users,
} from 'lucide-react';
import { knowledgeApi, modelApi } from '../../api/client';
import type { ChunkConfig, KnowledgeBase, RetrievalConfig } from '../../api/types';
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
  SearchInput,
  Select,
  Tabs,
  Textarea,
  VisibilityBadge,
} from '../../components/ui';
import { DEFAULT_CHUNK_CONFIG, DEFAULT_RETRIEVAL_CONFIG, STRATEGY_DESCRIPTIONS } from '../../utils/permissions';
import { errorMessage, formatDate, formatNumber } from '../../utils/format';

type FilterTab = 'all' | 'mine' | 'shared';

const SAMPLE_DOCUMENT = `# 差旅与费用报销指引（示例）

> 本文件为 ADP 示例知识库内容，用于验证上传、分块、检索与引用链路，不代表公司正式制度。

## 一、适用范围

本指引适用于公司全体员工的国内差旅与费用报销。员工应在出差前完成预算与行程审批，并在返程后 10 个工作日内提交报销单。

## 二、交通标准

1. 高铁与动车：原则上选择二等座。单程超过 5 小时且确因工作需要，可申请一等座，需在审批单中注明理由。
2. 飞机：经济舱。部门负责人及以上人员因行程需要可乘坐公务舱，需提前获得分管领导批准。
3. 市内交通：优先使用公共交通。因携带设备、夜间到达或客户地点偏远，可乘坐出租车或网约车，报销时需附行程单。

## 三、住宿标准

| 城市级别 | 普通员工 | 经理级 | 总监及以上 |
| --- | --- | --- | --- |
| 一线城市 | 450 元/晚 | 600 元/晚 | 800 元/晚 |
| 二线城市 | 350 元/晚 | 500 元/晚 | 650 元/晚 |
| 其他城市 | 300 元/晚 | 400 元/晚 | 550 元/晚 |

住宿费按实际发生金额报销，超过标准的部分由个人承担。与同事合住或入住协议酒店时，以实际发票金额为准。

## 四、餐费与补贴

出差期间按自然日发放餐费补贴：一线城市 120 元/天，其他城市 100 元/天。招待客户产生的餐费需单独提交招待申请，不得与个人餐费补贴重复报销。

## 五、报销材料

- 审批通过的出差申请单
- 交通票据与行程单
- 住宿发票（需注明入住人与天数）
- 其他费用的合规发票

## 六、例外处理

因不可抗力导致的超标准支出，应在报销单中说明原因，由部门负责人与财务共同确认后处理。未按规定提交材料的报销单将被退回补充。
`;

const EMPTY_FORM = {
  name: '',
  description: '',
  visibility: 'organization' as KnowledgeBase['visibility'],
  embeddingModelId: '',
  strategy: 'structure' as ChunkConfig['strategy'],
  maxChars: DEFAULT_CHUNK_CONFIG.maxChars,
  overlap: DEFAULT_CHUNK_CONFIG.overlap,
  mode: DEFAULT_RETRIEVAL_CONFIG.mode,
  topK: DEFAULT_RETRIEVAL_CONFIG.topK,
  threshold: DEFAULT_RETRIEVAL_CONFIG.threshold,
};

export function KnowledgeListPage() {
  useDocumentTitle('知识库');
  const { user, can } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [tab, setTab] = useState<FilterTab>('all');
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);
  const [sampleLoading, setSampleLoading] = useState(false);

  const kbs = useApi(() => knowledgeApi.list(), []);
  const models = useApi(() => modelApi.list().catch(() => []), []);
  const embeddingModels = (models.data ?? []).filter((model) => model.kind === 'embedding' && model.enabled);
  const all = kbs.data ?? [];

  const counts = useMemo(
    () => ({
      all: all.length,
      mine: all.filter((kb) => kb.ownerId === user?.id).length,
      shared: all.filter((kb) => kb.ownerId !== user?.id).length,
    }),
    [all, user?.id],
  );

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return all.filter((kb) => {
      if (tab === 'mine' && kb.ownerId !== user?.id) return false;
      if (tab === 'shared' && kb.ownerId === user?.id) return false;
      if (!keyword) return true;
      return (
        kb.name.toLowerCase().includes(keyword) ||
        (kb.description ?? '').toLowerCase().includes(keyword)
      );
    });
  }, [all, search, tab, user?.id]);

  const totals = useMemo(
    () => ({
      documents: all.reduce((sum, kb) => sum + (kb.documentCount ?? 0), 0),
      chunks: all.reduce((sum, kb) => sum + (kb.chunkCount ?? 0), 0),
    }),
    [all],
  );

  const openCreate = () => {
    setForm({
      ...EMPTY_FORM,
      embeddingModelId: embeddingModels[0]?.id ?? '',
    });
    setFormError(null);
    setCreateOpen(true);
  };

  const submitCreate = async () => {
    setSubmitting(true);
    setFormError(null);
    const chunkConfig: ChunkConfig = {
      strategy: form.strategy,
      maxChars: form.maxChars,
      overlap: form.overlap,
    };
    const retrievalConfig: RetrievalConfig = {
      mode: form.mode,
      topK: form.topK,
      threshold: form.threshold,
      rerankModelId: null,
      rewriteModelId: null,
    };
    try {
      const created = await knowledgeApi.create({
        name: form.name.trim(),
        description: form.description.trim(),
        visibility: form.visibility,
        embeddingModelId: form.embeddingModelId || null,
        chunkConfig,
        retrievalConfig,
      } as Partial<KnowledgeBase> & { name: string });
      toast.success(`知识库「${created.name}」已创建`);
      setCreateOpen(false);
      kbs.reload();
      navigate(`/knowledge/${created.id}`);
    } catch (error) {
      setFormError(error);
    } finally {
      setSubmitting(false);
    }
  };

  const createSample = async () => {
    setSampleLoading(true);
    try {
      const suffix = new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date());
      const kb = await knowledgeApi.create({
        name: `示例知识库 · ${suffix}`,
        description: '用于体验文档入库、分块、检索与带引用问答的示例知识库。',
        visibility: 'organization',
        embeddingModelId: embeddingModels[0]?.id ?? null,
        chunkConfig: {
          strategy: 'structure',
          maxChars: 800,
          overlap: 100,
        },
        retrievalConfig: {
          mode: 'hybrid',
          topK: 5,
          threshold: 0,
          rerankModelId: null,
          rewriteModelId: null,
        },
      } as Partial<KnowledgeBase> & { name: string });
      try {
        await knowledgeApi.createTextDocument(kb.id, {
          name: '差旅与费用报销指引（示例）.md',
          content: SAMPLE_DOCUMENT,
        });
        toast.success('示例知识库已创建，示例文档已加入处理队列');
      } catch (error) {
        toast.error(`知识库已创建，但示例文档写入失败：${errorMessage(error)}`);
      }
      navigate(`/knowledge/${kb.id}`);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSampleLoading(false);
    }
  };

  return (
    <div className="page knowledge-list-page">
      <PageHeader
        eyebrow="资源库"
        title="知识库"
        description="文档上传后自动解析、分块与索引。检索测试和问答引用都基于这里的真实内容。"
        actions={
          <>
            <Button
              variant="secondary"
              icon={<Sparkles size={15} />}
              onClick={createSample}
              loading={sampleLoading}
              title="创建知识库并写入一份真实示例文档"
            >
              创建示例知识库
            </Button>
            {can('knowledge:write') && (
              <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
                新建知识库
              </Button>
            )}
          </>
        }
      />

      <div className="knowledge-summary">
        <div className="knowledge-summary-item">
          <BookOpen size={18} />
          <div>
            <strong>{formatNumber(counts.all)}</strong>
            <span>可访问知识库</span>
          </div>
        </div>
        <div className="knowledge-summary-item">
          <FileText size={18} />
          <div>
            <strong>{formatNumber(totals.documents)}</strong>
            <span>文档总数</span>
          </div>
        </div>
        <div className="knowledge-summary-item">
          <Layers size={18} />
          <div>
            <strong>{formatNumber(totals.chunks)}</strong>
            <span>知识切片</span>
          </div>
        </div>
        <div className="knowledge-summary-item">
          <Users size={18} />
          <div>
            <strong>{formatNumber(counts.mine)}</strong>
            <span>我创建的</span>
          </div>
        </div>
      </div>

      {!models.loading && models.data && embeddingModels.length === 0 && (
        <Callout
          tone="warn"
          title="尚未发现可用的 Embedding 模型"
          action={
            can('models:manage') ? (
              <Link to="/models" className="btn btn-secondary btn-sm">
                去模型管理
              </Link>
            ) : undefined
          }
        >
          新建知识库仍可尝试创建，但文档向量化需要 Embedding 模型。请让系统或组织管理员接入一个 OpenAI 兼容的
          Embedding 模型；关键词检索可在没有向量的情况下单独使用。
        </Callout>
      )}

      <div className="toolbar toolbar-wrap">
        <Tabs
          tabs={[
            { value: 'all', label: '全部', count: counts.all },
            { value: 'mine', label: '我创建的', count: counts.mine },
            { value: 'shared', label: '共享给我', count: counts.shared },
          ]}
          value={tab}
          onChange={setTab}
        />
        <SearchInput value={search} onChange={setSearch} placeholder="搜索知识库名称或描述" className="grow-search" />
      </div>

      {kbs.loading && !kbs.data ? (
        <LoadingState label="正在读取知识库…" />
      ) : kbs.error ? (
        <ErrorState error={kbs.error} onRetry={kbs.reload} />
      ) : filtered.length === 0 ? (
        all.length === 0 ? (
          <Card className="knowledge-empty-card">
            <EmptyState
              icon={<FolderOpen size={24} />}
              title="还没有可访问的知识库"
              description={
                can('knowledge:write')
                  ? '创建知识库并上传第一份文档，或者用示例知识库快速走通入库、检索与问答。'
                  : '当前组织还没有共享给你的知识库。请联系知识库所有者或组织管理员授权。'
              }
              action={
                can('knowledge:write') ? (
                  <div className="empty-actions">
                    <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
                      新建知识库
                    </Button>
                    <Button variant="secondary" icon={<Sparkles size={15} />} onClick={createSample} loading={sampleLoading}>
                      创建示例知识库
                    </Button>
                  </div>
                ) : undefined
              }
            />
          </Card>
        ) : (
          <EmptyState
            icon={<FolderOpen size={22} />}
            title="没有匹配的知识库"
            description={search ? `没有找到包含「${search}」的知识库，试试其他关键词。` : '当前筛选下暂无知识库。'}
            action={
              search ? (
                <Button variant="secondary" onClick={() => setSearch('')}>
                  清空搜索
                </Button>
              ) : undefined
            }
          />
        )
      ) : (
        <div className="kb-grid">
          {filtered.map((kb) => (
            <KnowledgeCard key={kb.id} kb={kb} isMine={kb.ownerId === user?.id} />
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="新建知识库"
        description="先确定可见范围、Embedding 模型与默认分块方式，创建后可继续调整。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button variant="primary" loading={submitting} onClick={submitCreate}>
              创建知识库
            </Button>
          </>
        }
      >
        <form
          className="modal-form"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            void submitCreate();
          }}
        >
          <div className="form-grid two">
            <Field label="知识库名称" htmlFor="kb-name" required>
              <Input
                id="kb-name"
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="例如：公司制度库"
                autoFocus
                required
                maxLength={80}
              />
            </Field>
            <Field label="可见范围" htmlFor="kb-visibility" hint="下级组织不会自动继承资源。">
              <Select
                id="kb-visibility"
                value={form.visibility}
                onChange={(event) =>
                  setForm({ ...form, visibility: event.target.value as KnowledgeBase['visibility'] })
                }
              >
                <option value="private">仅自己</option>
                <option value="organization">本组织</option>
                <option value="platform">全平台</option>
              </Select>
            </Field>
          </div>
          <Field label="描述" htmlFor="kb-description" hint="说明文档范围与用途，便于协作者判断是否适用。">
            <Textarea
              id="kb-description"
              rows={3}
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
              placeholder="例如：考勤、假期、差旅、报销等制度文件"
            />
          </Field>
          <Field
            label="Embedding 模型"
            htmlFor="kb-embedding"
            hint={
              embeddingModels.length === 0
                ? '暂无可用 Embedding 模型，可先创建，但文档向量化会依赖后续配置。'
                : '用于文档向量化与语义召回；更换后需要重新处理文档。'
            }
          >
            <Select
              id="kb-embedding"
              value={form.embeddingModelId}
              onChange={(event) => setForm({ ...form, embeddingModelId: event.target.value })}
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

          <div className="form-section">
            <div className="form-section-title">默认分块与检索</div>
            <div className="form-grid three">
              <Field label="分块方式" htmlFor="kb-strategy">
                <Select
                  id="kb-strategy"
                  value={form.strategy}
                  onChange={(event) =>
                    setForm({ ...form, strategy: event.target.value as ChunkConfig['strategy'] })
                  }
                >
                  <option value="structure">结构分块</option>
                  <option value="semantic">语义分块</option>
                  <option value="parent-child">父子分块</option>
                </Select>
              </Field>
              <Field label="最大字符数" htmlFor="kb-maxchars">
                <Input
                  id="kb-maxchars"
                  type="number"
                  min={100}
                  max={8000}
                  value={form.maxChars}
                  onChange={(event) => setForm({ ...form, maxChars: Number(event.target.value) })}
                />
              </Field>
              <Field label="重叠字符数" htmlFor="kb-overlap">
                <Input
                  id="kb-overlap"
                  type="number"
                  min={0}
                  max={2000}
                  value={form.overlap}
                  onChange={(event) => setForm({ ...form, overlap: Number(event.target.value) })}
                />
              </Field>
            </div>
            <p className="form-section-hint">{STRATEGY_DESCRIPTIONS[form.strategy]}</p>
            <div className="form-grid three">
              <Field label="检索方式" htmlFor="kb-mode">
                <Select
                  id="kb-mode"
                  value={form.mode}
                  onChange={(event) =>
                    setForm({ ...form, mode: event.target.value as RetrievalConfig['mode'] })
                  }
                >
                  <option value="hybrid">混合检索</option>
                  <option value="keyword">关键词</option>
                  <option value="vector">语义向量</option>
                </Select>
              </Field>
              <Field label="召回数量 topK" htmlFor="kb-topk">
                <Input
                  id="kb-topk"
                  type="number"
                  min={1}
                  max={50}
                  value={form.topK}
                  onChange={(event) => setForm({ ...form, topK: Number(event.target.value) })}
                />
              </Field>
              <Field label="相关度阈值" htmlFor="kb-threshold">
                <Input
                  id="kb-threshold"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={form.threshold}
                  onChange={(event) => setForm({ ...form, threshold: Number(event.target.value) })}
                />
              </Field>
            </div>
          </div>

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

function KnowledgeCard({ kb, isMine }: { kb: KnowledgeBase; isMine: boolean }) {
  return (
    <Link to={`/knowledge/${kb.id}`} className="kb-card">
      <div className="kb-card-top">
        <span className="kb-card-icon">
          <BookOpen size={19} />
        </span>
        <VisibilityBadge visibility={kb.visibility} />
      </div>
      <div className="kb-card-body">
        <h3>{kb.name}</h3>
        <p>{kb.description || '暂无描述'}</p>
      </div>
      <div className="kb-card-stats">
        <span>
          <FileText size={14} />
          <strong>{formatNumber(kb.documentCount)}</strong> 文档
        </span>
        <span>
          <Layers size={14} />
          <strong>{formatNumber(kb.chunkCount)}</strong> 切片
        </span>
      </div>
      <div className="kb-card-foot">
        <span className="kb-owner">
          <Users size={13} />
          {isMine ? '我创建' : '协作可见'}
        </span>
        <span className="kb-date">
          <Calendar size={13} />
          {formatDate(kb.createdAt)}
        </span>
        <span className="kb-card-arrow">
          <ArrowRight size={15} />
        </span>
      </div>
      {kb.chunkConfig && (
        <div className="kb-card-meta">
          <Badge tone="neutral">
            {kb.chunkConfig.strategy === 'structure'
              ? '结构分块'
              : kb.chunkConfig.strategy === 'semantic'
                ? '语义分块'
                : '父子分块'}
          </Badge>
          <Badge tone="neutral">
            {kb.retrievalConfig?.mode === 'hybrid'
              ? '混合检索'
              : kb.retrievalConfig?.mode === 'vector'
                ? '语义向量'
                : '关键词'}
          </Badge>
        </div>
      )}
    </Link>
  );
}
