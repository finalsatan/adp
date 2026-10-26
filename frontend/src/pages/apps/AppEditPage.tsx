import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchWithRefresh } from "../../api/client";
import { ApiError, getErrorMessage, isApiError, readErrorBody } from "../../api/errors";
import { ChatPanel, type ChatRunRequest } from "../../components/chat/ChatPanel";
import { streamSSE, type ChatSseEvent } from "../../lib/sse";
import { useMetadataFields } from "../kb/api";
import { useFolders } from "../kb/documents/api";
import { ParseResultView } from "../kb/documents/ParseResultView";
import {
  OPERATOR_LABELS,
  buildMetadataGroup,
  defaultRetrievalConfig,
  operatorsForType,
  useRetrievalConfig,
  type MetadataCondition,
} from "../kb/hit-test/api";
import { AppHeader } from "./AppHeader";
import { AppPublishModal } from "./AppPublishModal";
import {
  buildDebugChatBody,
  buildDraftPayload,
  kbRefSummary,
  useApp,
  useAppDraft,
  useChatModels,
  useKbCandidates,
  usePromptPreview,
  useRerankModels,
  useSaveAppDraft,
  withConfigDefaults,
  type AppConfigForm,
  type AppKbRef,
  type AppModel,
  type CitationOut,
  type CitationPreviewOut,
  type KbCandidateOut,
  type MetadataGroup,
  type PromptPreviewOut,
  type RetrievalConfig,
} from "./api";

const SAME_MODEL = "__same__";

