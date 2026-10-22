import { Empty } from "antd";

import { PageHeader } from "../../components/PageHeader";

/** 第一期的“我的应用”：还没有应用，只显示空状态；第四期替换为真实列表。 */
export default function MyAppsPage() {
  return (
    <>
      <PageHeader title="我的应用" description="你能使用的、已发布的应用。" />
      <Empty description="暂无可用的应用" />
    </>
  );
}
