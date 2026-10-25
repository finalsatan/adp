import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useBatchDocuments, type DocumentOut, type MetadataField } from "./api";
import { DocumentMetaModal } from "./DocumentMetaModal";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, useBatchDocuments: vi.fn() };
});

const mockedBatch = vi.mocked(useBatchDocuments);

const FIELDS: MetadataField[] = [
  { id: 1, kb_id: 1, name: "制度编号", type: "text", options: null, doc_count: 0 },
  { id: 2, kb_id: 1, name: "部门", type: "select", options: ["人事", "财务"], doc_count: 0 },
];

const DOC = { id: 5, name: "差旅管理办法.pdf" } as DocumentOut;

beforeEach(() => {
  mockedBatch.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useBatchDocuments>);
});

afterEach(() => vi.clearAllMocks());

function renderModal() {
  render(
    <AntApp>
      <DocumentMetaModal open kbId={1} docs={[DOC]} fields={FIELDS} onClose={vi.fn()} />
    </AntApp>,
  );
}

describe("DocumentMetaModal", () => {
  it("只提交填写了的字段（设置）", async () => {
    const mutate = vi.fn();
    mockedBatch.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBatchDocuments>);

    renderModal();
    fireEvent.click(screen.getAllByText("设置")[0]!);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "ZD-001" } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toEqual({
      action: "set_metadata",
      doc_ids: [5],
      metadata: { 制度编号: "ZD-001" },
    });
  });

  it("清空字段提交 null，保持不变不提交", async () => {
    const mutate = vi.fn();
    mockedBatch.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBatchDocuments>);

    renderModal();
    // 两个字段：制度编号清空，部门保持默认“保持不变”
    const clearButtons = screen.getAllByText("清空");
    fireEvent.click(clearButtons[0]!);
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toEqual({
      action: "set_metadata",
      doc_ids: [5],
      metadata: { 制度编号: null },
    });
  });

  it("没有任何修改时不提交", () => {
    const mutate = vi.fn();
    mockedBatch.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBatchDocuments>);

    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
    expect(mutate).not.toHaveBeenCalled();
  });
});