/** 配置与调试页（原型 #v-app）。 */
export default function AppEditPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const appId = Number.isFinite(parsedId) ? parsedId : null;
  const { message } = AntApp.useApp();

  const { data: app, isLoading } = useApp(appId);
  const { data: draft } = useAppDraft(appId);
  const { data: candidates = [] } = useKbCandidates(appId);
  const { data: chatModelsPage } = useChatModels();
  const { data: rerankModelsPage } = useRerankModels();
  const { data: promptPreview } = usePromptPreview(appId);
  const saveDraft = useSaveAppDraft();

  const [config, setConfig] = useState<AppConfigForm | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [fixedPartOpen, setFixedPartOpen] = useState(false);
  const [addKbOpen, setAddKbOpen] = useState(false);
  const [previewChunk, setPreviewChunk] = useState<number | null>(null);

  useEffect(() => {
    if (draft) {
      setConfig((previous) => previous ?? withConfigDefaults(draft.config));
    }
  }, [draft]);

  const candidateByKb = useMemo(() => {
    const map = new Map<number, KbCandidateOut>();
    candidates.forEach((candidate) => map.set(candidate.kb_id, candidate));
    return map;
  }, [candidates]);

  if (isLoading || !app || !config) {
    return null;
  }

  const chatModels = chatModelsPage?.items ?? [];
  const rerankModels = rerankModelsPage?.items ?? [];

  const patch = (partial: Partial<AppConfigForm>) =>
    setConfig((current) => (current ? { ...current, ...partial } : current));

  const handleSave = () => {
    if (!draft) {
      return;
    }
    saveDraft.mutate(
      { id: app.id, body: buildDraftPayload(config, draft.draft_revision) },
      {
        onSuccess: () => message.success("草稿已保存"),
        onError: (error) => {
          if (isApiError(error) && error.code === "DRAFT_CONFLICT") {
            message.error("草稿已被他人修改，请刷新后重试");
            return;
          }
          message.error(getErrorMessage(error));
        },
      },
    );
  };

  const handleRestoreDefaultPrompt = () => {
    if (promptPreview) {
      patch({ system_prompt: promptPreview.default_system_prompt });
    }
  };

  const startDebugRun = ({ question, history, signal, onEvent }: ChatRunRequest) =>
    streamSSE<ChatSseEvent>(
      `/api/v1/apps/${app.id}/debug/chat`,
      buildDebugChatBody(question, history, draft?.draft_revision ?? 0),
      { signal, onEvent },
    );

  const handleCitationClick = (citation: CitationOut) => {
    const chunkId = citation.chunk_ids?.[0];
    if (chunkId != null) {
      setPreviewChunk(chunkId);
    }
  };

  return (
    <div>
      <AppHeader app={app} active="config" />

      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" loading={saveDraft.isPending} onClick={handleSave}>
          保存草稿
        </Button>
        <Button onClick={() => setPublishOpen(true)}>发布</Button>
        {draft?.has_changes ? <Tag color="orange">草稿有未发布的修改</Tag> : null}
      </Space>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <ModelPromptCard
            config={config}
            chatModels={chatModels}
            promptPreview={promptPreview}
            onModelChange={(partial) => patch({ model: { ...config.model, ...partial } })}
            onSystemPromptChange={(system_prompt) => patch({ system_prompt })}
            onShowFixedPart={() => setFixedPartOpen(true)}
            onRestoreDefault={handleRestoreDefaultPrompt}
          />

          <IntentRewriteCard
            config={config}
            chatModels={chatModels}
            onIntentChange={(partial) => patch({ intent: { ...config.intent, ...partial } })}
            onRewriteChange={(partial) => patch({ rewrite: { ...config.rewrite, ...partial } })}
            onMemoryTurnsChange={(memory_turns) => patch({ memory_turns })}
          />

          <KnowledgeCard
            config={config}
            candidateByKb={candidateByKb}
            onOpenAdd={() => setAddKbOpen(true)}
            onRemove={(kbId) =>
              patch({
                knowledge: {
                  ...config.knowledge,
                  kbs: config.knowledge.kbs.filter((ref) => ref.kb_id !== kbId),
                },
              })
            }
            onChangeRef={(next) =>
              patch({
                knowledge: {
                  ...config.knowledge,
                  kbs: config.knowledge.kbs.map((ref) =>
                    ref.kb_id === next.kb_id ? next : ref,
                  ),
                },
              })
            }
          />

          <DocRetrievalCard
            config={config}
            candidateByKb={candidateByKb}
            rerankModels={rerankModels}
            onDocChange={(partial) =>
              patch({ knowledge: { ...config.knowledge, doc: { ...config.knowledge.doc, ...partial } } })
            }
          />

          <ReplyCard
            config={config}
            onReplyChange={(partial) =>
              patch({ reply: { ...config.reply, ...partial } })
            }
          />
        </div>

        <div style={{ width: 420, flexShrink: 0 }}>
          <ChatPanel
            debug
            startRun={startDebugRun}
            onCitationClick={handleCitationClick}
            onChunkPreview={setPreviewChunk}
            emptyHint="输入问题测试草稿配置，Ctrl+Enter 发送。"
          />
        </div>
      </div>

      <Modal
        open={fixedPartOpen}
        title="系统拼接的部分"
        footer={null}
        width={720}
        onCancel={() => setFixedPartOpen(false)}
      >
        <Typography.Paragraph type="secondary">
          固定部分由系统拼接，不能修改。参考内容、引用标注要求、历史对话和当前问题都会加在它后面。
        </Typography.Paragraph>
        <pre style={{ whiteSpace: "pre-wrap" }}>{promptPreview?.fixed_part ?? ""}</pre>
      </Modal>

      <AddKbModal
        open={addKbOpen}
        candidates={candidates}
        selectedIds={config.knowledge.kbs.map((ref) => ref.kb_id)}
        onClose={() => setAddKbOpen(false)}
        onAdd={(kbIds) =>
          patch({
            knowledge: {
              ...config.knowledge,
              kbs: [
                ...config.knowledge.kbs,
                ...kbIds.map((kbId) => ({
                  kb_id: kbId,
                  retrieval: null,
                  filter: null,
                  qa_category_ids: [],
                })),
              ],
            },
          })
        }
      />

      <AppPublishModal app={app} open={publishOpen} onClose={() => setPublishOpen(false)} />

      <ChunkPreviewModal appId={app.id} chunkId={previewChunk} onClose={() => setPreviewChunk(null)} />
    </div>
  );
}

interface ModelPromptCardProps {
  config: AppConfigForm;
  chatModels: AppModel[];
  promptPreview: PromptPreviewOut | undefined;
  onModelChange: (partial: Partial<AppConfigForm["model"]>) => void;
  onSystemPromptChange: (value: string) => void;
  onShowFixedPart: () => void;
  onRestoreDefault: () => void;
}

