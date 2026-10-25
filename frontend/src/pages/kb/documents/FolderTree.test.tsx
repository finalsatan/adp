import { App as AntApp } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCreateFolder, useDeleteFolder, useRenameFolder, type FolderOut } from "./api";
import { FolderTree } from "./FolderTree";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useCreateFolder: vi.fn(),
    useRenameFolder: vi.fn(),
    useDeleteFolder: vi.fn(),
  };
});

const mockedCreate = vi.mocked(useCreateFolder);
const mockedRename = vi.mocked(useRenameFolder);
const mockedDelete = vi.mocked(useDeleteFolder);

function mutationStub(): never {
  return { mutate: vi.fn(), isPending: false } as never;
}

const FOLDERS: FolderOut[] = [
  { id: 1, kb_id: 1, parent_id: null, path: "/1/", name: "人事制度", doc_count: 46 },
  { id: 2, kb_id: 1, parent_id: 1, path: "/1/2/", name: "考勤与假期", doc_count: 12 },
  { id: 3, kb_id: 1, parent_id: null, path: "/3/", name: "财务制度", doc_count: 38 },
];

beforeEach(() => {
  mockedCreate.mockImplementation(() => mutationStub());
  mockedRename.mockImplementation(() => mutationStub());
  mockedDelete.mockImplementation(() => mutationStub());
});

afterEach(() => vi.clearAllMocks());

function renderTree(onSelect = vi.fn()) {
  render(
    <AntApp>
      <FolderTree
        kbId={1}
        folders={FOLDERS}
        selectedFolderId="all"
        onSelect={onSelect}
        totalCount={128}
        uncategorizedCount={13}
        editable
      />
    </AntApp>,
  );
  return onSelect;
}

describe("FolderTree", () => {
  it("显示全部文档、未分类与多级文件夹及各自文档数", () => {
    renderTree();
    expect(screen.getByText("全部文档")).toBeInTheDocument();
    expect(screen.getByText("128")).toBeInTheDocument();
    expect(screen.getByText("未分类")).toBeInTheDocument();
    expect(screen.getByText("13")).toBeInTheDocument();
    expect(screen.getByText("人事制度")).toBeInTheDocument();
    expect(screen.getByText("考勤与假期")).toBeInTheDocument();
    expect(screen.getByText("财务制度")).toBeInTheDocument();
    expect(screen.getByText("46")).toBeInTheDocument();
  });

  it("点击文件夹把文件夹 ID 回调出去", () => {
    const onSelect = renderTree();
    fireEvent.click(screen.getByText("财务制度"));
    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it("点击未分类回调 uncategorized", () => {
    const onSelect = renderTree();
    fireEvent.click(screen.getByText("未分类"));
    expect(onSelect).toHaveBeenCalledWith("uncategorized");
  });
});
