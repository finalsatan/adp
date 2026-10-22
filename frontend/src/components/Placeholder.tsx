import { Empty } from "antd";

import { PageHeader } from "./PageHeader";

interface PlaceholderProps {
  title: string;
  description?: string;
}

/** 占位页面，由后续前端任务卡替换成真实内容。 */
export function Placeholder({ title, description }: PlaceholderProps) {
  return (
    <>
      <PageHeader title={title} description={description} />
      <Empty description="功能开发中" />
    </>
  );
}