/** 模型与提示词（原型 #v-app 第一张卡）。深度思考第六期，本期不显示。 */
function ModelPromptCard({
  config,
  chatModels,
  promptPreview,
  onModelChange,
  onSystemPromptChange,
  onShowFixedPart,
  onRestoreDefault,
}: ModelPromptCardProps) {
  return (
    <Card title="模型与提示词" style={{ marginBottom: 16 }}>
      <Form layout="vertical">
        <Space size="large" align="start" wrap>
          <Form.Item label="回答模型" style={{ minWidth: 220 }}>
            <Select
              placeholder="选择已启用的回答模型"
              value={config.model.chat_model_id ?? undefined}
              options={chatModels.map((model) => ({ value: model.id, label: model.name }))}
              onChange={(value) => onModelChange({ chat_model_id: value })}
            />
          </Form.Item>
          <Form.Item label="温度">
            <InputNumber
              min={0}
              max={2}
              step={0.1}
              value={config.model.temperature}
              onChange={(value) => onModelChange({ temperature: value ?? 0.3 })}
            />
          </Form.Item>
          <Form.Item label="最大输出" extra="留空用模型默认值">
            <InputNumber
              min={1}
              value={config.model.max_tokens ?? undefined}
              onChange={(value) => onModelChange({ max_tokens: value ?? null })}
            />
          </Form.Item>
        </Space>

        <Form.Item label="系统提示词">
          <Input.TextArea
            rows={6}
            value={config.system_prompt}
            placeholder={promptPreview?.default_system_prompt}
            onChange={(event) => onSystemPromptChange(event.target.value)}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            只写角色、语气和回答要求。参考内容、引用标注要求、历史对话和当前问题由系统拼接，不能修改。
          </Typography.Text>
          <div>
            <Button type="link" style={{ padding: 0 }} onClick={onShowFixedPart}>
              查看系统拼接的部分
            </Button>
            <Typography.Text type="secondary"> · </Typography.Text>
            <Button type="link" style={{ padding: 0 }} onClick={onRestoreDefault}>
              恢复默认模板
            </Button>
          </div>
        </Form.Item>
      </Form>
    </Card>
  );
}

interface IntentRewriteCardProps {
  config: AppConfigForm;
  chatModels: AppModel[];
  onIntentChange: (partial: Partial<AppConfigForm["intent"]>) => void;
  onRewriteChange: (partial: Partial<AppConfigForm["rewrite"]>) => void;
  onMemoryTurnsChange: (value: number | null) => void;
}

