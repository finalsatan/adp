import type { TablePaginationConfig } from "antd";
import { useState } from "react";

/** 把 Ant Design Table 的分页与后端的 page、page_size、total 对上。 */
export function useTablePage(initialPageSize = 10) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialPageSize);

  const pagination = (total: number): TablePaginationConfig => ({
    current: page,
    pageSize,
    total,
    showSizeChanger: true,
    showTotal: (count) => `共 ${count} 条`,
    onChange: (nextPage, nextPageSize) => {
      if (nextPageSize !== pageSize) {
        setPageSize(nextPageSize);
        setPage(1);
        return;
      }
      setPage(nextPage);
    },
  });

  return { page, page_size: pageSize, pagination };
}
