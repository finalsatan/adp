import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { MetadataField } from "../api";
import type { FolderOut } from "../documents/api";
import { RetrievalConfigForm, type HitTestFilters } from "./RetrievalConfigForm";
import type { Model, RetrievalConfigInput } from "./api";

const FOLDERS = [
  { id: 3, kb_id: 1, parent_id: null, path: "财务制度", name: "财务制度", doc_count: 2 },
] as unknown as FolderOut[];

const FIELDS = [
  { id: 1, kb_id: 1, name: "部门", type: "select", options: ["财务", "人事"], doc_count: 0 },
  { id: 2, kb_id: 1, name: "年份", type: "number", options: null, doc_count: 0 },
] as unknown as MetadataField[];

const MODELS = [{ id: 9, name: "gte-rerank" }] as unknown as Model[];

function config(overrides: Partial<RetrievalConfigInput> = {}): RetrievalConfigInput {
  return {
    mode: "hybrid",
    vector_weight: 0.7,
    fulltext_weight: 0.3,
    top_k: 20,
    score_threshold: 0.35,
    rerank: { enabled: true, model_id: 9, top_n: 5 },
    ...overrides,
  };
}

function Harness({ initial, editable = true }: { initial: RetrievalConfigInput; editable?: boolean }) {
  const [value, setValue] = useState(initial);
  const [filters, setFilters] = useState<HitTestFilters>({
    folderId: "all",
    metadataOp: "and",
    conditions: [],
  });
  return (
    <RetrievalConfigForm
      config={value}
      onConfigChange={setValue}
      filters={filters}
      onFiltersChange={setFilters}
      folders={FOLDERS}
      metadataFields={FIELDS}
      rerankModels={MODELS}
      editable={editable}
      saving={false}
      onReset={vi.fn()}
      onSaveDefault={vi.fn()}
    />
  );
}

describe("RetrievalConfigForm", () => {
  it("表单默认值来自知识库默认配置", () => {
    render(<Harness initial={config()} />);

    expect(screen.getByDisplayValue("20")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.35")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.7")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.3")).toBeInTheDocument();
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
    expect(screen.getByText("gte-rerank")).toBeInTheDocument();
  });

  it("第三期不渲染相邻分块扩展控件", () => {
    render(<Harness initial={config()} />);
    expect(screen.queryByText(/相邻分块扩展/)).toBeNull();
  });

  it("混合方式权重和不为 1 时给出校验提示并禁用保存", () => {
    render(<Harness initial={config({ vector_weight: 0.7, fulltext_weight: 0.5 })} />);

    expect(screen.getByText("混合检索时向量权重与全文权重之和必须为 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存为默认配置/ })).toBeDisabled();
  });

  it("权重和为 1 时可以保存", () => {
    render(<Harness initial={config()} />);
    expect(screen.getByRole("button", { name: /保存为默认配置/ })).toBeEnabled();
  });

  it("非混合方式不显示权重输入", () => {
    render(<Harness initial={config({ mode: "vector" })} />);
    expect(screen.queryByDisplayValue("0.7")).toBeNull();
    expect(screen.getByText("向量")).toBeInTheDocument();
  });

  it("没有编辑权限时禁用保存", () => {
    render(<Harness initial={config()} editable={false} />);
    expect(screen.getByRole("button", { name: /保存为默认配置/ })).toBeDisabled();
  });

  it("添加元数据条件后出现一行且第二个条件时才显示且/或", () => {
    render(<Harness initial={config()} />);

    fireEvent.click(screen.getByRole("button", { name: /添加条件/ }));
    // 第一个条件默认选中第一个字段
    expect(screen.getByText("部门")).toBeInTheDocument();
    expect(screen.queryByText("且")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /添加条件/ }));
    expect(screen.getByText("且")).toBeInTheDocument();
    expect(screen.getByText("或")).toBeInTheDocument();
  });
});