/** 问题理解（原型 #v-app 第二张卡）。改写提示词本阶段不开放修改。 */
function IntentRewriteCard({
  config,
  chatModels,
  onIntentChange,
  onRewriteChange,
  onMemoryTurnsChange,
}: IntentRewriteCardProps) {
  const modelOptions = [
    { value: SAME_MODEL, label: "同回答模型" },
    ...chatModels.map((model) => ({ value: String(model.id), label: model.name })),
  ];

  return (
    <Card
      title={
        <Space>
          <span>问题理解</span>
          <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
            两步都开启时并行执行
          </Typography.Text>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      <Form layout="vertical">
        <Form.Item
          label={
            <Space>
              <b>意图识别</b>
              <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
                判断是知识问题还是闲聊问候；拿不准时按知识问题处理
              </Typography.Text>
            </Space>
          }
        >
          <Switch
            checked={config.intent.enabled}
            onChange={(enabled) => onIntentChange({ enabled })}
          />
        </Form.Item>
        {config.intent.enabled ? (
          <Form.Item label="意图识别模型">
            <Select
              style={{ width: 240 }}
              value={config.intent.model_id == null ? SAME_MODEL : String(config.intent.model_id)}
              options={modelOptions}
              onChange={(value) =>
                onIntentChange({ model_id: value === SAME_MODEL ? null : Number(value) })
              }
            />
          </Form.Item>
        ) : null}

        <Form.Item
          label={
            <Space>
              <b>问题改写</b>
              <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
                结合前几轮对话，把问题改写成能单独理解的完整问题，再去检索
              </Typography.Text>
            </Space>
          }
        >
          <Switch
            checked={config.rewrite.enabled}
            onChange={(enabled) => onRewriteChange({ enabled })}
          />
        </Form.Item>
        {config.rewrite.enabled ? (
          <Form.Item label="问题改写模型" extra="改写提示词本阶段不开放修改">
            <Select
              style={{ width: 240 }}
              value={config.rewrite.model_id == null ? SAME_MODEL : String(config.rewrite.model_id)}
              options={modelOptions}
              onChange={(value) =>
                onRewriteChange({ model_id: value === SAME_MODEL ? null : Number(value) })
              }
            />
          </Form.Item>
        ) : null}

        <Form.Item
          label="记忆轮数"
          extra="回答、意图识别、问题改写都只带最近 N 轮的问题和回答文字；默认值待实测。"
        >
          <InputNumber
            min={0}
            style={{ width: 120 }}
            value={config.memory_turns ?? undefined}
            placeholder="待实测"
            onChange={(value) => onMemoryTurnsChange(value ?? null)}
          />
        </Form.Item>
      </Form>
    </Card>
  );
}

interface KnowledgeCardProps {
  config: AppConfigForm;
  candidateByKb: Map<number, KbCandidateOut>;
  onOpenAdd: () => void;
  onRemove: (kbId: number) => void;
  onChangeRef: (next: AppKbRef) => void;
}

/** 知识库（原型 #v-app 第三张卡）。本期只展示文档库。 */
function KnowledgeCard({
  config,
  candidateByKb,
  onOpenAdd,
  onRemove,
  onChangeRef,
}: KnowledgeCardProps) {
  const [expandedKbId, setExpandedKbId] = useState<number | null>(null);

  return (
    <Card
      title={
        <Space>
          <span>知识库</span>
          <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
            可多选
          </Typography.Text>
        </Space>
      }
      extra={
        <Button icon={<PlusOutlined />} onClick={onOpenAdd}>
          添加知识库
        </Button>
      }
      style={{ marginBottom: 16 }}
    >
      {config.knowledge.kbs.length === 0 ? (
        <Typography.Text type="secondary">还没有引用知识库。</Typography.Text>
      ) : null}

      {config.knowledge.kbs.map((ref) => {
        const candidate = candidateByKb.get(ref.kb_id);
        const expanded = expandedKbId === ref.kb_id;
        return (
          <div
            key={ref.kb_id}
            style={{ border: "1px solid #f0f0f0", borderRadius: 6, padding: 12, marginBottom: 8 }}
          >
            <Space wrap>
              <Tag color={candidate?.kind === "qa" ? "purple" : "blue"}>
                {candidate?.kind === "qa" ? "问答库" : "文档库"}
              </Tag>
              <b>{candidate?.name ?? `知识库 #${ref.kb_id}`}</b>
              {candidate ? (
                <Typography.Text type="secondary">{candidate.embedding_model.name}</Typography.Text>
              ) : null}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {kbRefSummary(ref)} · {filterSummary(ref)}
              </Typography.Text>
              {candidate?.kind !== "qa" ? (
                <Button
                  type="link"
                  style={{ padding: 0 }}
                  onClick={() => setExpandedKbId(expanded ? null : ref.kb_id)}
                >
                  {expanded ? "收起" : "设置"}
                </Button>
              ) : null}
              <Button type="link" danger style={{ padding: 0 }} onClick={() => onRemove(ref.kb_id)}>
                移除
              </Button>
            </Space>

            {expanded && candidate?.kind !== "qa" ? (
              <KbRefSettings kbId={ref.kb_id} value={ref} onChange={onChangeRef} />
            ) : null}
          </div>
        );
      })}
    </Card>
  );
}

/** 一条知识库引用的过滤条件说明。 */
export function filterSummary(ref: AppKbRef): string {
  const filter = ref.filter;
  const folders = filter?.folder_ids?.length ?? 0;
  const conditions = filter?.metadata?.conditions.length ?? 0;
  if (folders === 0 && conditions === 0) {
    return "无过滤";
  }
  const parts: string[] = [];
  if (folders > 0) {
    parts.push(`${folders} 个文件夹`);
  }
  if (conditions > 0) {
    parts.push(`元数据 ${conditions} 条`);
  }
  return `过滤：${parts.join("、")}`;
}

interface KbRefSettingsProps {
  kbId: number;
  value: AppKbRef;
  onChange: (next: AppKbRef) => void;
}

/** 单条知识库引用的检索与过滤设置（原型 #v-app 展开区）。 */
function KbRefSettings({ kbId, value, onChange }: KbRefSettingsProps) {
  const { data: defaultConfigData } = useRetrievalConfig(kbId);
  const { data: folders = [] } = useFolders(kbId);
  const { data: metadataFields = [] } = useMetadataFields(kbId);

  const override = value.retrieval;
  const defaultConfig = defaultConfigData ? defaultRetrievalConfig(defaultConfigData) : null;

  const setRetrieval = (retrieval: RetrievalConfig | null) =>
    onChange({ ...value, retrieval });

  const setFilter = (filter: AppKbRef["filter"]) => onChange({ ...value, filter });

  const toggleOverride = (enabled: boolean) => {
    if (!enabled) {
      setRetrieval(null);
      return;
    }
    const base = defaultConfig ?? {
      mode: "hybrid" as const,
      vector_weight: 0.5,
      fulltext_weight: 0.5,
      top_k: 10,
      score_threshold: 0,
      rerank: { enabled: false, model_id: null, top_n: 10 },
    };
    setRetrieval({ adjacent_n: 0, ...base });
  };

  return (
    <div style={{ marginTop: 12 }}>
      <Form layout="vertical" size="small">
        <Form.Item label="检索方式">
          <Space>
            <Switch checked={override !== null} onChange={toggleOverride} />
            <Typography.Text type="secondary">
              {override ? "已覆盖默认配置" : "用知识库默认配置"}
            </Typography.Text>
            {override ? (
              <Button type="link" size="small" onClick={() => setRetrieval(null)}>
                恢复为知识库默认
              </Button>
            ) : null}
          </Space>
        </Form.Item>

        {override ? (
          <>
            <Form.Item>
              <Radio.Group
                value={override.mode}
                optionType="button"
                buttonStyle="solid"
                onChange={(event) =>
                  setRetrieval({ ...override, mode: event.target.value as RetrievalConfig["mode"] })
                }
              >
                <Radio.Button value="vector">向量</Radio.Button>
                <Radio.Button value="fulltext">全文</Radio.Button>
                <Radio.Button value="hybrid">混合</Radio.Button>
              </Radio.Group>
            </Form.Item>
            {override.mode === "hybrid" ? (
              <Form.Item label="权重">
                <Space>
                  <Typography.Text>向量</Typography.Text>
                  <InputNumber
                    min={0}
                    max={1}
                    step={0.1}
                    value={override.vector_weight}
                    onChange={(value) => setRetrieval({ ...override, vector_weight: value ?? 0 })}
                  />
                  <Typography.Text>/ 全文</Typography.Text>
                  <InputNumber
                    min={0}
                    max={1}
                    step={0.1}
                    value={override.fulltext_weight}
                    onChange={(value) => setRetrieval({ ...override, fulltext_weight: value ?? 0 })}
                  />
                </Space>
              </Form.Item>
            ) : null}
          </>
        ) : null}

        <Form.Item label="文件夹过滤">
          <Select
            mode="multiple"
            allowClear
            style={{ width: "100%" }}
            placeholder="全部文件夹"
            value={value.filter?.folder_ids ?? []}
            options={folders.map((folder) => ({ value: folder.id, label: folder.path }))}
            onChange={(folderIds) =>
              setFilter({
                folder_ids: folderIds,
                metadata: value.filter?.metadata ?? null,
              })
            }
          />
        </Form.Item>

        <Form.Item label="元数据过滤">
          <MetadataFilterEditor
            fields={metadataFields}
            group={value.filter?.metadata ?? null}
            onChange={(metadata) =>
              setFilter({
                folder_ids: value.filter?.folder_ids ?? [],
                metadata,
              })
            }
          />
        </Form.Item>
      </Form>
    </div>
  );
}

interface MetadataFilterEditorProps {
  fields: { id: number; name: string; type: string; options: string[] | null }[];
  group: MetadataGroup | null;
  onChange: (group: MetadataGroup | null) => void;
}

/** 元数据过滤：一层“且 / 或”多条件，不嵌套（与 P3-17 命中测试页一致）。 */
function MetadataFilterEditor({ fields, group, onChange }: MetadataFilterEditorProps) {
  const op = group?.op ?? "and";
  const conditions: MetadataCondition[] = (group?.conditions ?? []).map((condition) => ({
    field_id: condition.field_id,
    operator: condition.operator,
    value: typeof condition.value === "string" ? condition.value : String(condition.value ?? ""),
  }));

  const commit = (next: MetadataCondition[], nextOp = op) => onChange(buildMetadataGroup(next, nextOp));

  const update = (index: number, partial: Partial<MetadataCondition>) =>
    commit(conditions.map((condition, i) => (i === index ? { ...condition, ...partial } : condition)));

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {conditions.length > 1 ? (
        <Radio.Group size="small" value={op} onChange={(event) => commit(conditions, event.target.value)}>
          <Radio.Button value="and">且</Radio.Button>
          <Radio.Button value="or">或</Radio.Button>
        </Radio.Group>
      ) : null}

      {conditions.map((condition, index) => {
        const field = fields.find((item) => item.id === condition.field_id);
        return (
          <Space key={index} wrap>
            <Select
              placeholder="字段"
              style={{ width: 140 }}
              value={condition.field_id}
              options={fields.map((item) => ({ value: item.id, label: item.name }))}
              onChange={(value) => update(index, { field_id: value })}
            />
            <Select
              style={{ width: 110 }}
              value={condition.operator}
              options={operatorsForType(field?.type ?? "text").map((operator) => ({
                value: operator,
                label: OPERATOR_LABELS[operator],
              }))}
              onChange={(value) => update(index, { operator: value })}
            />
            {field?.type === "select" ? (
              <Select
                placeholder="值"
                style={{ width: 140 }}
                value={condition.value || undefined}
                options={(field.options ?? []).map((option) => ({ value: option, label: option }))}
                onChange={(value) => update(index, { value: value ?? "" })}
              />
            ) : (
              <Input
                placeholder="值"
                style={{ width: 140 }}
                value={condition.value}
                onChange={(event) => update(index, { value: event.target.value })}
              />
            )}
            <Button
              type="text"
              danger
              aria-label="删除条件"
              icon={<DeleteOutlined />}
              onClick={() => commit(conditions.filter((_condition, i) => i !== index))}
            />
          </Space>
        );
      })}

      <Button
        type="link"
        size="small"
        style={{ padding: 0 }}
        icon={<PlusOutlined />}
        disabled={fields.length === 0}
        onClick={() => {
          const first = fields[0];
          if (first) {
            commit([...conditions, { field_id: first.id, operator: "eq", value: "" }]);
          }
        }}
      >
        添加条件（且 / 或）
      </Button>
    </Space>
  );
}

