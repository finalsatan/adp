import { SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  Card,
  DatePicker,
  Input,
  Select,
  Table,
  Tag,
  TreeSelect,
  Typography,
  type TableProps,
} from "antd";
import type { Dayjs } from "dayjs";
import { useMemo, useState } from "react";

import { useOrgs } from "../../../api/orgs";
import { PageHeader } from "../../../components/PageHeader";
import { buildOrgTree, toOrgSelectData } from "../../../lib/orgTree";
import {
  useAuditLogs,
  useAuditMeta,
  type AuditListParams,
  type AuditLog,
  type AuditResult,
} from "./api";
import { formatActorOrg, formatAuditTime, formatTarget } from "./auditFormat";

const ALL = "all";

const RESULT_OPTIONS: { value: AuditResult | typeof ALL; label: string }[] = [
  { value: ALL, label: "全部结果" },
  { value: "success", label: "成功" },
  { value: "failure", label: "失败" },
];

/** 审计日志页（原型 #v-audit）。 */
export default function AuditPage() {
  const { data: meta } = useAuditMeta();
  const { data: orgs = [] } = useOrgs();

  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState<string>(ALL);
  const [targetType, setTargetType] = useState<string>(ALL);
  const [orgId, setOrgId] = useState<number | undefined>(undefined);
  const [result, setResult] = useState<AuditResult | typeof ALL>(ALL);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [expandedKeys, setExpandedKeys] = useState<number[]>([]);

  const params = useMemo<AuditListParams>(() => {
    const value: AuditListParams = { page, page_size: pageSize };
    if (range) {
      const [from, to] = range;
      value.from = from.toISOString();
      value.to = to.toISOString();
    }
    if (actor) {
      value.actor = actor;
    }
    if (action !== ALL) {
      value.action = action;
    }
    if (targetType !== ALL) {
      value.target_type = targetType;
    }
    if (orgId != null) {
      value.org_id = orgId;
    }
    if (result !== ALL) {
      value.result = result;
    }
    return value;
  }, [range, actor, action, targetType, orgId, result, page, pageSize]);

  const { data, isLoading } = useAuditLogs(params);

  const orgTreeData = useMemo(
    () => toOrgSelectData(buildOrgTree(orgs)),
    [orgs],
  );

  const toggleExpand = (id: number) => {
    setExpandedKeys((keys) =>
      keys.includes(id) ? keys.filter((key) => key !== id) : [...keys, id],
    );
  };

  const columns: TableProps<AuditLog>["columns"] = [
    {
      title: "时间",
      dataIndex: "at",
      width: 130,
      render: (at: string) => formatAuditTime(at),
    },
    {
      title: "操作人",
      dataIndex: "actor_name",
      render: (name: string | null) => (
        <Typography.Text strong>{name ?? "-"}</Typography.Text>
      ),
    },
    {
      title: "操作人组织",
      dataIndex: "actor_org_name",
      render: (name: string | null) => formatActorOrg(name),
    },
    { title: "动作", dataIndex: "action_label" },
    {
      title: "对象",
      render: (_value, record) => formatTarget(record),
    },
    {
      title: "对象所属组织",
      dataIndex: "target_org_name",
      render: (name: string | null) => name ?? "—",
    },
    {
      title: "结果",
      dataIndex: "result",
      render: (value: AuditResult) =>
        value === "success" ? (
          <Tag color="green">成功</Tag>
        ) : (
          <Tag color="red">失败</Tag>
        ),
    },
    {
      title: "来源 IP",
      dataIndex: "ip",
      render: (ip: string | null) => ip ?? "-",
    },
  ];

  return (
    <>
      <PageHeader
        title="审计日志"
        description="记录登录、用户与组织变更、可见范围与协作者变更、资源发布与删除、密钥操作、系统设置修改。"
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="日志只增不改，永久保存。系统管理员可查看全部；组织管理员可查看操作人或对象属于本组织及下级组织的记录，其他组织的协作者改了本组织的资源也能看到。"
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
            showTime
            value={range}
            style={{ width: 340 }}
            placeholder={["开始时间", "结束时间"]}
            onChange={(dates) => {
              if (!dates || !dates[0] || !dates[1]) {
                setRange(null);
              } else {
                setRange([dates[0], dates[1]]);
              }
              setPage(1);
            }}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="操作人"
            style={{ width: 160 }}
            onChange={(event) => {
              if (!event.target.value) {
                setActor("");
                setPage(1);
              }
            }}
            onPressEnter={(event) => {
              setActor((event.target as HTMLInputElement).value);
              setPage(1);
            }}
          />
          <Select
            value={action}
            style={{ width: 150 }}
            options={[
              { value: ALL, label: "全部动作" },
              ...(meta?.actions ?? []).map((item) => ({
                value: item.code,
                label: item.label,
              })),
            ]}
            onChange={(value) => {
              setAction(value);
              setPage(1);
            }}
          />
          <Select
            value={targetType}
            style={{ width: 150 }}
            options={[
              { value: ALL, label: "全部对象" },
              ...(meta?.target_types ?? []).map((item) => ({
                value: item.code,
                label: item.label,
              })),
            ]}
            onChange={(value) => {
              setTargetType(value);
              setPage(1);
            }}
          />
          <TreeSelect
            allowClear
            value={orgId}
            placeholder="全部组织"
            style={{ width: 180 }}
            treeData={orgTreeData}
            treeDefaultExpandAll
            onChange={(value) => {
              setOrgId(value ?? undefined);
              setPage(1);
            }}
          />
          <Select
            value={result}
            style={{ width: 130 }}
            options={RESULT_OPTIONS}
            onChange={(value) => {
              setResult(value);
              setPage(1);
            }}
          />
        </div>

        <Table<AuditLog>
          rowKey="id"
          columns={columns}
          dataSource={data?.items ?? []}
          loading={isLoading}
          onRow={(record) => ({ onClick: () => toggleExpand(record.id) })}
          expandable={{
            expandedRowKeys: expandedKeys,
            onExpandedRowsChange: (keys) => setExpandedKeys(keys as number[]),
            rowExpandable: (record) => record.detail != null,
            expandedRowRender: (record) => (
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {JSON.stringify(record.detail, null, 2)}
              </pre>
            ),
          }}
          pagination={{
            current: page,
            pageSize,
            total: data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (count) => `共 ${count} 条`,
            onChange: (nextPage, nextPageSize) => {
              if (nextPageSize !== pageSize) {
                setPageSize(nextPageSize);
                setPage(1);
                return;
              }
              setPage(nextPage);
            },
          }}
        />
      </Card>
    </>
  );
}
