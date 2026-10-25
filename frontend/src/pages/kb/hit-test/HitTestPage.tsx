import { App, Button, Card, Input, Space, Steps, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { getErrorMessage } from "../../../api/errors";
import { useKb, useMetadataFields } from "../api";
import { useFolders } from "../documents/api";
import { KbHeader } from "../KbHeader";
import { HitResultList } from "./HitResultList";
import { RetrievalConfigForm, type HitTestFilters } from "./RetrievalConfigForm";
import {
  STEP_KEYS,
  buildHitTestPayload,
  buildMetadataGroup,
  currentStepText,
  defaultRetrievalConfig,
  formatTimings,
  hybridWeightError,
  stepItems,
  stepResults,
  useHitTest,
  useRerankModels,
  useRetrievalConfig,
  useUpdateRetrievalConfig,
  type HitTestOut,
  type RetrievalConfigInput,
  type StepKey,
} from "./api";

const EMPTY_FILTERS: HitTestFilters = {
  folderId: "all",
  metadataOp: "and",
  conditions: [],
};

/** 知识库命中测试页（原型 #v-kbhit）。记录不保存；切换知识库或离开页面清空结果。 */
export default function HitTestPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const kbId = Number.isFinite(parsedId) ? parsedId : null;

  const { message } = App.useApp();
  const { data: kb, isLoading } = useKb(kbId);
  const { data: defaultConfigData } = useRetrievalConfig(kbId);
  const { data: folders = [] } = useFolders(kbId);
  const { data: metadataFields = [] } = useMetadataFields(kbId);
  const { data: rerankModelsPage } = useRerankModels();
  const updateConfig = useUpdateRetrievalConfig();
  const hitTest = useHitTest(kbId);

  const [config, setConfig] = useState<RetrievalConfigInput | null>(null);
  const [filters, setFilters] = useState<HitTestFilters>(EMPTY_FILTERS);
  const [question, setQuestion] = useState("");
  const [activeStep, setActiveStep] = useState<StepKey>("fusion");
  const [result, setResult] = useState<HitTestOut | null>(null);

  const defaultConfig = useMemo(
    () => (defaultConfigData ? defaultRetrievalConfig(defaultConfigData) : null),
    [defaultConfigData],
  );

  useEffect(() => {
    if (defaultConfig) {
      setConfig(defaultConfig);
    }
  }, [defaultConfig]);

  // 切换知识库清空结果与临时配置（离开页面由组件卸载清空）。
  useEffect(() => {
    setResult(null);
    setActiveStep("fusion");
    setQuestion("");
    setFilters(EMPTY_FILTERS);
  }, [kbId]);

  if (isLoading || !kb || !config) {
    return null;
  }

  const rerankModels = rerankModelsPage?.items ?? [];
  const weightError = hybridWeightError(config);
  const steps = result ? stepItems(result.steps) : [];
  const displayedItems = result ? stepResults(result.steps, activeStep) : [];

  const handleTest = () => {
    if (question.trim() === "") {
      message.warning("请输入问题");
      return;
    }
    if (weightError) {
      message.warning(weightError);
      return;
    }
    const metadata = buildMetadataGroup(filters.conditions, filters.metadataOp);
    hitTest.mutate(buildHitTestPayload(question.trim(), config, filters.folderId, metadata), {
      onSuccess: (data) => {
        setResult(data);
        setActiveStep(config.rerank?.enabled ? "rerank" : "fusion");
      },
      onError: (error) => message.error(getErrorMessage(error)),
    });
  };

  const handleReset = () => {
    if (defaultConfig) {
      setConfig(defaultConfig);
    }
    setFilters(EMPTY_FILTERS);
  };

  const handleSaveDefault = () => {
    if (weightError) {
      return;
    }
    updateConfig.mutate(
      { id: kb.id, config },
      {
        onSuccess: () => message.success("已保存为默认配置"),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  return (
    <div>
      <KbHeader kb={kb} active="hit-test" />
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <RetrievalConfigForm
          config={config}
          onConfigChange={setConfig}
          filters={filters}
          onFiltersChange={setFilters}
          folders={folders}
          metadataFields={metadataFields}
          rerankModels={rerankModels}
          editable={kb.editable}
          saving={updateConfig.isPending}
          onReset={handleReset}
          onSaveDefault={handleSaveDefault}
        />

        <Card
          title={
            <Space>
              <span>命中测试</span>
              <Typography.Text type="secondary" style={{ fontWeight: "normal" }}>
                记录不保存
              </Typography.Text>
            </Space>
          }
          style={{ flex: 1, minWidth: 0 }}
        >
          <Space.Compact style={{ width: "100%", marginBottom: 16 }}>
            <Input
              value={question}
              placeholder="输入问题，例如：去上海出差住酒店最多报多少钱？"
              onChange={(event) => setQuestion(event.target.value)}
              onPressEnter={handleTest}
            />
            <Button type="primary" loading={hitTest.isPending} onClick={handleTest}>
              测试
            </Button>
          </Space.Compact>

          {result ? (
            <>
              <Steps
                size="small"
                current={STEP_KEYS.indexOf(activeStep)}
                onChange={(value) => setActiveStep(STEP_KEYS[value] ?? "fusion")}
                items={steps.map((step) => ({ title: `${step.label} ${step.count}` }))}
                style={{ marginBottom: 12 }}
              />
              <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
                当前显示：{currentStepText(activeStep, displayedItems.length)}（耗时：
                {formatTimings(result.timings)}）。点击上方步骤可查看每一步的结果和得分。
              </Typography.Paragraph>
              <HitResultList kbId={kb.id} items={displayedItems} emptyText="该步骤没有结果" />
            </>
          ) : (
            <Typography.Text type="secondary">
              输入问题后点“测试”，查看向量召回、全文召回、融合与重排的结果和得分。
            </Typography.Text>
          )}
        </Card>
      </div>
    </div>
  );
}