interface DocRetrievalCardProps {
  config: AppConfigForm;
  candidateByKb: Map<number, KbCandidateOut>;
  rerankModels: AppModel[];
  onDocChange: (partial: Partial<AppConfigForm["knowledge"]["doc"]>) => void;
}

/** 文档库检索（原型 #v-app 第四张卡）。相邻分块扩展第五期，本期隐藏。 */
function DocRetrievalCard({ config, candidateByKb, rerankModels, onDocChange }: DocRetrievalCardProps) {
  const docKbs = config.knowledge.kbs.filter(
    (ref) => candidateByKb.get(ref.kb_id)?.kind !== "qa",
  );
  const embeddingModels = new Map<number, string>();
  docKbs.forEach((ref) => {
    const candidate = candidateByKb.get(ref.kb_id);
    if (candidate) {
      embeddingModels.set(candidate.embedding_model.id, candidate.embedding_model.name);
    }
  });
  const rerankRequired = embeddingModels.size > 1 && !config.knowledge.doc.rerank.enabled;

  return (
    <Card
      title={
        <Space>
          <span>文档库检索</span>
          <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
            作用于所有文档库合并后的结果
          </Typography.Text>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      {rerankRequired ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`所选文档库使用了不同的向量化模型（${[...embeddingModels.values()].join(
            "、",
          )}），分数不能直接比较，必须开启重排才能保存。`}
        />
      ) : null}

      <Form layout="vertical">
        <Space size="large" align="start" wrap>
          <Form.Item label="召回数量 Top K">
            <InputNumber
              min={1}
              max={100}
              value={config.knowledge.doc.top_k}
              onChange={(value) => onDocChange({ top_k: value ?? 20 })}
            />
          </Form.Item>
          <Form.Item label="分数阈值" extra="开启重排时作用于重排分数；默认值待实测">
            <InputNumber
              min={0}
              max={1}
              step={0.05}
              value={config.knowledge.doc.score_threshold ?? undefined}
              placeholder="待实测"
              onChange={(value) => onDocChange({ score_threshold: value ?? null })}
            />
          </Form.Item>
        </Space>

        <Form.Item label="重排">
          <Switch
            checked={config.knowledge.doc.rerank.enabled}
            onChange={(enabled) =>
              onDocChange({
                rerank: {
                  ...config.knowledge.doc.rerank,
                  enabled,
                  model_id: config.knowledge.doc.rerank.model_id ?? rerankModels[0]?.id ?? null,
                },
              })
            }
          />
        </Form.Item>
        {config.knowledge.doc.rerank.enabled ? (
          <Space size="large" align="start" wrap>
            <Form.Item label="重排模型">
              <Select
                style={{ width: 200 }}
                placeholder="选择重排模型"
                value={config.knowledge.doc.rerank.model_id ?? undefined}
                options={rerankModels.map((model) => ({ value: model.id, label: model.name }))}
                onChange={(value) =>
                  onDocChange({
                    rerank: { ...config.knowledge.doc.rerank, model_id: value },
                  })
                }
              />
            </Form.Item>
            <Form.Item label="保留条数">
              <InputNumber
                min={1}
                max={100}
                value={config.knowledge.doc.rerank.keep}
                onChange={(value) =>
                  onDocChange({ rerank: { ...config.knowledge.doc.rerank, keep: value ?? 5 } })
                }
              />
            </Form.Item>
          </Space>
        ) : null}
      </Form>
    </Card>
  );
}

