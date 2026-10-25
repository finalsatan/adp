import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildBatchPayload,
  buildDocumentListParams,
  buildPrecheckConfirmations,
  defaultResolutions,
  documentStatusView,
  groupByFileType,
  processingCounts,
  resolveSchemeId,
  shouldPoll,
  useDocumentPolling,
  type DocumentOut,
  type DocumentPrecheckOut,
  type PrecheckItem,
} from "./api";

function doc(overrides: Partial<DocumentOut>): DocumentOut {
  return {
    id: 1,
    kb_id: 1,
    name: "a.pdf",
    file_type: "pdf",
    ext: "pdf",
    size: 1,
    status: "queued",
    progress: 0,
    queue_position: null,
    error_message: null,
    ...overrides,
  } as unknown as DocumentOut;
}

function precheckItem(overrides: Partial<PrecheckItem>): PrecheckItem {
  return {
    name: "a.pdf",
    relative_path: null,
    file_type: "pdf",
    size: 1,
    scheme_id: 1,
    scheme_name: "PDF · 通用",
    name_conflict: null,
    duplicate: null,
    too_long: false,
    estimated_tokens: 100,
    limit_tokens: 1000,
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("documentStatusView", () => {
  it("排队中带本库位次", () => {
    expect(documentStatusView(doc({ status: "queued", queue_position: 3 })).label).toBe(
      "排队中（本库第 3 位）",
    );
    expect(documentStatusView(doc({ status: "queued" })).label).toBe("排队中");
  });

  it("向量化显示进度", () => {
    expect(documentStatusView(doc({ status: "embedding", progress: 72.4 })).label).toBe(
      "向量化中 72%",
    );
  });

  it("失败显示原因", () => {
    const view = documentStatusView(
      doc({ status: "failed", error_message: "第 5 个分块超长" }),
    );
    expect(view.label).toBe("失败");
    expect(view.error).toBe("第 5 个分块超长");
  });

  it("其他状态文案", () => {
    expect(documentStatusView(doc({ status: "parsing" })).label).toBe("解析中");
    expect(documentStatusView(doc({ status: "chunking" })).label).toBe("分块中");
    expect(documentStatusView(doc({ status: "enhancing" })).label).toBe("增强中");
    expect(documentStatusView(doc({ status: "done" })).label).toBe("已完成");
  });
});

describe("shouldPoll", () => {
  it("有处理中文档时轮询，全部完成或失败时停止", () => {
    expect(shouldPoll([doc({ status: "done" }), doc({ status: "failed" })])).toBe(false);
    expect(shouldPoll([doc({ status: "done" }), doc({ status: "embedding" })])).toBe(true);
    expect(shouldPoll(undefined)).toBe(false);
  });
});

describe("useDocumentPolling", () => {
  it("处理中每 5 秒轮询一次，处理完成后停止", () => {
    vi.useFakeTimers();
    const refetch = vi.fn();
    const { rerender } = renderHook(
      ({ processing }: { processing: boolean }) => useDocumentPolling(processing, refetch),
      { initialProps: { processing: true } },
    );

    act(() => {
      vi.advanceTimersByTime(15000);
    });
    expect(refetch).toHaveBeenCalledTimes(3);

    rerender({ processing: false });
    act(() => {
      vi.advanceTimersByTime(15000);
    });
    expect(refetch).toHaveBeenCalledTimes(3);
  });
});

describe("buildPrecheckConfirmations / defaultResolutions", () => {
  const out: DocumentPrecheckOut = {
    errors: [],
    items: [
      precheckItem({
        name: "同名.pdf",
        name_conflict: { doc_id: 7, name: "同名.pdf" },
      }),
      precheckItem({
        name: "重复.docx",
        file_type: "word",
        duplicate: { doc_id: 8, name: "原文档.docx" },
      }),
      precheckItem({ name: "超长.pdf", too_long: true, estimated_tokens: 9000, limit_tokens: 8000 }),
    ],
  };

  it("按同名、重复、超长拆成三类确认项", () => {
    const confirmations = buildPrecheckConfirmations(out);
    expect(confirmations.nameConflicts).toHaveLength(1);
    expect(confirmations.nameConflicts[0]?.docId).toBe(7);
    expect(confirmations.duplicates).toHaveLength(1);
    expect(confirmations.duplicates[0]?.refName).toBe("原文档.docx");
    expect(confirmations.tooLong).toHaveLength(1);
    expect(confirmations.tooLong[0]?.name).toBe("超长.pdf");
  });

  it("默认同名替换、重复跳过", () => {
    const resolutions = defaultResolutions(out);
    expect(resolutions.name_conflict["同名.pdf"]).toBe("replace");
    expect(resolutions.duplicate["重复.docx"]).toBe("skip");
  });
});

describe("groupByFileType / resolveSchemeId", () => {
  it("按文件类型分组", () => {
    const groups = groupByFileType([
      { name: "a.pdf" },
      { name: "b.docx" },
      { name: "c.pdf" },
    ]);
    expect(groups.map((group) => group.fileType)).toEqual(["pdf", "word"]);
    expect(groups[0]?.files).toHaveLength(2);
  });

  it("默认方案优先知识库设置，其次平台内置，最后第一个可用方案", () => {
    const defaultSchemes = {
      items: [
        {
          file_type: "pdf" as const,
          scheme_id: 5,
          scheme_name: "PDF · 扫描件",
          fallback_scheme_id: 1,
          fallback_scheme_name: "PDF · 通用",
        },
        {
          file_type: "word" as const,
          scheme_id: null,
          scheme_name: null,
          fallback_scheme_id: 2,
          fallback_scheme_name: "Word · 通用",
        },
      ],
    };
    const schemes = {
      items: [
        { id: 1, name: "PDF · 通用", file_type: "pdf" as const },
        { id: 3, name: "Excel · 表格", file_type: "excel" as const },
      ],
    };
    expect(resolveSchemeId("pdf", defaultSchemes, schemes)).toBe(5);
    expect(resolveSchemeId("word", defaultSchemes, schemes)).toBe(2);
    expect(resolveSchemeId("excel", { items: [] }, schemes)).toBe(3);
    expect(resolveSchemeId("image", { items: [] }, schemes)).toBeNull();
  });
});

describe("buildBatchPayload", () => {
  it("设置元数据", () => {
    expect(buildBatchPayload("set_metadata", [1, 2], { metadata: { 部门: "财务" } })).toEqual({
      action: "set_metadata",
      doc_ids: [1, 2],
      metadata: { 部门: "财务" },
    });
  });

  it("移动到文件夹（含未分类）", () => {
    expect(buildBatchPayload("move", [1], { folderId: null })).toEqual({
      action: "move",
      doc_ids: [1],
      folder_id: null,
    });
  });

  it("重新解析带方式与方案", () => {
    expect(buildBatchPayload("reparse", [1, 2], { mode: "rechunk", schemeId: 9 })).toEqual({
      action: "reparse",
      doc_ids: [1, 2],
      mode: "rechunk",
      scheme_id: 9,
    });
  });

  it("启用只带动作与 ID", () => {
    expect(buildBatchPayload("enable", [3])).toEqual({ action: "enable", doc_ids: [3] });
  });
});

describe("buildDocumentListParams", () => {
  it("拼装筛选参数，未分类/全部项不下发", () => {
    const params = buildDocumentListParams(
      {
        folderId: 3,
        statusFilter: "edited",
        fileType: "pdf",
        schemeId: 5,
        keyword: "制度",
        metadata: { field_id: 2, operator: "eq", value: "财务" },
      },
      2,
      20,
    );
    expect(params.folder_id).toBe(3);
    expect(params.include_sub).toBe(true);
    expect(params.edited).toBe(true);
    expect(params.status).toBeUndefined();
    expect(params.file_type).toBe("pdf");
    expect(params.scheme_id).toBe(5);
    expect(params.keyword).toBe("制度");
    expect(params.metadata).toBe('{"field_id":2,"operator":"eq","value":"财务"}');
    expect(params.page).toBe(2);
    expect(params.page_size).toBe(20);
  });

  it("失败筛选用 status=failed", () => {
    const params = buildDocumentListParams(
      { folderId: "all", statusFilter: "failed", fileType: "all", schemeId: "all", keyword: "", metadata: null },
      1,
      10,
    );
    expect(params.status).toBe("failed");
    expect(params.folder_id).toBeUndefined();
  });

  it("未分类用 uncategorized=true，不下发 folder_id", () => {
    const params = buildDocumentListParams(
      { folderId: "uncategorized", statusFilter: "all", fileType: "all", schemeId: "all", keyword: "", metadata: null },
      1,
      10,
    );
    expect(params.uncategorized).toBe(true);
    expect(params.folder_id).toBeUndefined();
    expect(params.include_sub).toBeUndefined();
  });
});

describe("processingCounts", () => {
  it("从全库 status_counts 汇总失败与处理中", () => {
    expect(
      processingCounts({
        queued: 2,
        parsing: 1,
        chunking: 0,
        enhancing: 1,
        embedding: 3,
        done: 10,
        failed: 4,
      }),
    ).toEqual({ failed: 4, processing: 7 });
  });

  it("缺省计数按 0 处理", () => {
    expect(processingCounts(undefined)).toEqual({ failed: 0, processing: 0 });
    expect(processingCounts({ done: 5 })).toEqual({ failed: 0, processing: 0 });
  });
});
