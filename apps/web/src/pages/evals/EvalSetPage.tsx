import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Download,
  FileJson,
  FileSpreadsheet,
  FileText,
  FlaskConical,
  MessageSquareWarning,
  Plus,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
} from 'lucide-react';
import type { DocumentItem, EvalCase, Job, KnowledgeBase, RetrievalConfig } from '../../api/types';
import { appApi, evalApi, jobApi, knowledgeApi, modelApi } from '../../api/client';
import { useApi, useDocumentTitle, usePolling } from '../../hooks';
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
  ProgressBar,
  SearchInput,
  Select,
  StatusBadge,
  Tabs,
  Textarea,
  useConfirm,
} from '../../components/ui';
import { RetrievalConfigFields } from '../../components/RetrievalConfigFields';
import { DEFAULT_RETRIEVAL_CONFIG, RETRIEVAL_MODE_LABELS } from '../../utils/permissions';
import {
  errorMessage,
  formatDate,
  formatNumber,
  formatPercent,
  normalizeProgress,
  truncate,
} from '../../utils/format';

type SetTab = 'cases' | 'runs';
type ImportMode = 'json' | 'csv';

interface CaseDraft {
  question: string;
  expectedAnswer: string;
  documentId?: string;
  tags?: string[];
}

export function EvalSetPage() {
  useDocumentTitle('评测集');
  const { id = '' } = useParams();
  const { can } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [tab, setTab] = useState<SetTab>('cases');
  const [caseSearch, setCaseSearch] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [expected, setExpected] = useState('');
  const [documentId, setDocumentId] = useState('');
  const [tags, setTags] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<unknown>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importMode, setImportMode] = useState<ImportMode>('json');
  const [importText, setImportText] = useState('');
  const [importPreview, setImportPreview] = useState<CaseDraft[]>([]);
  const [importErrors, setImportErrors] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<unknown>(null);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [generateKbId, setGenerateKbId] = useState('');
  const [generateCount, setGenerateCount] = useState(5);
  const [generating, setGenerating] = useState(false);
  const [generateJob, setGenerateJob] = useState<{ jobId: string } | null>(null);
  const [generateError, setGenerateError] = useState<unknown>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [runName, setRunName] = useState('');
  const [configA, setConfigA] = useState<RetrievalConfig>({ ...DEFAULT_RETRIEVAL_CONFIG });
  const [configB, setConfigB] = useState<RetrievalConfig>({ ...DEFAULT_RETRIEVAL_CONFIG, mode: 'keyword' });
  const [compare, setCompare] = useState(false);
  const [creatingRun, setCreatingRun] = useState(false);
  const [runError, setRunError] = useState<unknown>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const setState = useApi(() => evalApi.getSet(id), [id], { enabled: Boolean(id) });
  const casesState = useApi(() => evalApi.cases(id), [id], { enabled: Boolean(id) });
  const runsState = useApi(() => evalApi.runs(id), [id], { enabled: Boolean(id) });
  const modelsState = useApi(() => modelApi.list().catch(() => []), []);
  const kbsState = useApi(() => knowledgeApi.list().catch(() => []), []);
  const appState = useApi(() => appApi.get(setState.data!.appId), [setState.data?.appId], {
    enabled: Boolean(setState.data?.appId),
  });
  const docsState = useApi(
    async () => {
      const app = appState.data;
      if (!app) return [];
      const lists = await Promise.all(
        (app.kbIds ?? []).map((kbId) => knowledgeApi.documents(kbId).catch(() => [] as DocumentItem[])),
      );
      return lists.flat();
    },
    [appState.data?.id, (appState.data?.kbIds ?? []).join(',')],
    { enabled: Boolean(appState.data) },
  );

  const set = setState.data;
  const cases = casesState.data ?? [];
  const runs = runsState.data ?? [];
  const models = modelsState.data ?? [];
  const kbs = kbsState.data ?? [];
  const docs = docsState.data ?? [];
  const appKbs: KnowledgeBase[] = (appState.data?.kbIds ?? [])
    .map((kbId) => kbs.find((kb) => kb.id === kbId))
    .filter((kb): kb is KnowledgeBase => Boolean(kb));

  const jobState = useApi(() => jobApi.get(generateJob!.jobId), [generateJob?.jobId], {
    enabled: Boolean(generateJob),
  });
  const generateJobData: Job | null = jobState.data;
  const jobActive =
    generateJobData?.status === 'QUEUED' || generateJobData?.status === 'RUNNING';
  const activeRuns = runs.some((run) => run.status === 'QUEUED' || run.status === 'RUNNING');
  usePolling(() => runsState.reload(), 4000, activeRuns);
  usePolling(() => jobState.reload(), 3000, Boolean(generateJob) && jobActive);

  useEffect(() => {
    if (generateJobData?.status === 'COMPLETED') {
      casesState.reload();
      setState.reload();
    }
    // Only react to a status transition, not to every cases reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generateJobData?.status]);

  const filteredCases = useMemo(() => {
    const keyword = caseSearch.trim().toLowerCase();
    if (!keyword) return cases;
    return cases.filter(
      (item) =>
        item.question.toLowerCase().includes(keyword) ||
        item.expectedAnswer.toLowerCase().includes(keyword) ||
        item.tags?.some((tag) => tag.toLowerCase().includes(keyword)),
    );
  }, [cases, caseSearch]);

  const docName = (docId?: string | null) =>
    docs.find((doc) => doc.id === docId)?.name ?? (docId ? docId.slice(0, 10) : '');

  if (!can('eval:manage')) {
    return (
      <div className="page">
        <PageHeader title="评测集" />
        <Card><PermissionDenied permission="eval:manage" /></Card>
      </div>
    );
  }

  if (setState.loading && !set) {
    return <div className="page"><LoadingState label="正在读取评测集…" /></div>;
  }

  if (setState.error || !set) {
    return (
      <div className="page">
        <PageHeader
          title="评测集"
          back={<Link to="/evals" className="back-link"><ArrowLeft size={15} /> 返回效果评测</Link>}
        />
        <ErrorState error={setState.error ?? new Error('评测集不存在')} onRetry={setState.reload} />
      </div>
    );
  }

  const refreshAll = () => {
    casesState.reload();
    setState.reload();
    runsState.reload();
  };

  const openAdd = () => {
    setQuestion('');
    setExpected('');
    setDocumentId('');
    setTags('');
    setAddError(null);
    setAddOpen(true);
  };

  const submitCase = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!question.trim() || !expected.trim()) {
      setAddError(new Error('请填写问题与标准答案。'));
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      await evalApi.addCase(set.id, {
        question: question.trim(),
        expectedAnswer: expected.trim(),
        documentId: documentId || undefined,
        tags: tags
          .split(/[,，;；]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      });
      toast.success('用例已添加');
      setAddOpen(false);
      refreshAll();
    } catch (error) {
      setAddError(error);
    } finally {
      setAdding(false);
    }
  };

  const deleteCase = async (item: EvalCase) => {
    if (
      !(await confirm({
        title: '删除评测用例？',
        description: `「${truncate(item.question, 60)}」将从评测集中移除。已完成的运行结果保留自己的快照。`,
        confirmLabel: '删除用例',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await evalApi.removeCase(item.id);
      toast.success('用例已删除');
      refreshAll();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const parseImport = (text: string, mode: ImportMode) => {
    const errors: string[] = [];
    let items: CaseDraft[] = [];
    if (!text.trim()) {
      setImportPreview([]);
      setImportErrors([]);
      return;
    }
    if (mode === 'json') {
      try {
        const raw = JSON.parse(text) as unknown;
        const list = Array.isArray(raw)
          ? raw
          : raw && typeof raw === 'object' && Array.isArray((raw as { cases?: unknown }).cases)
            ? ((raw as { cases: unknown[] }).cases)
            : null;
        if (!list) {
          errors.push('JSON 顶层需为数组，或包含 cases 数组。');
        } else {
          list.forEach((entry, index) => {
            if (!entry || typeof entry !== 'object') {
              errors.push(`第 ${index + 1} 条不是对象。`);
              return;
            }
            const record = entry as Record<string, unknown>;
            const q = typeof record.question === 'string' ? record.question.trim() : '';
            const a = typeof record.expectedAnswer === 'string' ? record.expectedAnswer.trim() : '';
            if (!q) errors.push(`第 ${index + 1} 条缺少 question。`);
            if (!a) errors.push(`第 ${index + 1} 条缺少 expectedAnswer。`);
            if (q && a) {
              items.push({
                question: q,
                expectedAnswer: a,
                documentId: typeof record.documentId === 'string' && record.documentId ? record.documentId : undefined,
                tags: Array.isArray(record.tags)
                  ? record.tags.filter((tag): tag is string => typeof tag === 'string')
                  : typeof record.tags === 'string'
                    ? record.tags.split(/[,，;；|]/).map((tag) => tag.trim()).filter(Boolean)
                    : undefined,
              });
            }
          });
        }
      } catch {
        errors.push('JSON 解析失败，请检查引号、逗号与括号。');
      }
    } else {
      const rows = parseCsv(text);
      if (rows.length === 0) errors.push('CSV 为空。');
      const header = rows[0]?.map((cell) => cell.trim().toLowerCase()) ?? [];
      const questionIndex = header.indexOf('question');
      const answerIndex = header.indexOf('expectedanswer');
      const hasHeader = questionIndex >= 0 && answerIndex >= 0;
      const body = hasHeader ? rows.slice(1) : rows;
      if (!hasHeader) {
        errors.push('CSV 需要表头 question,expectedAnswer（可追加 documentId,tags）。');
      } else {
        body.forEach((row, index) => {
          const q = (row[questionIndex] ?? '').trim();
          const a = (row[answerIndex] ?? '').trim();
          if (!q || !a) {
            errors.push(`第 ${index + (hasHeader ? 2 : 1)} 行缺少问题或标准答案。`);
            return;
          }
          const docIndex = header.indexOf('documentid');
          const tagsIndex = header.indexOf('tags');
          items.push({
            question: q,
            expectedAnswer: a,
            documentId: docIndex >= 0 ? (row[docIndex] ?? '').trim() || undefined : undefined,
            tags:
              tagsIndex >= 0
                ? (row[tagsIndex] ?? '')
                    .split(/[|;；]/)
                    .map((tag) => tag.trim())
                    .filter(Boolean)
                : undefined,
          });
        });
      }
    }
    setImportPreview(items);
    setImportErrors(errors.slice(0, 20));
  };

  const readImportFile = async (file: File | null) => {
    if (!file) return;
    const text = await file.text();
    setImportText(text);
    const mode: ImportMode = file.name.toLowerCase().endsWith('.json') ? 'json' : 'csv';
    setImportMode(mode);
    parseImport(text, mode);
  };

  const submitImport = async () => {
    if (importPreview.length === 0) {
      setImportError(new Error('没有可导入的用例。'));
      return;
    }
    if (importErrors.length > 0) {
      setImportError(new Error('请先修正预览中的错误。'));
      return;
    }
    setImporting(true);
    setImportError(null);
    try {
      await evalApi.addCase(set.id, { cases: importPreview });
      toast.success(`已导入 ${importPreview.length} 条用例`);
      setImportOpen(false);
      setImportText('');
      setImportPreview([]);
      refreshAll();
    } catch (error) {
      setImportError(error);
    } finally {
      setImporting(false);
    }
  };

  const downloadTemplate = (mode: ImportMode) => {
    const content =
      mode === 'json'
        ? JSON.stringify(
            [
              { question: '一线城市经理出差住宿标准是多少？', expectedAnswer: '600 元/晚。', tags: ['差旅'] },
            ],
            null,
            2,
          )
        : 'question,expectedAnswer,documentId,tags\n"一线城市经理出差住宿标准是多少？","600 元/晚。","","差旅|住宿"';
    const blob = new Blob([content], { type: mode === 'json' ? 'application/json' : 'text/csv' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = mode === 'json' ? 'eval-cases-template.json' : 'eval-cases-template.csv';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  const openGenerate = () => {
    setGenerateKbId(appKbs[0]?.id ?? kbs[0]?.id ?? '');
    setGenerateCount(5);
    setGenerateJob(null);
    setGenerateError(null);
    setGenerateOpen(true);
  };

  const submitGenerate = async () => {
    if (!generateKbId) {
      setGenerateError(new Error('请选择用于生成问题的知识库。'));
      return;
    }
    setGenerating(true);
    setGenerateError(null);
    try {
      const result = await evalApi.generate(set.id, { count: generateCount, kbId: generateKbId });
      setGenerateJob(result);
      toast.success('生成任务已提交，正在读取任务状态');
    } catch (error) {
      setGenerateError(error);
    } finally {
      setGenerating(false);
    }
  };

  const openRun = () => {
    const base = appState.data?.retrievalConfig ?? DEFAULT_RETRIEVAL_CONFIG;
    setConfigA({ ...base });
    setConfigB({ ...base, mode: base.mode === 'keyword' ? 'hybrid' : 'keyword' });
    setRunName('');
    setCompare(false);
    setRunError(null);
    setRunOpen(true);
  };

  const submitRun = async () => {
    if (cases.length === 0) {
      setRunError(new Error('评测集还没有用例，无法运行。'));
      return;
    }
    setCreatingRun(true);
    setRunError(null);
    try {
      const run = await evalApi.createRun(set.id, {
        name: runName.trim() || undefined,
        configA,
        configB: compare ? configB : undefined,
      });
      toast.success('评测运行已入队');
      setRunOpen(false);
      runsState.reload();
      navigate(`/evals/${set.id}/runs/${run.id}`);
    } catch (error) {
      setRunError(error);
    } finally {
      setCreatingRun(false);
    }
  };

  return (
    <div className="page">
      <PageHeader
        back={<Link to="/evals" className="back-link"><ArrowLeft size={15} /> 返回效果评测</Link>}
        eyebrow="效果评测 / 评测集"
        title={
          <span className="title-with-badge">
            {set.name}
            <Badge tone="indigo">{set.caseCount} 条用例</Badge>
          </span>
        }
        description={
          <span className="kb-meta-line">
            {set.description || '暂无描述'} · 应用：
            {appState.data ? (
              <Link to={`/apps/${appState.data.id}`} className="text-link">{appState.data.name}</Link>
            ) : (
              set.appId
            )}{' '}
            · 创建于 {formatDate(set.createdAt)}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" icon={<FileJson size={15} />} onClick={() => { setImportMode('json'); setImportText(''); setImportPreview([]); setImportErrors([]); setImportError(null); setImportOpen(true); }}>
              导入 JSON / CSV
            </Button>
            <Button variant="secondary" icon={<Wand2 size={15} />} onClick={openGenerate}>
              LLM 生成
            </Button>
            <Button variant="primary" icon={<Plus size={15} />} onClick={openAdd}>
              添加用例
            </Button>
          </>
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><FileText size={17} /></div>
          <div className="stat-copy"><span>用例数</span><strong>{formatNumber(set.caseCount)}</strong><small>已加载 {cases.length} 条</small></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><FlaskConical size={17} /></div>
          <div className="stat-copy"><span>运行次数</span><strong>{formatNumber(runs.length)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Bot size={17} /></div>
          <div className="stat-copy"><span>目标应用</span><strong className="stat-small">{appState.data?.name ?? set.appId.slice(0, 10)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Sparkles size={17} /></div>
          <div className="stat-copy">
            <span>LLM 生成任务</span>
            <strong className="stat-small">{generateJobData ? jobStatusText(generateJobData) : generateJob ? '读取任务状态…' : '未运行'}</strong>
          </div>
        </div>
      </div>

      {generateJob && (
        <Callout
          tone={
            generateJobData?.status === 'FAILED'
              ? 'danger'
              : generateJobData?.status === 'COMPLETED'
                ? 'success'
                : 'info'
          }
          title={
            generateJobData
              ? jobStatusText(generateJobData)
              : jobState.error != null
                ? '无法读取生成任务状态'
                : '生成任务已提交'
          }
          action={
            <Button size="sm" variant="secondary" onClick={() => setGenerateJob(null)}>
              关闭状态
            </Button>
          }
        >
          {jobState.error != null ? (
            <span>
              任务状态接口不可用：{errorMessage(jobState.error)}。仍可手动刷新用例；任务标识保留在下方任务信息中供排查。
            </span>
          ) : generateJobData ? (
            <div className="job-progress">
              <ProgressBar
                value={normalizeProgress(generateJobData.progress)}
                tone={generateJobData.status === 'FAILED' ? 'red' : generateJobData.status === 'COMPLETED' ? 'green' : 'indigo'}
                label={`${Math.round(normalizeProgress(generateJobData.progress))}%`}
              />
              <span>
                {generateJobData.status === 'QUEUED' && '任务正在排队，尚未开始调用模型。'}
                {generateJobData.status === 'RUNNING' && '模型正在阅读文档并生成问题，页面每 3 秒更新进度。'}
                {generateJobData.status === 'COMPLETED' && '生成已完成，用例列表已自动刷新。请逐条审核后再作为标准答案使用。'}
                {generateJobData.status === 'FAILED' && `生成失败：${generateJobData.error || '服务端未返回失败原因。'}`}
                {generateJobData.attempts > 1 && ` 已尝试 ${generateJobData.attempts} 次。`}
              </span>
              <details className="job-id-detail">
                <summary>任务信息</summary>
                <div>任务 ID：<code>{generateJobData.id}</code></div>
                <div>类型：<code>{generateJobData.type}</code> · 尝试：{generateJobData.attempts} 次 · 创建：{formatDate(generateJobData.createdAt)}</div>
              </details>
            </div>
          ) : (
            <span>正在读取任务状态…演示 provider 会明确拒绝生成。</span>
          )}
        </Callout>
      )}

      <div className="eval-tabs">
        <Tabs
          tabs={[
            { value: 'cases', label: '用例', count: cases.length },
            { value: 'runs', label: '运行记录', count: runs.length },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {tab === 'cases' && (
        <Card padded={false}>
          <div className="card-toolbar">
            <div className="eval-toolbar">
              <Badge tone="neutral"><FileText size={12} /> {cases.length} 条用例</Badge>
              <Button size="sm" variant="ghost" icon={<MessageSquareWarning size={14} />} onClick={() => navigate('/feedback')}>
                从用户反馈加入
              </Button>
            </div>
            <SearchInput value={caseSearch} onChange={setCaseSearch} placeholder="搜索问题、答案或标签" />
          </div>
          {casesState.loading && !casesState.data ? (
            <LoadingState label="正在读取用例…" />
          ) : casesState.error ? (
            <div className="card-pad"><ErrorState error={casesState.error} onRetry={casesState.reload} compact /></div>
          ) : filteredCases.length === 0 ? (
            <EmptyState
              icon={<FileText size={23} />}
              title={cases.length === 0 ? '还没有评测用例' : '没有匹配的用例'}
              description={
                cases.length === 0
                  ? '可以手工录入、粘贴 JSON/CSV、按文档调用 LLM 生成，或把终端用户的点踩与纠错转为用例。'
                  : '调整搜索词后再试。'
              }
              action={
                cases.length === 0 ? (
                  <div className="empty-actions">
                    <Button variant="primary" icon={<Plus size={15} />} onClick={openAdd}>手工录入</Button>
                    <Button variant="secondary" icon={<Upload size={15} />} onClick={() => setImportOpen(true)}>导入文件</Button>
                  </div>
                ) : undefined
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="table eval-case-table">
                <thead>
                  <tr>
                    <th>问题</th>
                    <th>标准答案</th>
                    <th>关联文档</th>
                    <th>标签</th>
                    <th className="align-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCases.map((item) => (
                    <tr key={item.id}>
                      <td><div className="case-question-cell">{item.question}</div></td>
                      <td><div className="case-answer-cell">{item.expectedAnswer}</div></td>
                      <td className="cell-provider">{docName(item.documentId) || <span className="muted-text">—</span>}</td>
                      <td>
                        {item.tags?.length ? (
                          <div className="case-tags">{item.tags.map((tag) => <Badge key={tag} tone="neutral">{tag}</Badge>)}</div>
                        ) : (
                          <span className="muted-text">—</span>
                        )}
                      </td>
                      <td className="align-right">
                        <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => deleteCase(item)}>
                          删除
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="card-foot-note">
            标准答案应由业务方确认。导入时不会编造答案；LLM 生成只基于文档内容，失败会明确报错。
          </div>
        </Card>
      )}

      {tab === 'runs' && (
        <Card padded={false}>
          <div className="card-toolbar">
            <div className="eval-toolbar">
              <Badge tone="neutral"><FlaskConical size={12} /> {runs.length} 次运行</Badge>
              {activeRuns && <Badge tone="blue" dot>运行中自动刷新</Badge>}
            </div>
            <div className="card-toolbar-actions">
              <Button variant="primary" icon={<Plus size={15} />} onClick={openRun} disabled={cases.length === 0}>
                新建运行
              </Button>
            </div>
          </div>
          {runsState.loading && !runsState.data ? (
            <LoadingState label="正在读取运行记录…" />
          ) : runsState.error ? (
            <div className="card-pad"><ErrorState error={runsState.error} onRetry={runsState.reload} compact /></div>
          ) : runs.length === 0 ? (
            <EmptyState
              icon={<FlaskConical size={23} />}
              title="还没有运行记录"
              description="配置 A（可选配置 B）后运行评测。配置快照会保留，指标未标注时显示未标注。"
              action={
                <Button variant="primary" icon={<Plus size={15} />} onClick={openRun} disabled={cases.length === 0}>
                  新建运行
                </Button>
              }
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>运行</th>
                    <th>状态</th>
                    <th>配置</th>
                    <th>指标摘要</th>
                    <th>时间</th>
                    <th className="align-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id}>
                      <td>
                        <Link to={`/evals/${set.id}/runs/${run.id}`} className="table-link">
                          {run.name || '未命名运行'}
                        </Link>
                      </td>
                      <td>
                        <div className="run-status-cell">
                          <StatusBadge status={run.status} />
                          {(run.status === 'QUEUED' || run.status === 'RUNNING') && (
                            <ProgressBar value={normalizeProgress(run.progress)} />
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="run-metrics-mini">
                          <span><strong>A</strong> {RETRIEVAL_MODE_LABELS[run.configA?.mode] ?? run.configA?.mode}</span>
                          {run.configB && <span><strong>B</strong> {RETRIEVAL_MODE_LABELS[run.configB.mode] ?? run.configB.mode}</span>}
                        </div>
                      </td>
                      <td>
                        <RunMetricMini label="答案匹配" value={run.metricsA?.answerMatch} />
                        <RunMetricMini label="引用" value={run.metricsA?.citationAccuracy} />
                        <RunMetricMini label="召回" value={run.metricsA?.retrievalRecall} />
                      </td>
                      <td className="muted-cell">{formatDate(run.createdAt)}</td>
                      <td className="align-right">
                        <Link to={`/evals/${set.id}/runs/${run.id}`} className="row-action">
                          查看 <ArrowRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="card-foot-note">
            运行会调用真实检索与模型。指标为 0..1 的比例；“答案匹配”是词项匹配参考值，不是业务正确率。
          </div>
        </Card>
      )}

      {/* Add case */}
      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="添加评测用例"
        description="问题与标准答案应来自真实业务。关联文档用于定位与后续生成。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setAddOpen(false)}>取消</Button>
            <Button variant="primary" loading={adding} onClick={() => submitCase()} icon={<Plus size={15} />}>
              添加用例
            </Button>
          </>
        }
      >
        <form className="modal-form" onSubmit={(event) => { event.preventDefault(); void submitCase(); }}>
          <Field label="问题" htmlFor="case-question" required>
            <Textarea id="case-question" rows={3} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="终端用户实际会问的问题" autoFocus />
          </Field>
          <Field label="标准答案" htmlFor="case-expected" required hint="请填写经业务确认的答案；词项匹配会以此为参考。">
            <Textarea id="case-expected" rows={4} value={expected} onChange={(event) => setExpected(event.target.value)} placeholder="标准答案" />
          </Field>
          <div className="form-grid two">
            <Field label="关联文档（可选）" htmlFor="case-doc" hint="来自该应用绑定的知识库。">
              <Select id="case-doc" value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
                <option value="">不关联</option>
                {docs.map((doc) => <option key={doc.id} value={doc.id}>{doc.name}</option>)}
              </Select>
            </Field>
            <Field label="标签（可选）" htmlFor="case-tags" hint="用逗号分隔，例如：差旅,住宿。">
              <Input id="case-tags" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="差旅,住宿" />
            </Field>
          </div>
          {addError != null && <Callout tone="danger" title="添加失败">{errorMessage(addError)}</Callout>}
        </form>
      </Modal>

      {/* Import */}
      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title="导入评测用例"
        description="JSON 数组或 CSV（UTF-8，首行表头）。导入前会预览并校验，不合法行不会写入。"
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setImportOpen(false)}>取消</Button>
            <Button
              variant="primary"
              loading={importing}
              onClick={submitImport}
              disabled={importPreview.length === 0 || importErrors.length > 0}
              icon={<Upload size={15} />}
            >
              导入 {importPreview.length} 条
            </Button>
          </>
        }
      >
        <div className="import-tabs">
          <button type="button" className={importMode === 'json' ? 'is-active' : ''} onClick={() => { setImportMode('json'); parseImport(importText, 'json'); }}>
            <FileJson size={14} /> JSON
          </button>
          <button type="button" className={importMode === 'csv' ? 'is-active' : ''} onClick={() => { setImportMode('csv'); parseImport(importText, 'csv'); }}>
            <FileSpreadsheet size={14} /> CSV
          </button>
        </div>
        <div className="form-stack">
          <Field
            label={importMode === 'json' ? 'JSON 内容' : 'CSV 内容'}
            htmlFor="import-text"
            hint={importMode === 'json' ? '顶层数组或 { cases: [...] }；字段 question、expectedAnswer 必填。' : '列：question,expectedAnswer，可选 documentId,tags（tags 用 | 分隔）。'}
          >
            <Textarea
              id="import-text"
              rows={9}
              value={importText}
              onChange={(event) => { setImportText(event.target.value); parseImport(event.target.value, importMode); }}
              placeholder={importMode === 'json' ? '[\n  {"question": "...", "expectedAnswer": "...", "tags": ["制度"]}\n]' : 'question,expectedAnswer,documentId,tags\n"问题","答案","","制度|差旅"'}
            />
          </Field>
          <div className="import-actions">
            <input ref={fileInputRef} type="file" accept=".json,.csv,text/csv,application/json" hidden onChange={(event: ChangeEvent<HTMLInputElement>) => { void readImportFile(event.target.files?.[0] ?? null); event.target.value = ''; }} />
            <Button size="sm" variant="secondary" icon={<Upload size={14} />} onClick={() => fileInputRef.current?.click()}>
              读取文件
            </Button>
            <Button size="sm" variant="ghost" icon={<Download size={14} />} onClick={() => downloadTemplate(importMode)}>
              下载模板
            </Button>
            {importText && <span className="muted-text">已读取 {importText.length} 个字符</span>}
          </div>
          {(importPreview.length > 0 || importErrors.length > 0) && (
            <div className="import-preview">
              <div className="import-preview-head">
                <span>预览：{importPreview.length} 条可导入</span>
                <span className={importErrors.length ? 'text-danger' : ''}>{importErrors.length} 个错误</span>
              </div>
              {importPreview.length > 0 && (
                <div className="table-wrap">
                  <table className="table">
                    <thead><tr><th>问题</th><th>标准答案</th><th>标签</th></tr></thead>
                    <tbody>
                      {importPreview.slice(0, 5).map((item, index) => (
                        <tr key={index}>
                          <td>{truncate(item.question, 70)}</td>
                          <td>{truncate(item.expectedAnswer, 70)}</td>
                          <td>{item.tags?.join('、') || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {importErrors.length > 0 && <div className="import-errors">{importErrors.map((error, index) => <span key={index}>{error}</span>)}</div>}
            </div>
          )}
          {importError != null && <Callout tone="danger" title="导入失败">{errorMessage(importError)}</Callout>}
          <div className="csv-template">示例：CSV 中的 tags 使用 <code>|</code> 分隔；导入后不会自动生成标准答案。</div>
        </div>
      </Modal>

      {/* Generate */}
      <Modal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        title="按文档生成评测问题"
        description="调用真实 Chat 模型阅读知识库文档并生成问题与参考答案。任务入队后需等待后端处理。"
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setGenerateOpen(false)}>关闭</Button>
            {!generateJob ? (
              <Button variant="primary" loading={generating} onClick={submitGenerate} icon={<Wand2 size={15} />}>
                生成并加入队列
              </Button>
            ) : generateJobData?.status === 'FAILED' ? (
              <Button variant="primary" onClick={submitGenerate} loading={generating} icon={<Wand2 size={15} />}>
                重新生成
              </Button>
            ) : (
              <Button variant="primary" onClick={() => { casesState.reload(); setState.reload(); }}>刷新用例</Button>
            )}
          </>
        }
      >
        <div className="form-stack">
          <Field label="知识库" htmlFor="generate-kb" required hint={appKbs.length ? '来自应用绑定的知识库。' : '应用未绑定知识库，可从可访问知识库中选择。'}>
            <Select id="generate-kb" value={generateKbId} onChange={(event) => setGenerateKbId(event.target.value)}>
              {(appKbs.length ? appKbs : kbs).map((kb) => <option key={kb.id} value={kb.id}>{kb.name} · {kb.documentCount} 文档</option>)}
            </Select>
          </Field>
          <Field label="生成数量" htmlFor="generate-count" hint="1–20 条。生成结果需要业务审核后才能作为标准答案。">
            <Input id="generate-count" type="number" min={1} max={20} value={generateCount} onChange={(event) => setGenerateCount(Number(event.target.value))} />
          </Field>
          {generateJob && (
            <div className={`generate-state${generateJobData?.status === 'FAILED' ? ' is-failed' : ''}`}>
              <Sparkles size={16} />
              <div>
                <strong>{generateJobData ? jobStatusText(generateJobData) : '正在读取任务状态…'}</strong>
                {generateJobData ? (
                  <>
                    <ProgressBar
                      value={normalizeProgress(generateJobData.progress)}
                      tone={generateJobData.status === 'FAILED' ? 'red' : generateJobData.status === 'COMPLETED' ? 'green' : 'indigo'}
                      label={`${Math.round(normalizeProgress(generateJobData.progress))}%`}
                    />
                    <div className="muted-text">
                      {generateJobData.status === 'FAILED'
                        ? generateJobData.error || '服务端未返回失败原因。'
                        : generateJobData.status === 'COMPLETED'
                          ? '已完成，用例列表已刷新。请审核生成内容。'
                          : '任务在后台执行，页面会自动更新进度。'}
                    </div>
                    <details className="job-id-detail">
                      <summary>任务信息</summary>
                      <div>任务 ID：<code>{generateJobData.id}</code> · 尝试 {generateJobData.attempts} 次</div>
                    </details>
                  </>
                ) : jobState.error != null ? (
                  <div className="muted-text">状态接口不可用：{errorMessage(jobState.error)}</div>
                ) : (
                  <div className="muted-text">正在读取任务状态…</div>
                )}
              </div>
            </div>
          )}
          {generateError != null && <Callout tone="danger" title="生成失败">{errorMessage(generateError)}</Callout>}
          <Callout tone="warn" title="生成内容仍需人工确认">
            LLM 生成的问题与答案只基于文档片段，可能遗漏或误解。未经业务确认的内容不得作为验收标准。
          </Callout>
        </div>
      </Modal>

      {/* New run */}
      <Modal
        open={runOpen}
        onClose={() => setRunOpen(false)}
        title="新建评测运行"
        description="配置 A 必填，可选择同时运行配置 B。运行会使用真实检索与模型调用。"
        size="xl"
        footer={
          <>
            <Button variant="secondary" onClick={() => setRunOpen(false)}>取消</Button>
            <Button variant="primary" loading={creatingRun} onClick={submitRun} icon={<FlaskConical size={15} />} disabled={cases.length === 0}>
              开始运行
            </Button>
          </>
        }
      >
        <div className="form-stack">
          <Field label="运行名称（可选）" htmlFor="run-name" hint="便于区分配置与时间。">
            <Input id="run-name" value={runName} onChange={(event) => setRunName(event.target.value)} placeholder="例如：混合检索 vs 关键词检索" />
          </Field>
          <div className="run-config-grid">
            <div className="run-config-card is-a">
              <div className="run-config-head"><strong>配置 A</strong><Badge tone="indigo">必填</Badge></div>
              <RetrievalConfigFields value={configA} onChange={setConfigA} models={models} layout="stacked" />
            </div>
            {compare ? (
              <div className="run-config-card is-b">
                <div className="run-config-head"><strong>配置 B</strong><Badge tone="amber">对比</Badge></div>
                <RetrievalConfigFields value={configB} onChange={setConfigB} models={models} layout="stacked" />
              </div>
            ) : (
              <div className="run-config-card">
                <div className="run-config-head"><strong>配置 B</strong><Badge tone="neutral">未启用</Badge></div>
                <p className="muted-text">开启下方对比后会同时运行第二套检索配置，并展示两组指标。</p>
              </div>
            )}
          </div>
          <label className="run-compare-toggle">
            <input type="checkbox" checked={compare} onChange={(event) => setCompare(event.target.checked)} />
            同时运行配置 B，并比较两套检索配置
          </label>
          <Callout tone="info">
            本次运行将跑 {cases.length} 条用例。配置快照会保存在运行记录中；不会修改应用配置。
          </Callout>
          {runError != null && <Callout tone="danger" title="无法开始运行">{errorMessage(runError)}</Callout>}
        </div>
      </Modal>
    </div>
  );
}

function RunMetricMini({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="run-metrics-mini">
      <span>
        {label} <strong>{value == null ? '未标注' : formatPercent(value)}</strong>
      </span>
    </div>
  );
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (inQuotes) {
      if (char === '"' && next === '"') {
        cell += '"';
        index += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      inQuotes = true;
    } else if (char === ',') {
      row.push(cell);
      cell = '';
    } else if (char === '\n') {
      row.push(cell.replace(/\r$/, ''));
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = '';
    } else {
      cell += char;
    }
  }
  row.push(cell.replace(/\r$/, ''));
  if (row.some((value) => value.trim())) rows.push(row);
  return rows;
}

function jobStatusText(job: Job): string {
  if (job.status === 'QUEUED') return '生成任务排队中';
  if (job.status === 'RUNNING') return `正在生成 · ${Math.round(normalizeProgress(job.progress))}%`;
  if (job.status === 'COMPLETED') return '生成完成';
  return '生成失败';
}
