import { Card, Empty, Segmented, Typography } from "antd";
import dayjs from "dayjs";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatCount, formatToken } from "../../lib/numberFormat";
import type { UsageDailyItem } from "./api";

type Metric = "calls" | "tokens";

interface UsageTrendProps {
  items: UsageDailyItem[];
  loading: boolean;
}

function axisLabel(date: string): string {
  const text = dayjs(date).format("MM-DD");
  return date === dayjs().format("YYYY-MM-DD") ? `${text}（今天）` : text;
}

/** 按天趋势：柱状图，可切换调用次数 / Token（原型 #v-usage 的 u-trend）。 */
export function UsageTrend({ items, loading }: UsageTrendProps) {
  const [metric, setMetric] = useState<Metric>("calls");

  const data = useMemo(
    () =>
      items.map((item) => ({
        date: item.date,
        calls: item.calls,
        tokens: item.input_tokens + item.output_tokens,
      })),
    [items],
  );

  return (
    <Card
      style={{ marginTop: 16 }}
      title="按天趋势"
      extra={
        <Segmented
          value={metric}
          onChange={(value) => setMetric(value as Metric)}
          options={[
            { label: "调用次数", value: "calls" },
            { label: "Token", value: "tokens" },
          ]}
        />
      }
    >
      {data.length === 0 && !loading ? (
        <Empty description="所选范围内没有调用记录" />
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="date" tickFormatter={axisLabel} fontSize={12} />
            <YAxis
              fontSize={12}
              width={72}
              tickFormatter={(value: number) =>
                metric === "calls" ? formatCount(value) : formatToken(value)
              }
            />
            <Tooltip
              labelFormatter={(date) => dayjs(date as string).format("YYYY-MM-DD")}
              formatter={(value) =>
                metric === "calls"
                  ? [formatCount(Number(value)), "调用次数"]
                  : [formatToken(Number(value)), "Token"]
              }
            />
            <Bar dataKey={metric} fill="#1677ff" maxBarSize={24} />
          </BarChart>
        </ResponsiveContainer>
      )}
      <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
        悬停柱子可查看当天数值。
      </Typography.Paragraph>
    </Card>
  );
}