interface ReplyCardProps {
  config: AppConfigForm;
  onReplyChange: (partial: Partial<AppConfigForm["reply"]>) => void;
}

/** 回答设置（原型 #v-app 最后一张卡）。追问建议第六期，本期不显示。 */
function ReplyCard({ config, onReplyChange }: ReplyCardProps) {
  const [questionInput, setQuestionInput] = useState("");
  const questions = config.reply.opening_questions ?? [];

  return (
    <Card title="回答设置" style={{ marginBottom: 16 }}>
      <Form layout="vertical">
        <Form.Item label="兜底话术" extra="问答库和文档库都没有超过阈值的结果时直接回复，不调用模型">
          <Input
            value={config.reply.fallback_text}
            onChange={(event) => onReplyChange({ fallback_text: event.target.value })}
          />
        </Form.Item>
        <Form.Item label="开场白">
          <Input.TextArea
            rows={2}
            value={config.reply.opening}
            onChange={(event) => onReplyChange({ opening: event.target.value })}
          />
        </Form.Item>
        <Form.Item label="开场推荐问题">
          <Space wrap>
            {questions.map((question) => (
              <Tag
                key={question}
                closable
                onClose={() =>
                  onReplyChange({
                    opening_questions: questions.filter((item) => item !== question),
                  })
                }
              >
                {question}
              </Tag>
            ))}
          </Space>
          <Space.Compact style={{ width: 320, marginTop: 8 }}>
            <Input
              value={questionInput}
              placeholder="添加推荐问题"
              onChange={(event) => setQuestionInput(event.target.value)}
            />
            <Button
              onClick={() => {
                const value = questionInput.trim();
                if (value === "" || questions.includes(value)) {
                  return;
                }
                onReplyChange({ opening_questions: [...questions, value] });
                setQuestionInput("");
              }}
            >
              添加
            </Button>
          </Space.Compact>
        </Form.Item>
        <Form.Item label="允许下载原文件" extra="关闭时终端用户只能预览被引用的页或段落">
          <Switch
            checked={config.reply.allow_download}
            onChange={(allow_download) => onReplyChange({ allow_download })}
          />
        </Form.Item>
      </Form>
    </Card>
  );
}

