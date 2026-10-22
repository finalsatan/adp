import dayjs from "dayjs";

/** 按浏览器时区显示，如 2025-10-19 11:03。 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) {
    return "-";
  }
  return dayjs(iso).format("YYYY-MM-DD HH:mm");
}

/** 列表里的相对时间：今天 09:12 / 昨天 17:05 / 10-19 11:03。 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) {
    return "-";
  }
  const time = dayjs(iso);
  const now = dayjs();
  const clock = time.format("HH:mm");
  if (time.isSame(now, "day")) {
    return `今天 ${clock}`;
  }
  if (time.isSame(now.subtract(1, "day"), "day")) {
    return `昨天 ${clock}`;
  }
  return time.format("MM-DD HH:mm");
}
