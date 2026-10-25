import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";

import type { MetadataField } from "../api";
import type { FolderOut } from "../documents/api";
import {
  OPERATOR_LABELS,
  hybridWeightError,
  operatorsForType,
  type MetadataCondition,
  type Model,
  type RetrievalConfigInput,
  type RetrievalMode,
} from "./api";

export interface HitTestFilters {
  folderId: number | "all";
  metadataOp: "and" | "or";
  conditions: MetadataCondition[];
}

interface RetrievalConfigFormProps {
  config: RetrievalConfigInput;
  onConfigChange: (config: RetrievalConfigInput) => void;
  filters: HitTestFilters;
  onFiltersChange: (filters: HitTestFilters) => void;
  folders: FolderOut[];
  metadataFields: MetadataField[];
  rerankModels: Model[];
  editable: boolean;
  saving: boolean;
  onReset: () => void;
  onSaveDefault: () => void;
}

/**
 * 检索配置表单（原型 #v-kbhit 左栏）。
 * 相邻分块扩展属于第五期，第三期不渲染该控件，提交对象里也不出现 `adjacent_n`。
 */
export function RetrievalConfigForm({
  config,
  onConfigChange,
  filters,
  onFiltersChange,
  folders,
  metadataFields,
  rerankModels,
  editable,
  saving,
  onReset,
  onSaveDefault,
}: RetrievalConfigFormProps) {
  const rerank = config.rerank ?? { enabled: false, model_id: null, top_n: 10 };
  const weightError = hybridWeightError(config);

  const setConditions = (conditions: MetadataCondition[]) =>
    onFiltersChange({ ...filters, conditions });

  const addCondition = () => {
    const first = metadataFields[0];
    if (!first) {
      return;
    }
    setConditions([
      ...filters.conditions,
      { field_id: first.id, operator: "eq", value: "" },
    ]);
  };

  const updateCondition = (index: number, patch: Partial<MetadataCondition>) => {
    setConditions(
      filters.conditions.map((condition, i) =>
        i === index ? { ...condition, ...patch } : condition,
      ),
    );
  };

  const removeCondition = (index: number) => {
    setConditions(filters.conditions.filter((_condition, i) => i !== index));
  };

  return (
    <Card
      title={
        <Space>
          <span>检索配置</span>
          <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
            知识库默认值，应用可覆盖
          </Typography.Text>
        </Space>
      }
      style={{ width: 400, flexShrink: 0 }}
    >
      <Form layout="vertical" size="small">
        <Form.Item label="检索方式">
          <Radio.Group
            value={config.mode}
            optionType="button"
            buttonStyle="solid"
            onChange={(event) =>
              onConfigChange({ ...config, mode: event.target.value as RetrievalMode })
            }
          >
            <Radio.Button value="vector">向量</Radio.Button>
            <Radio.Button value="fulltext">全文</Radio.Button>
            <Radio.Button value="hybrid">混合</Radio.Button>
          </Radio.Group>
        </Form.Item>

        {config.mode === "hybrid" ? (
          <Form.Item
            label="权重"
            validateStatus={weightError ? "error" : undefined}
            help={weightError ?? undefined}
          >
            <Space>
              <Typography.Text>向量</Typography.Text>
              <InputNumber
                min={0}
                max={1}
                step={0.1}
                value={config.vector_weight}
                onChange={(value) => onConfigChange({ ...config, vector_weight: value ?? 0 })}
              />
              <Typography.Text>/ 全文</Typography.Text>
              <InputNumber
                min={0}
                max={1}
                step={0.1}
                value={config.fulltext_weight}
                onChange={(value) => onConfigChange({ ...config, fulltext_weight: value ?? 0 })}
              />
            </Space>
          </Form.Item>
        ) : null}

        <Space size="middle" align="start">
          <Form.Item label="召回数量 Top K">
            <InputNumber
              min={1}
              max={100}
              value={config.top_k}
              onChange={(value) => onConfigChange({ ...config, top_k: value ?? 1 })}
            />
          </Form.Item>
          <Form.Item label="分数阈值">
            <InputNumber
              min={0}
              max={1}
              step={0.05}
              value={config.score_threshold}
              onChange={(value) => onConfigChange({ ...config, score_threshold: value ?? 0 })}
            />
          </Form.Item>
        </Space>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: -8 }}>
          开启重排时阈值作用于重排分数，未开启时作用于融合后的分数
        </Typography.Paragraph>

        <Form.Item label="重排">
          <Switch
            checked={rerank.enabled}
            onChange={(enabled) =>
              onConfigChange({
                ...config,
                rerank: {
                  ...rerank,
                  enabled,
                  model_id: rerank.model_id ?? rerankModels[0]?.id ?? null,
                },
              })
            }
          />
        </Form.Item>

        {rerank.enabled ? (
          <Space size="middle" align="start">
            <Form.Item label="重排模型">
              <Select
                style={{ width: 160 }}
                placeholder="选择重排模型"
                value={rerank.model_id ?? undefined}
                options={rerankModels.map((model) => ({ value: model.id, label: model.name }))}
                onChange={(value) =>
                  onConfigChange({ ...config, rerank: { ...rerank, model_id: value } })
                }
              />
            </Form.Item>
            <Form.Item label="保留条数">
              <InputNumber
                min={1}
                max={100}
                value={rerank.top_n}
                onChange={(value) =>
                  onConfigChange({ ...config, rerank: { ...rerank, top_n: value ?? 1 } })
                }
              />
            </Form.Item>
          </Space>
        ) : null}

        <Form.Item label="文件夹过滤">
          <Select
            style={{ width: "100%" }}
            value={filters.folderId}
            options={[
              { value: "all", label: "全部文件夹" },
              ...folders.map((folder) => ({
                value: folder.id,
                label: `${folder.path}（含下级）`,
              })),
            ]}
            onChange={(value) => onFiltersChange({ ...filters, folderId: value })}
          />
        </Form.Item>

        <Form.Item label="元数据过滤">
          <Space direction="vertical" style={{ width: "100%" }}>
            {filters.conditions.length > 1 ? (
              <Radio.Group
                size="small"
                value={filters.metadataOp}
                onChange={(event) =>
                  onFiltersChange({ ...filters, metadataOp: event.target.value as "and" | "or" })
                }
              >
                <Radio.Button value="and">且</Radio.Button>
                <Radio.Button value="or">或</Radio.Button>
              </Radio.Group>
            ) : null}

            {filters.conditions.map((condition, index) => {
              const field = metadataFields.find((item) => item.id === condition.field_id);
              return (
                <Space key={index} wrap>
                  <Select
                    placeholder="字段"
                    style={{ width: 120 }}
                    value={condition.field_id}
                    options={metadataFields.map((item) => ({ value: item.id, label: item.name }))}
                    onChange={(value) => updateCondition(index, { field_id: value })}
                  />
                  <Select
                    style={{ width: 100 }}
                    value={condition.operator}
                    options={operatorsForType(field?.type ?? "text").map((operator) => ({
                      value: operator,
                      label: OPERATOR_LABELS[operator],
                    }))}
                    onChange={(value) => updateCondition(index, { operator: value })}
                  />
                  {field?.type === "select" ? (
                    <Select
                      placeholder="值"
                      style={{ width: 140 }}
                      value={condition.value || undefined}
                      options={(field.options ?? []).map((option) => ({
                        value: option,
                        label: option,
                      }))}
                      onChange={(value) => updateCondition(index, { value: value ?? "" })}
                    />
                  ) : (
                    <Input
                      placeholder="值"
                      style={{ width: 140 }}
                      value={condition.value}
                      onChange={(event) => updateCondition(index, { value: event.target.value })}
                    />
                  )}
                  <Button
                    type="text"
                    danger
                    aria-label="删除条件"
                    icon={<DeleteOutlined />}
                    onClick={() => removeCondition(index)}
                  />
                </Space>
              );
            })}

            <Button
              type="link"
              size="small"
              style={{ padding: 0 }}
              icon={<PlusOutlined />}
              disabled={metadataFields.length === 0}
              onClick={addCondition}
            >
              添加条件（且 / 或）
            </Button>
          </Space>
        </Form.Item>

        <Space>
          <Button onClick={onReset}>恢复默认</Button>
          <Button
            type="primary"
            disabled={!editable || weightError !== null}
            loading={saving}
            onClick={onSaveDefault}
          >
            保存为默认配置
          </Button>
        </Space>
      </Form>
    </Card>
  );
}
