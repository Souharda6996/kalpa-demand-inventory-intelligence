/** Indian digit grouping (1,23,456) with the org currency. */
export function formatMoney(value: number, currency: "INR" | "USD" = "INR"): string {
  return new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-IN").format(value);
}

export function formatPct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}
