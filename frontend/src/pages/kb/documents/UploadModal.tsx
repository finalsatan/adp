import { InboxOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Modal,
  Progress,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
} from "antd";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type InputHTMLAttributes } from "react";

import { getErrorMessage } from "../../../api/errors";
import { fileTypeLabel } from "../api";
import {
  buildPrecheckConfirmations,
  defaultResolutions,
  groupByFileType,
  hasPrecheckErrors,
  precheckDocuments,
  resolveSchemeId,
  sha256File,
  uploadDocuments,
  useDefaultSchemes,
  useSchemes,
  type DocumentPrecheckOut,
  type FolderOut,
  type PrecheckItem,
  type Resolutions,
  type Scheme,
  type UploadInput,
} from "./api";

interface UploadModalProps {
  open: boolean;
  kbId: number;
  kbName: string;
  folders: FolderOut[];
  defaultFolderId: number | null;
  onClose: () => void;
  onUploaded: () => void;
}

type Step = "select" | "confirm" | "uploading";

interface DirectoryInputProps extends InputHTMLAttributes<HTMLInputElement> {
  webkitdirectory?: string;
  directory?: string;
}

const FOLDER_PLACEHOLDER = 0;
const COLLAPSE_THRESHOLD = 5;

function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve) => {
    const all: FileSystemEntry[] = [];
    const read = () =>
      reader.readEntries((batch) => {
        if (batch.length === 0) {
          resolve(all);
          return;
        }
        all.push(...batch);
        read();
      });
    read();
  });
}

async function collectEntry(
  entry: FileSystemEntry,
  prefix: string,
  out: UploadInput[],
): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => {
      (entry as FileSystemFileEntry).file(resolve, reject);
    });
    out.push({ file, relativePath: prefix });
    return;
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const children = await readAllEntries(reader);
    for (const child of children) {
      await collectEntry(child, `${prefix}${child.name}${child.isDirectory ? "/" : ""}`, out);
    }
  }
}

