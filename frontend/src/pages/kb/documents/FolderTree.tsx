import { MoreOutlined } from "@ant-design/icons";
import { App, Button, Card, Dropdown, Form, Input, Modal, Select, Tree, Typography } from "antd";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { getErrorMessage } from "../../../api/errors";
import {
  useCreateFolder,
  useDeleteFolder,
  useRenameFolder,
  type FolderOut,
} from "./api";

interface FolderTreeProps {
  kbId: number;
  folders: FolderOut[];
  selectedFolderId: number | "all" | "uncategorized";
  onSelect: (folderId: number | "all" | "uncategorized") => void;
  totalCount: number;
  uncategorizedCount: number;
  editable: boolean;
}

interface FolderNode {
  key: string;
  title: ReactNode;
  children: FolderNode[];
  selectable?: boolean;
}

function folderDepth(path: string): number {
  return path.split("/").filter(Boolean).length;
}

/** 知识库文档页左侧文件夹树（原型 #v-kb 左栏）。 */
export function FolderTree({
  kbId,
  folders,
  selectedFolderId,
  onSelect,
  totalCount,
  uncategorizedCount,
  editable,
}: FolderTreeProps) {
  const { message, modal } = App.useApp();
  const createFolder = useCreateFolder(kbId);
  const renameFolder = useRenameFolder(kbId);
  const deleteFolder = useDeleteFolder(kbId);

  const [newOpen, setNewOpen] = useState(false);
  const [newParentId, setNewParentId] = useState<number | null>(null);
  const [renameTarget, setRenameTarget] = useState<FolderOut | null>(null);
  const [form] = Form.useForm<{ name: string }>();
  const [renameForm] = Form.useForm<{ name: string }>();

  const parentOptions = useMemo(
    () => [
      { value: 0, label: "根目录" },
      ...folders.map((folder) => ({
        value: folder.id,
        label: `${"　".repeat(Math.max(0, folderDepth(folder.path) - 1))}${folder.name}`,
      })),
    ],
    [folders],
  );

  useEffect(() => {
    if (newOpen) {
      form.resetFields();
    }
  }, [newOpen, form]);

  useEffect(() => {
    if (renameTarget) {
      renameForm.setFieldsValue({ name: renameTarget.name });
    }
  }, [renameTarget, renameForm]);

  const handleDelete = (folder: FolderOut) =>
    modal.confirm({
      title: `删除文件夹「${folder.name}」？`,
      content: "文件夹下有子文件夹或文档时不能删除。",
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () =>
        deleteFolder.mutateAsync(folder.id).then(() => {
          message.success("已删除");
          if (selectedFolderId === folder.id) {
            onSelect("all");
          }
        }),
    });

  const treeData = useMemo<FolderNode[]>(() => {
    const childrenOf = new Map<number | null, FolderOut[]>();
    for (const folder of folders) {
      const list = childrenOf.get(folder.parent_id) ?? [];
      list.push(folder);
      childrenOf.set(folder.parent_id, list);
    }

    const renderFolderTitle = (folder: FolderOut): ReactNode => (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <span>{folder.name}</span>
        <Typography.Text type="secondary">{folder.doc_count}</Typography.Text>
        {editable ? (
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [
                {
                  key: "new-child",
                  label: "新建子文件夹",
                  onClick: () => {
                    setNewParentId(folder.id);
                    setNewOpen(true);
                  },
                },
                { key: "rename", label: "重命名", onClick: () => setRenameTarget(folder) },
                {
                  key: "delete",
                  label: "删除",
                  danger: true,
                  onClick: () => void handleDelete(folder),
                },
              ],
            }}
          >
            <Button
              type="text"
              size="small"
              aria-label={`${folder.name} 操作`}
              icon={<MoreOutlined />}
              onClick={(event) => event.stopPropagation()}
            />
          </Dropdown>
        ) : null}
      </span>
    );

    const build = (parentId: number | null): FolderNode[] =>
      (childrenOf.get(parentId) ?? []).map((folder) => ({
        key: `folder-${folder.id}`,
        title: renderFolderTitle(folder),
        children: build(folder.id),
      }));

    return [
      {
        key: "all",
        title: (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span>全部文档</span>
            <Typography.Text type="secondary">{totalCount}</Typography.Text>
          </span>
        ),
        children: [
          ...build(null),
          {
            key: "uncategorized",
            title: (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <span>未分类</span>
                <Typography.Text type="secondary">{uncategorizedCount}</Typography.Text>
              </span>
            ),
            children: [],
          },
        ],
      },
    ];
    // handleDelete 每次渲染都会重建，但只在菜单点击时调用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folders, editable, totalCount, uncategorizedCount]);

  const handleCreate = async () => {
    const values = await form.validateFields();
    createFolder.mutate(
      { name: values.name, parent_id: newParentId },
      {
        onSuccess: () => {
          message.success("已创建");
          setNewOpen(false);
        },
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  const handleRename = async () => {
    if (!renameTarget) {
      return;
    }
    const values = await renameForm.validateFields();
    renameFolder.mutate(
      { folderId: renameTarget.id, name: values.name },
      {
        onSuccess: () => {
          message.success("已保存");
          setRenameTarget(null);
        },
        onError: (error) => message.error(getErrorMessage(error)),
      },
    );
  };

  const openNew = () => {
    setNewParentId(typeof selectedFolderId === "number" ? selectedFolderId : null);
    setNewOpen(true);
  };

  return (
    <Card
      title="文件夹"
      size="small"
      extra={editable ? <a onClick={openNew}>+ 新建</a> : null}
      style={{ width: 240, flex: "0 0 240px", alignSelf: "flex-start" }}
    >
      <Tree
        blockNode
        defaultExpandAll
        treeData={treeData}
        selectedKeys={
          selectedFolderId === "all"
            ? ["all"]
            : selectedFolderId === "uncategorized"
              ? ["uncategorized"]
              : [`folder-${selectedFolderId}`]
        }
        onSelect={(keys) => {
          const key = keys[0];
          if (typeof key !== "string") {
            return;
          }
          if (key === "all") {
            onSelect("all");
          } else if (key === "uncategorized") {
            onSelect("uncategorized");
          } else if (key.startsWith("folder-")) {
            onSelect(Number(key.slice("folder-".length)));
          }
        }}
      />

      <Modal
        open={newOpen}
        title="新建文件夹"
        okText="创建"
        cancelText="取消"
        confirmLoading={createFolder.isPending}
        onOk={handleCreate}
        onCancel={() => setNewOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: "请输入文件夹名称" }]}
          >
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item label="上级文件夹">
            <Select
              value={newParentId ?? 0}
              options={parentOptions}
              onChange={(value: number) => setNewParentId(value === 0 ? null : value)}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        open={renameTarget !== null}
        title="重命名文件夹"
        okText="保存"
        cancelText="取消"
        confirmLoading={renameFolder.isPending}
        onOk={handleRename}
        onCancel={() => setRenameTarget(null)}
        destroyOnHidden
      >
        <Form form={renameForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入文件夹名称" }]}>
            <Input maxLength={50} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
