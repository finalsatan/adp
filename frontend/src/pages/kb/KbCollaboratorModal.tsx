import { Alert, App, Modal, Select, Typography } from "antd";
import { useEffect, useState } from "react";

import { getErrorMessage } from "../../api/errors";
import { useUserSearch } from "../../components/resource/api";
import { useAddKbMember, type Kb } from "./api";

interface KbCollaboratorModalProps {
  open: boolean;
  kb: Kb | null;
  /** 已经是协作者的用户 ID，不在下拉里重复出现。 */
  existingIds: Set<number>;
  onClose: () => void;
}

/** 添加知识库协作者弹窗（原型 #d-collab）。 */
export function KbCollaboratorModal({
  open,
  kb,
  existingIds,
  onClose,
}: KbCollaboratorModalProps) {
  const { message } = App.useApp();
  const addMember = useAddKbMember();
  const [keyword, setKeyword] = useState("");
  const [userId, setUserId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = useUserSearch(keyword);

  useEffect(() => {
    if (open) {
      setKeyword("");
      setUserId(null);
      setError(null);
    }
  }, [open]);

  const options = (search.data ?? [])
    .filter((user) => !existingIds.has(user.id))
    .map((user) => ({
      value: user.id,
      label: `${user.username} ${user.name} · ${user.org?.name ?? "无组织"}`,
    }));

  const handleAdd = () => {
    if (!kb || userId == null) {
      setError("请选择要添加的用户");
      return;
    }
    setError(null);
    addMember.mutate(
      { id: kb.id, userId },
      {
        onSuccess: () => {
          message.success("已添加");
          onClose();
        },
        onError: (err) => setError(getErrorMessage(err)),
      },
    );
  };

  return (
    <Modal
      open={open}
      title={kb ? `添加协作者 · ${kb.name}` : "添加协作者"}
      okText="添加"
      cancelText="取消"
      confirmLoading={addMember.isPending}
      onOk={handleAdd}
      onCancel={onClose}
      destroyOnHidden
    >
      <Select
        showSearch
        value={userId ?? undefined}
        placeholder="搜索用户名或姓名，可选任何组织的用户"
        style={{ width: "100%" }}
        filterOption={false}
        onSearch={setKeyword}
        onChange={(value) => setUserId(value)}
        options={options}
        notFoundContent={
          keyword.trim().length > 0
            ? search.isFetching
              ? "搜索中…"
              : "没有匹配的用户"
            : "输入关键字搜索"
        }
      />

      {error ? (
        <Alert type="error" showIcon style={{ marginTop: 12 }} message={error} />
      ) : null}

      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        协作者可以查看和编辑本知识库：上传、删除、移动文档；修改解析结果、分块、问答对；修改元数据字段、默认入库方案、默认检索配置。不能改可见范围、增减协作者、停用或删除知识库。
      </Typography.Paragraph>
    </Modal>
  );
}