/** 上传弹窗（原型 #d-upload）：多文件 / 文件夹、按类型分组选方案、预检确认、进度。 */
export function UploadModal({
  open,
  kbId,
  kbName,
  folders,
  defaultFolderId,
  onClose,
  onUploaded,
}: UploadModalProps) {
  const { message } = App.useApp();
  const { data: defaultSchemes } = useDefaultSchemes(kbId);
  const { data: schemePage } = useSchemes();
  const schemeList = useMemo(() => schemePage?.items ?? [], [schemePage]);

  const [step, setStep] = useState<Step>("select");
  const [files, setFiles] = useState<UploadInput[]>([]);
  const [folderId, setFolderId] = useState<number | null>(defaultFolderId);
  const [schemes, setSchemes] = useState<Record<string, number | null>>({});
  const [precheck, setPrecheck] = useState<DocumentPrecheckOut | null>(null);
  const [resolutions, setResolutions] = useState<Resolutions>({
    name_conflict: {},
    duplicate: {},
  });
  const [tooLongKeep, setTooLongKeep] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const folderInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setStep("select");
      setFiles([]);
      setSchemes({});
      setPrecheck(null);
      setResolutions({ name_conflict: {}, duplicate: {} });
      setTooLongKeep({});
      setError(null);
      setProgress(0);
      setBusy(false);
      setFolderId(defaultFolderId);
      setExpandedGroups({});
    }
  }, [open, defaultFolderId]);

  useEffect(() => {
    const input = folderInputRef.current;
    if (input) {
      input.setAttribute("webkitdirectory", "");
    }
  }, [open]);

  const groups = useMemo(
    () => groupByFileType(files.map((item) => ({ ...item, name: item.file.name }))),
    [files],
  );

  // 默认方案：知识库默认 → 平台内置 → 该类型第一个方案
  useEffect(() => {
    setSchemes((previous) => {
      const next = { ...previous };
      let changed = false;
      for (const group of groups) {
        if (next[group.fileType] == null) {
          const resolved = resolveSchemeId(group.fileType, defaultSchemes, schemePage);
          if (resolved != null) {
            next[group.fileType] = resolved;
            changed = true;
          }
        }
      }
      return changed ? next : previous;
    });
  }, [groups, defaultSchemes, schemePage]);

  const schemesByType = useMemo(() => {
    const map = new Map<string, Scheme[]>();
    for (const scheme of schemeList) {
      const list = map.get(scheme.file_type) ?? [];
      list.push(scheme);
      map.set(scheme.file_type, list);
    }
    return map;
  }, [schemeList]);

  const addFiles = (incoming: UploadInput[]) => {
    setFiles((previous) => {
      const seen = new Set(previous.map((item) => `${item.relativePath}:${item.file.name}`));
      const merged = [...previous];
      for (const item of incoming) {
        const key = `${item.relativePath}:${item.file.name}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(item);
        }
      }
      return merged;
    });
    setStep("select");
    setPrecheck(null);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const items = event.dataTransfer.items;
    const entries: FileSystemEntry[] = [];
    for (let index = 0; index < items.length; index += 1) {
      const entry = items[index]?.webkitGetAsEntry();
      if (entry) {
        entries.push(entry);
      }
    }
    if (entries.length === 0) {
      addFiles(Array.from(event.dataTransfer.files).map((file) => ({ file, relativePath: "" })));
      return;
    }
    void (async () => {
      const collected: UploadInput[] = [];
      for (const entry of entries) {
        await collectEntry(entry, "", collected);
      }
      addFiles(collected);
    })();
  };

  const handleFolderPick = (event: ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files ?? []).map((file) => ({
      file,
      relativePath: file.webkitRelativePath || "",
    }));
    addFiles(picked);
    event.target.value = "";
  };

  const removeFile = (target: UploadInput) => {
    setFiles((previous) =>
      previous.filter(
        (item) => !(item.file === target.file && item.relativePath === target.relativePath),
      ),
    );
    setPrecheck(null);
    setStep("select");
  };

  const runPrecheck = async () => {
    if (files.length === 0) {
      setError("请先选择文件");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const precheckFiles = await Promise.all(
        files.map(async ({ file, relativePath }) => ({
          name: file.name,
          size: file.size,
          relative_path: relativePath || null,
          sha256: await sha256File(file),
        })),
      );
      const result = await precheckDocuments(kbId, {
        files: precheckFiles,
        folder_id: folderId,
        schemes,
      });
      setPrecheck(result);
      setResolutions(defaultResolutions(result));
      setTooLongKeep(
        Object.fromEntries(result.items.filter((item) => item.too_long).map((item) => [item.name, true])),
      );
      setStep("confirm");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const startUpload = async () => {
    setBusy(true);
    setError(null);
    setStep("uploading");
    setProgress(0);
    try {
      const result = await uploadDocuments({
        kbId,
        files,
        folderId,
        schemes,
        resolutions,
        onProgress: setProgress,
      });
      const skipped = result.skipped.length;
      message.success(
        skipped > 0 ? `已上传 ${result.items.length} 个，跳过 ${skipped} 个` : `已上传 ${result.items.length} 个文档`,
      );
      onUploaded();
      onClose();
    } catch (caught) {
      setError(getErrorMessage(caught));
      setStep("confirm");
    } finally {
      setBusy(false);
    }
  };

  const confirmations = precheck ? buildPrecheckConfirmations(precheck) : null;
  const blockedByTooLong = Object.values(tooLongKeep).some((keep) => !keep);

  const folderOptions = useMemo(
    () => [
      { value: FOLDER_PLACEHOLDER, label: "未分类" },
      ...folders.map((folder) => ({ value: folder.id, label: folder.path })),
    ],
    [folders],
  );

  const renderItem = (item: PrecheckItem) => (
    <div key={`${item.name}-${item.relative_path ?? ""}`} style={{ marginBottom: 8 }}>
      <Space align="start" wrap>
        <Tag>{fileTypeLabel(item.file_type)}</Tag>
        <Typography.Text>{item.name}</Typography.Text>
        <Typography.Text type="secondary">→ {item.scheme_name}</Typography.Text>
      </Space>
    </div>
  );

  return (
    <Modal
      open={open}
      title={`上传文档 · ${kbName}`}
      width={720}
      footer={null}
      onCancel={onClose}
      destroyOnHidden
    >
      {step === "select" ? (
        <>
          <div
            onDrop={handleDrop}
            onDragOver={(event) => event.preventDefault()}
            style={{
              border: "1px dashed #d9d9d9",
              borderRadius: 8,
              padding: 24,
              textAlign: "center",
              marginBottom: 12,
            }}
          >
            <InboxOutlined style={{ fontSize: 32, color: "#1677ff" }} />
            <Typography.Paragraph style={{ margin: "8px 0" }}>
              拖入文件或文件夹，或点击选择
            </Typography.Paragraph>
            <Space>
              <Upload
                multiple
                showUploadList={false}
                beforeUpload={(file) => {
                  addFiles([{ file, relativePath: "" }]);
                  return false;
                }}
              >
                <Button>选择文件</Button>
              </Upload>
              <Button onClick={() => folderInputRef.current?.click()}>选择文件夹</Button>
              <input
                ref={folderInputRef}
                type="file"
                multiple
                style={{ display: "none" }}
                onChange={handleFolderPick}
                {...({ directory: "" } as DirectoryInputProps)}
              />
            </Space>
            <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
              单个文件不超过 200MB；上传文件夹时保留目录结构
            </Typography.Paragraph>
          </div>

          <Space style={{ marginBottom: 12 }}>
            <Typography.Text>放到文件夹</Typography.Text>
            <Select
              style={{ width: 240 }}
              value={folderId ?? FOLDER_PLACEHOLDER}
              options={folderOptions}
              onChange={(value: number) => setFolderId(value === FOLDER_PLACEHOLDER ? null : value)}
            />
          </Space>

          {files.length > 0 ? (
            <>
              <Typography.Paragraph type="secondary">
                已选 {files.length} 个文件，按类型选择入库方案
              </Typography.Paragraph>
              {groups.map((group) => {
                const expanded = expandedGroups[group.fileType] ?? false;
                const shown = expanded ? group.files : group.files.slice(0, COLLAPSE_THRESHOLD);
                return (
                  <div key={group.fileType} style={{ marginBottom: 12 }}>
                    <Space align="center" wrap>
                      <Typography.Text strong>
                        {fileTypeLabel(group.fileType)} {group.files.length} 个
                      </Typography.Text>
                      {group.files.length > COLLAPSE_THRESHOLD ? (
                        <a
                          onClick={() =>
                            setExpandedGroups((previous) => ({
                              ...previous,
                              [group.fileType]: !expanded,
                            }))
                          }
                        >
                          {expanded ? "收起" : "展开"}
                        </a>
                      ) : null}
                    </Space>
                    <div style={{ marginTop: 4 }}>
                      {shown.map((item) => (
                        <div key={`${item.relativePath}:${item.file.name}`}>
                          <Typography.Text type="secondary">
                            {item.relativePath ? `${item.relativePath}` : item.file.name}
                          </Typography.Text>
                          <a style={{ marginLeft: 8 }} onClick={() => removeFile(item)}>
                            移除
                          </a>
                        </div>
                      ))}
                      {!expanded && group.files.length > COLLAPSE_THRESHOLD ? (
                        <Typography.Text type="secondary">
                          …等 {group.files.length} 个
                        </Typography.Text>
                      ) : null}
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <Select
                        style={{ width: 280 }}
                        value={schemes[group.fileType] ?? undefined}
                        placeholder="选择入库方案"
                        options={(schemesByType.get(group.fileType) ?? []).map((scheme) => ({
                          value: scheme.id,
                          label: scheme.name,
                        }))}
                        onChange={(value: number) =>
                          setSchemes((previous) => ({ ...previous, [group.fileType]: value }))
                        }
                      />
                    </div>
                  </div>
                );
              })}
            </>
          ) : null}

          {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}

          <div style={{ textAlign: "right" }}>
            <Space>
              <Button onClick={onClose}>取消</Button>
              <Button type="primary" loading={busy} onClick={runPrecheck}>
                预检并继续
              </Button>
            </Space>
          </div>
        </>
      ) : null}

      {step === "confirm" && precheck && confirmations ? (
        <>
          {precheck.errors.length > 0 ? (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 12 }}
              message="以下文件不能上传"
              description={
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  {precheck.errors.map((item) => (
                    <li key={`${item.name}-${item.code}`}>
                      {item.name}：{item.message}
                    </li>
                  ))}
                </ul>
              }
            />
          ) : null}

          {confirmations.tooLong.length === 0 ? (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message="超长预检通过：按各组所选方案估算，分块连同标题路径、上下文说明不会超过本库向量化模型最大输入的 90%。"
            />
          ) : (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={`需要确认 ${confirmations.tooLong.length} 项超长风险`}
              description={
                <Space direction="vertical">
                  {confirmations.tooLong.map((entry) => (
                    <div key={entry.name}>
                      <Typography.Text>{entry.name}</Typography.Text>
                      <div>
                        <Checkbox
                          checked={tooLongKeep[entry.name] ?? false}
                          onChange={(event) =>
                            setTooLongKeep((previous) => ({
                              ...previous,
                              [entry.name]: event.target.checked,
                            }))
                          }
                        >
                          仍然上传（不勾选请返回上一步更换方案）
                        </Checkbox>
                      </div>
                    </div>
                  ))}
                </Space>
              }
            />
          )}

          {confirmations.nameConflicts.length > 0 ? (
            <div style={{ marginBottom: 12 }}>
              <Typography.Text strong>
                需要确认 {confirmations.nameConflicts.length + confirmations.duplicates.length} 项
              </Typography.Text>
              {confirmations.nameConflicts.map((entry) => (
                <div key={`conflict-${entry.name}`} style={{ marginTop: 8 }}>
                  <Typography.Text>
                    {entry.name} 与“{entry.refName}”文件夹中的已有文档同名
                  </Typography.Text>
                  <div>
                    <Radio.Group
                      value={resolutions.name_conflict[entry.name] ?? "replace"}
                      onChange={(event) =>
                        setResolutions((previous) => ({
                          ...previous,
                          name_conflict: { ...previous.name_conflict, [entry.name]: event.target.value },
                        }))
                      }
                    >
                      <Radio value="replace">替换</Radio>
                      <Radio value="keep">保留两份</Radio>
                    </Radio.Group>
                  </div>
                  <Typography.Text type="secondary">
                    替换时保留原文档的元数据和所在文件夹；不同文件夹中的同名文件不提示
                  </Typography.Text>
                </div>
              ))}
            </div>
          ) : null}

          {confirmations.duplicates.length > 0 ? (
            <div style={{ marginBottom: 12 }}>
              {confirmations.duplicates.map((entry) => (
                <div key={`duplicate-${entry.name}`} style={{ marginTop: 8 }}>
                  <Typography.Text>
                    {entry.name} 与已有文档“{entry.refName}”内容相同
                  </Typography.Text>
                  <div>
                    <Radio.Group
                      value={resolutions.duplicate[entry.name] ?? "skip"}
                      onChange={(event) =>
                        setResolutions((previous) => ({
                          ...previous,
                          duplicate: { ...previous.duplicate, [entry.name]: event.target.value },
                        }))
                      }
                    >
                      <Radio value="skip">跳过</Radio>
                      <Radio value="upload">仍然上传</Radio>
                    </Radio.Group>
                  </div>
                </div>
              ))}
            </div>
          ) : null}

          <div>
            {precheck.items.map(renderItem)}
          </div>

          {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}

          <div style={{ textAlign: "right" }}>
            <Space>
              <Button onClick={() => setStep("select")}>返回上一步</Button>
              <Button
                type="primary"
                loading={busy}
                disabled={hasPrecheckErrors(precheck) || blockedByTooLong}
                onClick={startUpload}
              >
                开始上传
              </Button>
            </Space>
          </div>
        </>
      ) : null}

      {step === "uploading" ? (
        <div style={{ padding: "24px 0" }}>
          <Typography.Paragraph>正在上传…</Typography.Paragraph>
          <Progress percent={progress} />
        </div>
      ) : null}
    </Modal>
  );
}
