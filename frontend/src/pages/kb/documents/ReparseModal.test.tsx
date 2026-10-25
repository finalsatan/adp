import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useBatchDocuments,
  useReparseDocument,
  type DocumentOut,
  type Scheme,
} from "./api";
import { ReparseModal } from "./ReparseModal";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, useReparseDocument: vi.fn(), useBatchDocuments: vi.fn() };
});

const mockedReparse = vi.mocked(useReparseDocument);
const mockedBatch = vi.mocked(useBatchDocuments);

const SCHEMES: Scheme[] = [
  { id: 1, name: "PDF · 通用", file_type: "pdf" },
  { id: 2, name: "PDF · 扫描件", file_type: "pdf" },
] as unknown as Scheme[];

function documentOut(overrides: Partial<DocumentOut>): DocumentOut {
  return {
    id: 1,
    kb_id: 1,
    name: "差旅管理办法.pdf",
    file_type: "pdf",
    status: "done",
    edited: true,
    scheme: { id: 1, name: "PDF · 通用", file_type: "pdf", builtin: true },
    scheme_snapshot_name: "PDF · 通用",
    ...overrides,
  } as unknown as DocumentOut;
}

beforeEach(() => {
  mockedReparse.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useReparseDocument>);
  mockedBatch.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useBatchDocuments>);
});

afterEach(() => vi.clearAllMocks());

describe("ReparseModal", () => {
  it("单个文档显示方案快照与人工修改提示，提交重新解析", async () => {
    const mutate = vi.fn();
    mockedReparse.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useReparseDocument>);

    render(
      <AntApp>
        <ReparseModal
          open
          kbId={1}
          docs={[documentOut({})]}
          schemes={SCHEMES}
          onClose={vi.fn()}
        />
      </AntApp>,
    );

    expect(screen.getByText(/文档当前使用的方案快照：PDF · 通用/)).toBeInTheDocument();
    expect(screen.getByText(/解析结果或分块有人工修改/)).toBeInTheDocument();
    expect(screen.getByText(/按方案重新生成解析结果和分块/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ docId: 1, mode: "reparse", schemeId: 1 });
  });

  it("批量重新解析统一选方式并汇总文档", async () => {
    const mutate = vi.fn();
    mockedBatch.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBatchDocuments>);

    render(
      <AntApp>
        <ReparseModal
          open
          kbId={1}
          docs={[documentOut({ id: 1 }), documentOut({ id: 2, name: "考勤.docx", edited: false })]}
          schemes={SCHEMES}
          onClose={vi.fn()}
        />
      </AntApp>,
    );

    expect(screen.getByText("重新解析 · 已选 2 个文档")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toEqual({
      action: "reparse",
      doc_ids: [1, 2],
      mode: "reparse",
      scheme_id: null,
    });
  });
});
