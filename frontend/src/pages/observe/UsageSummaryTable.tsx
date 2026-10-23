import { Card, Segmented, Table, Typography, type TableProps } from "antd";

import { formatCount, formatShare, formatToken, sharePercent } from "../../lib/numberFormat";
import type { UsageGroupBy, UsageItem, UsageSortBy, UsageSummary } from "./api";

const GROUP_BY_LABELS: Record<UsageGroupBy, string> = {
  model: "模型",
  org: "组织",
  app: "应用",
  user: "用户",
  source: "来源",
};

const GROUP_BY_OPTIONS = (Object.keys(GROUP_BY_LABELS) as UsageGroupBy[]).map(
  (value) => ({ value, label: GROUP_BY_LABELS[value] }),
);

interface UsageSummaryTableProps {
  groupBy: UsageGroupBy;
  onGroupByChange: (value: UsageGroupBy) => void;
  data: UsageSummary | undefined;
  loading: boolean;
  sortBy: UsageSortBy;
  order: "asc" | "desc";
  onSortChange: (sortBy: UsageSortBy, order: "asc" | "desc") => void;
  page: number;
  pageSize: number;
  onPageChange: (page: number, pageSize: number) => void;
}

/** 汇总表：维度切换 + 服务端排序 + 分页（原型 #v-usage 的第二个 card）。 */
export function UsageSummaryTable({
  groupBy,
  onGroupByChange,
  data,
  loading,
  sortBy,
  order,
  onSortChange,
  page,
  pageSize,
  onPageChange,
}: UsageSummaryTableProps) {
  const totals = data?.totals ?? { calls: 0, failures: 0, input_tokens: 0, output_tokens: 0 };

  const sortOrder = (key: UsageSortBy) =>
    sortBy === key ? (order === "desc" ? "descend" : "ascend") : null;

  const columns: TableProps<UsageItem>["columns"] = [
    {
      title: GROUP_BY_LABELS[groupBy],
      dataIndex: "label",
      render: (_value, record) => (
        <>
          <div>{record.label}</div>
          {record.sub_label ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {record.sub_label}
            </Typography.Text>
          ) : null}
        </>
      ),
    },
    {
      title: "调用次数",
      dataIndex: "calls",
      align: "right",
      width: 120,
      sorter: true,
      sortOrder: sortOrder("calls"),
      render: (value: number) => formatCount(value),
    },
    {
      title: "占比",
      key: "share",
      width: 180,
      render: (_value, record) => (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              flex: 1,
              height: 6,
              background: "#f0f0f0",
              borderRadius: 3,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${sharePercent(record.calls, totals.calls)}%`,
                height: "100%",
                background: "#1677ff",
              }}
            />
          </div>
          <span style={{ whiteSpace: "nowrap" }}>
            {formatShare(record.calls, totals.calls)}
          </span>
        </div>
      ),
    },
    {
      title: "失败",
      dataIndex: "failures",
      align: "right",
      width: 100,
      sorter: true,
      sortOrder: sortOrder("failures"),
      render: (value: number) => formatCount(value),
    },
    {
      title: "输入 Token",
      dataIndex: "input_tokens",
      align: "right",
      width: 130,
      sorter: true,
      sortOrder: sortOrder("input_tokens"),
      render: (value: number) => formatToken(value),
    },
    {
      title: "输出 Token",
      dataIndex: "output_tokens",
      align: "right",
      width: 130,
      sorter: true,
      sortOrder: sortOrder("output_tokens"),
      render: (value: number) => formatToken(value),
    },
  ];

  return (
    <Card
      style={{ marginTop: 16 }}
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span>汇总</span>
          <Segmented
            value={groupBy}
            onChange={(value) => onGroupByChange(value as UsageGroupBy)}
            options={GROUP_BY_OPTIONS}
          />
          {groupBy === "app" ? (
            <Typography.Text type="secondary" style={{ fontWeight: 400, fontSize: 14 }}>
              按应用汇总时只统计来源为“应用”“应用调试”的调用
            </Typography.Text>
          ) : null}
        </div>
      }
    >
      <Table<UsageItem>
        rowKey={(record) => `${record.key ?? "null"}:${record.label}`}
        columns={columns}
        dataSource={data?.items ?? []}
        loading={loading}
        onChange={(_pagination, _filters, sorter) => {
          const current = Array.isArray(sorter) ? sorter[0] : sorter;
          if (!current || !current.order) {
            return;
          }
          onSortChange(
            current.field as UsageSortBy,
            current.order === "ascend" ? "asc" : "desc",
          );
        }}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (count) =>
            `共 ${count} 行 · 合计 ${formatCount(totals.calls)} 次`,
          onChange: onPageChange,
        }}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        点击表头可排序
      </Typography.Text>
    </Card>
  );
}