interface AddKbModalProps {
  open: boolean;
  candidates: KbCandidateOut[];
  selectedIds: number[];
  onClose: () => void;
  onAdd: (kbIds: number[]) => void;
}

/** 添加知识库弹窗（原型 #d-addkb）。本期只列文档库。 */
function AddKbModal({ open, candidates, selectedIds, onClose, onAdd }: AddKbModalProps) {
  const [checked, setChecked] = useState<number[]>([]);

  useEffect(() => {
    if (open) {
      setChecked([]);
    }
  }, [open]);

  const docs = candidates.filter((candidate) => candidate.kind !== "qa");

  return (
    <Modal
      open={open}
      title="添加知识库"
      okText={`添加 ${checked.length} 个`}
      okButtonProps={{ disabled: checked.length === 0 }}
      onCancel={onClose}
      onOk={() => {
        onAdd(checked);
        onClose();
      }}
    >
      <Typography.Paragraph type="secondary">
        只能选择你和应用创建人都能查看的知识库。已停用的知识库也可以添加，但检索时会跳过它。
      </Typography.Paragraph>
      <Table<KbCandidateOut>
        rowKey="kb_id"
        size="small"
        pagination={false}
        dataSource={docs}
        columns={[
          {
            title: "",
            key: "select",
            render: (_value, record) => {
              const added = record.added || selectedIds.includes(record.kb_id);
              return (
                <Checkbox
                  aria-label={record.name}
                  disabled={!record.selectable || added}
                  checked={checked.includes(record.kb_id)}
                  onChange={(event) =>
                    setChecked((current) =>
                      event.target.checked
                        ? [...current, record.kb_id]
                        : current.filter((id) => id !== record.kb_id),
                    )
                  }
                />
              );
            },
          },
          { title: "名称", dataIndex: "name" },
          {
            title: "类型",
            dataIndex: "kind",
            render: (kind: string) => (kind === "qa" ? "问答库" : "文档库"),
          },
          { title: "向量化模型", dataIndex: ["embedding_model", "name"] },
          { title: "所属组织", dataIndex: ["org", "name"] },
          {
            title: "",
            key: "state",
            render: (_value, record) =>
              record.added || selectedIds.includes(record.kb_id)
                ? "已添加"
                : record.selectable
                  ? ""
                  : (record.reason ?? "不能添加"),
          },
        ]}
      />
    </Modal>
  );
}

