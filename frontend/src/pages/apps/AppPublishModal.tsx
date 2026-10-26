import { Alert, App as AntApp, Input, Modal, Typography } from "antd";
import { useEffect, useState } from "react";

import { getErrorMessage } from "../../api/errors";
import { usageScopeText, usePublishApp, publishProblems, type App, type PublishProblem } from "./api";

interface AppPublishModalProps {
  app: App;
  open: boolean;
  onClose: () => void;
  onPublished?: () => void;
}

/** 发布弹窗（原型 #m-publish）：版本说明、使用范围、对话页链接、校验问题清单。 */
export function AppPublishModal({ app, open, onClose, onPublished }: AppPublishModalProps) {
  const { message } = AntApp.useApp();
  const publish = usePublishApp();
  const [note, setNote] = useState("");
  const [problems, setProblems] = useState<PublishProblem[]>([]);

  useEffect(() => {
    if (open) {
      setNote("");
      setProblems([]);
    }
  }, [open]);

  const nextVersion = (app.published_version?.version_no ?? 0) + 1;
  const chatUrl =
    typeof window === "undefined" ? app.chat_path : `${window.location.origin}${app.chat_path}`;

  const handlePublish = () => {
    if (note.trim() === "") {
      message.warning("请填写版本说明");
      return;
    }
    publish.mutate(
      { id: app.id, body: { note: note.trim() } },
      {
        onSuccess: () => {
          message.success("已发布");
          onPublished?.();
          onClose();
        },
        onError: (error) => {
          const list = publishProblems(error);
          setProblems(list);
          if (list.length === 0) {
            message.error(getErrorMessage(error));
          }
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      title={`发布「${app.name}」`}
      okText="发布"
      cancelText="取消"
      confirmLoading={publish.isPending}
      onOk={handlePublish}
      onCancel={onClose}
    >
      <Typography.Paragraph>
        将把当前草稿发布为 <b>v{nextVersion}</b>，终端用户随即使用新版本。
      </Typography.Paragraph>

      {problems.length > 0 ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message="发布校验未通过"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {problems.map((problem, index) => (
                <li key={`${problem.code}-${index}`}>{problem.message}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      <Typography.Text>版本说明</Typography.Text>
      <Input.TextArea
        value={note}
        rows={3}
        maxLength={200}
        placeholder="例如：问题改写改用通义千问-Turbo；加入“产品手册”，开启重排"
        onChange={(event) => setNote(event.target.value)}
      />

      <Typography.Paragraph style={{ marginTop: 16, marginBottom: 4 }}>
        <Typography.Text type="secondary">使用范围：</Typography.Text>
        {usageScopeText(app.usage_scope)}（在“权限与设置”中修改）
      </Typography.Paragraph>
      <Typography.Paragraph style={{ marginBottom: 0 }}>
        <Typography.Text type="secondary">对话页链接：</Typography.Text>
        <Typography.Link href={app.chat_path} target="_blank" rel="noopener noreferrer">
          {chatUrl}
        </Typography.Link>
      </Typography.Paragraph>
    </Modal>
  );
}
