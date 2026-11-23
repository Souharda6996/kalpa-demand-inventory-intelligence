import { describe, expect, it } from "vitest";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";

describe("format", () => {
  it("groups INR the Indian way", () => {
    expect(formatMoney(123456)).toBe("₹1,23,456");
    expect(formatNumber(1234567)).toBe("12,34,567");
  });
  it("formats USD and percentages", () => {
    expect(formatMoney(1234, "USD")).toBe("$1,234");
    expect(formatPct(0.1234)).toBe("12.3%");
  });
});