interface ChunkPreviewModalProps {
  appId: number;
  chunkId: number | null;
  onClose: () => void;
}

/** 调试里点击召回结果查看分块位置（接口 P4-11，联调待后端实现）。 */
function ChunkPreviewModal({ appId, chunkId, onClose }: ChunkPreviewModalProps) {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<CitationPreviewOut | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (chunkId === null) {
      setPreview(null);
      setPdfUrl(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPreview(null);
    setPdfUrl(null);
    const url = `/api/v1/apps/${appId}/debug/chunks/${chunkId}/preview`;
    fetchWithRefresh(new Request(url, { headers: { Accept: "application/json" } }))
      .then(async (response) => {
        if (cancelled) {
          return;
        }
        if (!response.ok) {
          setError(getErrorMessage(ApiError.from(response.status, await readErrorBody(response))));
          return;
        }
        const contentType = response.headers.get("content-type") ?? "";
        if (contentType.includes("application/json")) {
          setPreview((await response.json()) as CitationPreviewOut);
        } else {
          setPdfUrl(url);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("分块预览加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [appId, chunkId]);

  const elements = (preview?.elements ?? []).map((element) => ({
    ref: element.ref,
    text: element.text,
    type: element.type,
    bbox: null,
    page_no: null,
  }));
  const highlightRefs = (preview?.elements ?? [])
    .filter((element) => element.highlight)
    .map((element) => element.ref);

  return (
    <Modal open={chunkId !== null} title="分块位置" footer={null} width={720} onCancel={onClose}>
      {loading ? <Spin /> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {pdfUrl ? (
        <iframe title="分块 PDF 预览" src={pdfUrl} style={{ width: "100%", height: 520 }} />
      ) : null}
      {preview ? (
        <>
          <Typography.Paragraph type="secondary">
            {preview.doc_name}（{preview.kb_name}）
            {preview.stale ? " · 原文已更新，只显示快照片段" : ""}
            {preview.edited ? " · 人工修改内容" : ""}
          </Typography.Paragraph>
          {preview.content ? (
            <ParseResultView
              result={{
                markdown: preview.content,
                elements,
                edited_at: null,
                edited_by: null,
                engine: "builtin",
                engine_version: null,
                kind: "original",
                ocr_pages: null,
                pages: null,
              }}
              highlightRefs={highlightRefs}
            />
          ) : null}
        </>
      ) : null}
    </Modal>
  );
}
