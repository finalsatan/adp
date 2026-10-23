import { Alert, Card, Col, DatePicker, Row, Select, TreeSelect, Typography } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useMemo, useState, type ReactNode } from "react";

import { useMe } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import { PageHeader } from "../../components/PageHeader";
import { formatCount, formatShare, formatToken } from "../../lib/numberFormat";
import { buildOrgTree, toOrgSelectData } from "../../lib/orgTree";
import {
  useUsageDaily,
  useUsageMeta,
  useUsageModels,
  useUsageSummary,
  type UsageFilters,
  type UsageGroupBy,
  type UsageSortBy,
  type UsageSource,
} from "./api";
import { UsageSummaryTable } from "./UsageSummaryTable";
import { UsageTrend } from "./UsageTrend";

const DEFAULT_DAYS = 30;

function defaultRange(): [Dayjs, Dayjs] {
  return [dayjs().subtract(DEFAULT_DAYS - 1, "day").startOf("day"), dayjs().startOf("day")];
}

function Kpi({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Col span={6}>
      <Card size="small">
        <Typography.Text type="secondary">{label}</Typography.Text>
        <div style={{ fontSize: 28, fontWeight: 600, lineHeight: 1.4 }}>{children}</div>
      </Card>
    </Col>
  );
}

/** 用量统计页（原型 #v-usage）。 */
export default function UsagePage() {
  const { data: me } = useMe();
  const { data: meta } = useUsageMeta();
  const { data: modelsPage } = useUsageModels();
  const { data: orgs = [] } = useOrgs();

  const [range, setRange] = useState<[Dayjs, Dayjs]>(defaultRange);
  const [modelId, setModelId] = useState<number | undefined>(undefined);
  const [orgId, setOrgId] = useState<number | undefined>(undefined);
  const [source, setSource] = useState<UsageSource | undefined>(undefined);
  const [groupBy, setGroupBy] = useState<UsageGroupBy>("org");
  const [sortBy, setSortBy] = useState<UsageSortBy>("calls");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const filters = useMemo<UsageFilters>(
    () => ({
      from: range[0].startOf("day").toISOString(),
      to: range[1].add(1, "day").startOf("day").toISOString(),
      ...(modelId != null ? { model_id: modelId } : {}),
      ...(orgId != null ? { org_id: orgId } : {}),
      ...(source ? { source } : {}),
    }),
    [range, modelId, orgId, source],
  );

  const summaryParams = useMemo(
    () => ({ ...filters, group_by: groupBy, sort_by: sortBy, order, page, page_size: pageSize }),
    [filters, groupBy, sortBy, order, page, pageSize],
  );

  const { data: summary, isLoading: summaryLoading } = useUsageSummary(summaryParams);
  const { data: daily, isLoading: dailyLoading } = useUsageDaily(filters);

  const orgTreeData = useMemo(() => toOrgSelectData(buildOrgTree(orgs)), [orgs]);
  const totals = summary?.totals ?? {
    calls: 0,
    failures: 0,
    input_tokens: 0,
    output_tokens: 0,
  };

  const isSysAdmin = me?.role === "sys_admin";
  const identity = isSysAdmin
    ? "当前身份：系统管理员，可查看全部记录。"
    : "当前身份：组织管理员，只看到本组织及下级组织发起的调用。";

  /** 改筛选项时回到第 1 页，汇总和趋势都会重查。 */
  const changeFilter = (apply: () => void) => {
    apply();
    setPage(1);
  };

  return (
    <>
      <PageHeader
        title="用量统计"
        description="每一次模型调用（对话、向量化、重排）的次数和 Token 用量，只统计、不计费。"
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`按发起调用的人在调用时所属的组织统计：终端用户提问算提问人的组织，入库算上传或发起重新解析的人。${identity}`}
      />

      <Card>
        <div
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
            marginBottom: 16,
          }}
        >
          <DatePicker.RangePicker
            value={range}
            style={{ width: 240 }}
            placeholder={["开始日期", "结束日期"]}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                changeFilter(() => setRange([dates[0] as Dayjs, dates[1] as Dayjs]));
              }
            }}
          />
          <Select
            allowClear
            value={modelId}
            placeholder="全部模型"
            style={{ width: 180 }}
            options={(modelsPage?.items ?? []).map((model) => ({
              value: model.id,
              label: model.name,
            }))}
            onChange={(value) => changeFilter(() => setModelId(value))}
          />
          <TreeSelect
            allowClear
            value={orgId}
            placeholder="全部组织"
            style={{ width: 180 }}
            treeData={orgTreeData}
            treeDefaultExpandAll
            onChange={(value) => changeFilter(() => setOrgId(value ?? undefined))}
          />
          <Select
            value="all"
            disabled
            style={{ width: 160 }}
            options={[{ value: "all", label: "全部应用" }]}
          />
          <Select
            allowClear
            value={source}
            placeholder="全部来源"
            style={{ width: 160 }}
            options={(meta?.sources ?? []).map((item) => ({
              value: item.code,
              label: item.label,
            }))}
            onChange={(value) => changeFilter(() => setSource(value))}
          />
        </div>

        <Row gutter={16}>
          <Kpi label="调用次数">{formatCount(totals.calls)}</Kpi>
          <Kpi label="失败">
            {formatCount(totals.failures)}
            <Typography.Text
              type="secondary"
              style={{ fontSize: 14, fontWeight: 400, marginLeft: 8 }}
            >
              {formatShare(totals.failures, totals.calls)}
            </Typography.Text>
          </Kpi>
          <Kpi label="输入 Token">{formatToken(totals.input_tokens)}</Kpi>
          <Kpi label="输出 Token">{formatToken(totals.output_tokens)}</Kpi>
        </Row>
      </Card>

      <UsageTrend items={daily?.items ?? []} loading={dailyLoading} />

      <UsageSummaryTable
        groupBy={groupBy}
        onGroupByChange={(value) => changeFilter(() => setGroupBy(value))}
        data={summary}
        loading={summaryLoading}
        sortBy={sortBy}
        order={order}
        onSortChange={(nextSortBy, nextOrder) =>
          changeFilter(() => {
            setSortBy(nextSortBy);
            setOrder(nextOrder);
          })
        }
        page={page}
        pageSize={pageSize}
        onPageChange={(nextPage, nextPageSize) => {
          if (nextPageSize !== pageSize) {
            setPageSize(nextPageSize);
            setPage(1);
            return;
          }
          setPage(nextPage);
        }}
      />
    </>
  );
}
