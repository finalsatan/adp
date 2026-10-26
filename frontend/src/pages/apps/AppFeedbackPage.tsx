import {
  Alert,
  Button,
  Card,
  Checkbox,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  type TableColumnsType,
} from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { AppHeader } from "./AppHeader";
import {
  FEEDBACK_RATING_OPTIONS,
  FEEDBACK_STATUS_OPTIONS,
  LOG_RANGE_OPTIONS,
  buildFeedbackListParams,
  feedbackReasonLabel,
  isForbidden,
  logConversationPath,
  useApp,
  useAppFeedback,
  type FeedbackListItemOut,
  type FeedbackRatingValue,
  type LogRange,
} from "./api";

/** 用户反馈页（原型 #v-appfb）。“加入问答库”属第六期，本页不显示。 */
export default function AppFeedbackPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const appId = Number.isFinite(parsedId) ? parsedId : null;
  const navigate = useNavigate();

  const [rating, setRating] = useState<"all" | FeedbackRatingValue>("all");
  const [hasCorrection, setHasCorrection] = useState(false);
  const [status, setStatus] = useState<"all" | "unhandled">("all");
  const [range, setRange] = useState<LogRange>("30d");
  const [page, setPage] = useState(1);

  const appQuery = useApp(appId);
  const query = useAppFeedback(
    appId,
    buildFeedbackListParams({ rating, hasCorrection, status, range }, page, 10),
  );

  if (isForbidden(appQuery.error) || isForbidden(query.error)) {
    return (
      <Alert
        type="error"
        showIcon
        data-testid="feedback-forbidden"
        message="没有权限查看用户反馈"
        description="用户反馈包含终端用户的提问，只有应用的“编辑”权限可以查看。"
      />
    );
  }

  const app = appQuery.data;
  if (appQuery.isLoading || !app) {
    return null;
  }

  const summary = query.data?.summary;
  const items = query.data?.items ?? [];

  const columns: TableColumnsType<FeedbackListItemOut> = [
    {
      title: "时间",
      dataIndex: "created_at",
      width: 130,
      render: (value: string) => dayjs(value).format("MM-DD HH:mm"),
    },
    {
      title: "用户",
      key: "user",
      width: 140,
      render: (_value, record) => (
        <Space direction="vertical" size={0}>
          <span>{record.user.name}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.user.username}
          </Typography.Text>
        </Space>
      ),
    },
    { title: "问题", dataIndex: "question", ellipsis: true },
    {
      title: "反馈",
      dataIndex: "rating",
      width: 80,
      render: (value: FeedbackRatingValue) => (
        <Tag color={value === "up" ? "green" : "red"}>{value === "up" ? "点赞" : "点踩"}</Tag>
      ),
    },
    {
      title: "原因",
      key: "reasons",
      width: 160,
      render: (_value, record) =>
        record.reasons.length > 0 ? record.reasons.map(feedbackReasonLabel).join("、") : "—",
    },
    {
      title: "纠错内容",
      dataIndex: "correction",
      render: (value: string | null) => value ?? "—",
    },
    {
      title: "状态",
      key: "status",
      width: 100,
      render: () => "未处理",
    },
    {
      title: "操作",
      key: "action",
      width: 100,
      render: (_value, record) => (
        <Button
          type="link"
          style={{ padding: 0 }}
          onClick={() => navigate(logConversationPath(app.id, record.conversation_id))}
        >
          对话日志
        </Button>
      ),
    },
  ];

  return (
    <div>
      <AppHeader app={app} active="feedback" />

      <Card>
        <Space wrap style={{ marginBottom: 12 }}>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={rating}
            options={FEEDBACK_RATING_OPTIONS}
            onChange={(event) => {
              setRating(event.target.value as "all" | FeedbackRatingValue);
              setPage(1);
            }}
          />
          <Checkbox
            checked={hasCorrection}
            onChange={(event) => {
              setHasCorrection(event.target.checked);
              setPage(1);
            }}
          >
            有纠错
          </Checkbox>
          <Select
            aria-label="状态"
            style={{ width: 130 }}
            value={status}
            options={FEEDBACK_STATUS_OPTIONS}
            onChange={(value) => {
              setStatus(value);
              setPage(1);
            }}
          />
          <Select
            aria-label="时间范围"
            style={{ width: 150 }}
            value={range}
            options={LOG_RANGE_OPTIONS}
            onChange={(value) => {
              setRange(value);
              setPage(1);
            }}
          />
          <Typography.Text type="secondary" data-testid="feedback-summary">
            点赞 {summary?.up ?? 0} · 点踩 {summary?.down ?? 0}（纠错{" "}
            {summary?.down_with_correction ?? 0}）
          </Typography.Text>
        </Space>

        <Table<FeedbackListItemOut>
          rowKey="id"
          size="small"
          loading={query.isLoading}
          columns={columns}
          dataSource={items}
          pagination={{
            current: page,
            pageSize: 10,
            total: query.data?.total ?? 0,
            onChange: setPage,
          }}
        />
      </Card>
    </div>
  );
}
