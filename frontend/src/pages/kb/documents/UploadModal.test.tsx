import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  precheckDocuments,
  sha256File,
  uploadDocuments,
  useDefaultSchemes,
  useSchemes,
  type DocumentPrecheckOut,
} from "./api";
import { UploadModal } from "./UploadModal";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useDefaultSchemes: vi.fn(),
    useSchemes: vi.fn(),
    precheckDocuments: vi.fn(),
    sha256File: vi.fn(),
    uploadDocuments: vi.fn(),
  };
});

const mockedDefaultSchemes = vi.mocked(useDefaultSchemes);
const mockedSchemes = vi.mocked(useSchemes);
const mockedPrecheck = vi.mocked(precheckDocuments);
const mockedSha = vi.mocked(sha256File);
const mockedUpload = vi.mocked(uploadDocuments);

const PRECHECK: DocumentPrecheckOut = {
  errors: [],
  items: [
    {
      name: "a.pdf",
      relative_path: null,
      file_type: "pdf",
      size: 3,
      scheme_id: 1,
      scheme_name: "PDF · 通用",
      name_conflict: null,
      duplicate: null,
      too_long: false,
      estimated_tokens: 100,
      limit_tokens: 8000,
    },
  ],
};

beforeEach(() => {
  mockedDefaultSchemes.mockReturnValue({
    data: {
      items: [
        {
          file_type: "pdf",
          scheme_id: 1,
          scheme_name: "PDF · 通用",
          fallback_scheme_id: 1,
          fallback_scheme_name: "PDF · 通用",
        },
      ],
    },
  } as unknown as ReturnType<typeof useDefaultSchemes>);
  mockedSchemes.mockReturnValue({
    data: { items: [{ id: 1, name: "PDF · 通用", file_type: "pdf" }], total: 1 },
  } as unknown as ReturnType<typeof useSchemes>);
  mockedPrecheck.mockResolvedValue(PRECHECK);
  mockedSha.mockResolvedValue("abc123");
  mockedUpload.mockResolvedValue({ items: [], skipped: [] });
});

afterEach(() => vi.clearAllMocks());

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <UploadModal
          open
          kbId={1}
          kbName="员工制度库"
          folders={[
            { id: 9, kb_id: 1, parent_id: null, path: "/9/", name: "财务制度", doc_count: 1 },
          ]}
          defaultFolderId={9}
          onClose={vi.fn()}
          onUploaded={vi.fn()}
        />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("UploadModal", () => {
  it("显示上传说明与默认文件夹", () => {
    renderModal();
    expect(screen.getByText("拖入文件或文件夹，或点击选择")).toBeInTheDocument();
    expect(
      screen.getByText("单个文件不超过 200MB；上传文件夹时保留目录结构"),
    ).toBeInTheDocument();
    expect(screen.getByText("/9/")).toBeInTheDocument();
  });

  it("按文件类型分组并默认选中知识库设置的方案，预检后进入确认", async () => {
    renderModal();
    const input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input as HTMLInputElement, {
      target: { files: [new File(["abc"], "a.pdf", { type: "application/pdf" })] },
    });

    expect(await screen.findByText(/PDF 1 个/)).toBeInTheDocument();
    expect(screen.getByText("PDF · 通用")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "预检并继续" }));
    expect(await screen.findByText(/超长预检通过/)).toBeInTheDocument();
    await waitFor(() => expect(mockedPrecheck).toHaveBeenCalledTimes(1));
    expect(mockedPrecheck.mock.calls[0]?.[1].schemes).toEqual({ pdf: 1 });
  });
});
