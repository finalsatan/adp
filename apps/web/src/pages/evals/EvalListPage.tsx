import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Bot,
  Calendar,
  FileQuestion,
  FlaskConical,
  Layers,
  Plus,
  Target,
} from 'lucide-react';
import type { EvalSet } from '../../api/types';
import { appApi, evalApi } from '../../api/client';
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
  Textarea,
} from '../../components/ui';
import { errorMessage, formatDate, formatNumber } from '../../utils/format';

export function EvalListPage() {
  useDocumentTitle('效果评测');
  const { can } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [appId, setAppId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<unknown>(null);

  const setsState = useApi(() => evalApi.sets(), []);
  const appsState = useApi(() => appApi.list().catch(() => []), []);
  const sets = setsState.data ?? [];
  const apps = appsState.data ?? [];

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return sets.filter(
      (set) =>
        !keyword ||
        set.name.toLowerCase().includes(keyword) ||
        (set.description ?? '').toLowerCase().includes(keyword),
    );
  }, [sets, search]);

  const totalCases = useMemo(() => sets.reduce((sum, set) => sum + (set.caseCount ?? 0), 0), [sets]);

  if (!can('eval:manage')) {
    return (
      <div className="page">
        <PageHeader title="效果评测" description="用真实问题比较检索配置，指标据实呈现。" />
        <Card><PermissionDenied permission="eval:manage" /></Card>
      </div>
    );
  }

  const openCreate = () => {
    setName('');
    setDescription('');
    setAppId(apps[0]?.id ?? '');
    setFormError(null);
    setCreateOpen(true);
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!name.trim()) {
      setFormError(new Error('请填写评测集名称。'));
      return;
    }
    if (!appId) {
      setFormError(new Error('请选择评测所针对的应用。'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      const created = await evalApi.createSet({
        name: name.trim(),
        description: description.trim() || undefined,
        appId,
      });
      toast.success('评测集已创建');
      setCreateOpen(false);
      navigate(`/evals/${created.id}`);
    } catch (error) {
      setFormError(error);
    } finally {
      setSubmitting(false);
    }
  };

  const appName = (id: string) => apps.find((app) => app.id === id)?.name ?? id;

  return (
    <div className="page">
      <PageHeader
        eyebrow="应用观测"
        title="效果评测"
        description="用人工录入、文档生成或终端反馈建立问题集，跑两套检索配置并比较结果。所有指标按实际数据呈现。"
        actions={
          <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
            新建评测集
          </Button>
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><FlaskConical size={17} /></div>
          <div className="stat-copy"><span>评测集</span><strong>{formatNumber(sets.length)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><FileQuestion size={17} /></div>
          <div className="stat-copy"><span>问题总数</span><strong>{formatNumber(totalCases)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Bot size={17} /></div>
          <div className="stat-copy"><span>覆盖应用</span><strong>{formatNumber(new Set(sets.map((set) => set.appId)).size)}</strong></div>
        </div>
      </div>

      <Callout tone="warn" title="指标口径">
        答案匹配使用词项匹配作为参考值，不等于经业务确认的“正确率”。召回率与引用准确率依赖标准答案和标注数据；没有标注的指标会显示“未标注”，不会用 0 代替。
      </Callout>

      <div className="toolbar toolbar-wrap">
        <div className="filter-group">
          <Badge tone="neutral"><Target size={12} /> 共 {sets.length} 个评测集</Badge>
        </div>
        <SearchInput value={search} onChange={setSearch} placeholder="搜索评测集名称或描述" className="grow-search" />
      </div>

      {setsState.loading && !setsState.data ? (
        <LoadingState label="正在读取评测集…" />
      ) : setsState.error ? (
        <ErrorState error={setsState.error} onRetry={setsState.reload} />
      ) : filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<FlaskConical size={24} />}
            title={sets.length === 0 ? '还没有评测集' : '没有匹配的评测集'}
            description={
              sets.length === 0
                ? '先创建一个评测集，录入问题或从文档生成，再运行检索配置对比。'
                : '调整搜索词后再试。'
            }
            action={
              sets.length === 0 ? (
                <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>
                  新建评测集
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <div className="eval-grid">
          {filtered.map((set) => (
            <EvalSetCard key={set.id} set={set} appName={appName(set.appId)} />
          ))}
        </div>
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="新建评测集"
        description="评测集绑定一个问答应用；用例可以手工录入、JSON/CSV 导入、按文档生成或从反馈加入。"
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>取消</Button>
            <Button variant="primary" loading={submitting} onClick={() => submit()} disabled={apps.length === 0}>
              创建评测集
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
          <Field label="评测集名称" htmlFor="eval-name" required>
            <Input
              id="eval-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：HR 制度问答基线（业务确认版）"
              autoFocus
              maxLength={100}
            />
          </Field>
          <Field label="描述" htmlFor="eval-description" hint="说明数据来源、确认人和适用范围。">
            <Textarea
              id="eval-description"
              rows={2}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="例如：200 条问题由 HR 业务方确认标准答案，用于比较关键词与混合检索。"
            />
          </Field>
          <Field
            label="目标应用"
            htmlFor="eval-app"
            required
            hint={apps.length === 0 ? '暂无可选应用，请先创建知识问答。' : '评测会使用该应用的知识库与模型配置。'}
          >
            <Select id="eval-app" value={appId} onChange={(event) => setAppId(event.target.value)}>
              {apps.length === 0 && <option value="">暂无应用</option>}
              {apps.map((app) => (
                <option key={app.id} value={app.id}>
                  {app.name}
                  {app.publishedVersion ? ` · 已发布 v${app.publishedVersion}` : ' · 草稿'}
                </option>
              ))}
            </Select>
          </Field>
          {apps.length === 0 && (
            <Callout tone="warn" title="没有可选应用">
              评测需要绑定一个知识问答应用。请先创建应用并配置知识库。
            </Callout>
          )}
          {formError != null && (
            <Callout tone="danger" title="创建失败">{errorMessage(formError)}</Callout>
          )}
        </form>
      </Modal>
    </div>
  );
}

function EvalSetCard({ set, appName }: { set: EvalSet; appName: string }) {
  return (
    <article className="eval-card">
      <div className="eval-card-top">
        <span className="eval-card-icon"><FlaskConical size={18} /></span>
        <Badge tone="indigo">{set.caseCount} 条用例</Badge>
      </div>
      <div className="eval-card-body">
        <h3><Link to={`/evals/${set.id}`}>{set.name}</Link></h3>
        <p>{set.description || '暂无描述'}</p>
      </div>
      <div className="eval-card-meta">
        <span><Bot size={13} /> {appName}</span>
        <span><Calendar size={13} /> {formatDate(set.createdAt)}</span>
      </div>
      <div className="eval-card-foot">
        <span className="muted-text"><Layers size={12} /> {set.caseCount > 0 ? '可运行配置对比' : '待录入用例'}</span>
        <Link to={`/evals/${set.id}`} className="btn btn-secondary btn-sm">
          打开评测集 <ArrowRight size={13} />
        </Link>
      </div>
    </article>
  );
}
