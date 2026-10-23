/** 普通计数：千分位、不加单位。用于调用次数、失败次数等（照原型 #v-usage）。 */
export function formatCount(value: number): string {
  return Math.round(value).toLocaleString("en-US");
}

/**
 * Token 数：大于等于 1 万时以“万”为单位（如 61,200,000 → “6,120 万”），
 * 小于 1 万时按原数显示（如 9,999 → “9,999”）。照原型 #v-usage。
 */
export function formatToken(value: number): string {
  if (value < 10000) {
    return formatCount(value);
  }
  const wan = Math.round((value / 10000) * 10) / 10;
  const text = Number.isInteger(wan) ? wan.toLocaleString("en-US") : wan.toFixed(1);
  return `${text} 万`;
}

/** 占比：该行调用次数 ÷ 合计，合计为 0 时显示“—”。 */
export function formatShare(value: number, total: number): string {
  if (total <= 0) {
    return "—";
  }
  return `${((value / total) * 100).toFixed(1)}%`;
}

/** 占比进度条的宽度（0–100），合计为 0 时为 0。 */
export function sharePercent(value: number, total: number): number {
  if (total <= 0) {
    return 0;
  }
  return Math.min(100, (value / total) * 100);
}
