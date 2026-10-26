import { Alert, App as AntApp, Button, Card, Descriptions, Modal, Space, Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useState } from "react";
import { useParams } from "react-router-dom";

import { getErrorMessage } from "../../api/errors";
import { AppHeader } from "./AppHeader";
import { AppPublishModal } from "./AppPublishModal";
import {
  configSummaryRows,
  useApp,
  useAppVersions,
  useRestoreAppVersion,
  type AppVersionOut,
} from "./api";

/** 发布历史页（原型 #v-appver）。 */
export default function AppVersionsPage() {
  const { id: idParam } = useParams();
  const parsedId = Number(idParam);
  const appId = Number.isFinite(parsedId) ? parsedId : null;
  const { message } = AntApp.useApp();

  const { data: app, isLoading } = useApp(appId);
  const [page, setPage] = useState(1);
  const { data: versions } = useAppVersions(appId, page, 20);
  const restore = useRestoreAppVersion();

  const [configVersion, setConfigVersion] = useState<AppVersionOut | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);

  if (isLoading || !app) {
    return null;
  }

  const handleRestore = (version: AppVersionOut) => {
    restore.mutate(
      { id: app.id, version: version.version_no },
      {
        onSuccess: () => message.success(`已把 v${version.version_no} 恢复为草稿，可到“配置与调试”查看`),
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  return (
    <div>
      <AppHeader app={app} active="versions" />

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={() => setPublishOpen(true)}>
          发布
        </Button>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="终端用户使用最新发布的版本。要回滚，先把历史版本恢复为草稿，再发布。"
      />

      <Card>
        <Table<AppVersionOut>
          rowKey="id"
          dataSource={versions?.items ?? []}
          pagination={{
            current: page,
            pageSize: 20,
            total: versions?.total ?? 0,
            onChange: setPage,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: "版本",
              dataIndex: "version_no",
              render: (value: number, record) => (
                <Space size="small">
                  <b>v{value}</b>
                  {record.is_current ? <Tag color="green">当前</Tag> : null}
                </Space>
              ),
            },
            {
              title: "版本说明",
              dataIndex: "note",
              render: (value: string) => <Typography.Text>{value}</Typography.Text>,
            },
            { title: "发布人", dataIndex: ["published_by", "name"] },
            {
              title: "发布时间",
              dataIndex: "published_at",
              render: (value: string) => (
                <Typography.Text type="secondary">
                  {dayjs(value).format("MM-DD HH:mm")}
                </Typography.Text>
              ),
            },
            {
              title: "操作",
              key: "actions",
              render: (_value, record) => (
                <Space size="middle">
                  <Button type="link" style={{ padding: 0 }} onClick={() => setConfigVersion(record)}>
                    查看配置
                  </Button>
                  <Button
                    type="link"
                    style={{ padding: 0 }}
                    disabled={record.is_current}
                    loading={restore.isPending}
                    onClick={() => handleRestore(record)}
                  >
                    恢复为草稿
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <AppPublishModal
        app={app}
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
      />

      <Modal
        open={configVersion !== null}
        title={configVersion ? `v${configVersion.version_no} 的配置` : "配置"}
        footer={null}
        width={720}
        onCancel={() => setConfigVersion(null)}
      >
        <Descriptions
          column={1}
          size="small"
          items={
            configVersion
              ? configSummaryRows(configVersion.config).map((row) => ({
                  key: row.label,
                  label: row.label,
                  children: (
                    <Typography.Text style={{ whiteSpace: "pre-wrap" }}>{row.value}</Typography.Text>
                  ),
                }))
              : []
          }
        />
      </Modal>
    </div>
  );
}
