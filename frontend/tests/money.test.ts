/**
 * Money: formatting signed minor units, and parsing what a person typed.
 *
 * These are the two places a financial UI can quietly lie. A sign that is
 * carried by position instead of by a glyph, or an amount divided by the wrong
 * power of ten, produce a number that looks right and is not. The rule under
 * test is ARCHITECTURE.md §6: signed INTEGER minor units, exponent from the
 * currency, never a float.
 */
import { describe, expect, it } from "vitest";
import {
  amountParts,
  currencyDecimals,
  formatMinorUnits,
  parseSignedAmount,
  todayIso,
} from "@/lib/money";

describe("formatMinorUnits — the sign is explicit", () => {
  it("shows money out with a real minus sign, not a hyphen", () => {
    // U+2212, not "-". A hyphen is the character this project already uses for
    // a range and for a bullet; a minus that looks like one is a misread away
    // from a wrong sign.
    expect(formatMinorUnits(-4050, "EUR")).toBe("−40.50 EUR");
    expect(formatMinorUnits(-4050, "EUR")).not.toContain("-");
  });

  it("shows money in with an explicit plus", () => {
    expect(formatMinorUnits(4050, "EUR")).toBe("+40.50 EUR");
  });

  it("gives zero no sign, because zero is neither in nor out", () => {
    expect(formatMinorUnits(0, "EUR")).toBe("0.00 EUR");
  });

  it("keeps minor units in minor units — −4050 is not −4050.00 of EUR", () => {
    expect(formatMinorUnits(-4050, "EUR")).not.toContain("4050");
  });
});

describe("formatMinorUnits — the exponent comes from the currency", () => {
  it("divides by 10^2 for a two-decimal currency", () => {
    expect(formatMinorUnits(123456, "USD")).toBe("+1,234.56 USD");
  });

  it("treats JPY as having no minor units, so 1200 is 1200", () => {
    // A hardcoded 10**2 would render ¥1200.00, which is a hundredfold error and
    // looks perfectly plausible on a statement.
    expect(currencyDecimals("JPY")).toBe(0);
    expect(formatMinorUnits(1200, "JPY")).toBe("+1,200 JPY");
    expect(formatMinorUnits(-1200, "JPY")).toBe("−1,200 JPY");
  });

  it("handles a three-decimal currency", () => {
    expect(currencyDecimals("KWD")).toBe(3);
    expect(formatMinorUnits(-1234, "KWD")).toBe("−1.234 KWD");
  });

  it("normalises the code and groups thousands", () => {
    expect(formatMinorUnits(-1234567, "eur")).toBe("−12,345.67 EUR");
    expect(formatMinorUnits(100000000, "EUR")).toBe("+1,000,000.00 EUR");
  });

  it("refuses a fractional minor unit instead of rounding it", () => {
    // A float in this position is a value that should never have left the
    // ledger. Rounding it here would hide that rather than surface it.
    expect(() => formatMinorUnits(-40.5, "EUR")).toThrow(TypeError);
    expect(() => formatMinorUnits(Number.NaN, "EUR")).toThrow(TypeError);
  });
});

describe("amountParts", () => {
  it("splits sign, digits and code for layout", () => {
    expect(amountParts(-4050, "EUR")).toEqual({ sign: "−", digits: "40.50", code: "EUR" });
    expect(amountParts(0, "EUR")).toEqual({ sign: "", digits: "0.00", code: "EUR" });
  });
});

describe("parseSignedAmount — what gets typed is what gets sent", () => {
  it("parses money out and says so in the minor units", () => {
    const parsed = parseSignedAmount("-40.50", "EUR");
    expect(parsed).toEqual({ ok: true, value: "-40.50", minor: -4050 });
  });

  it("parses money in", () => {
    expect(parseSignedAmount("40.50", "EUR")).toEqual({
      ok: true,
      value: "40.50",
      minor: 4050,
    });
  });

  it("accepts a comma as the decimal separator and normalises it on the wire", () => {
    expect(parseSignedAmount("-40,50", "EUR")).toEqual({
      ok: true,
      value: "-40.50",
      minor: -4050,
    });
  });

  it("pads a short fraction to the currency's exponent", () => {
    expect(parseSignedAmount("-40.5", "EUR")).toEqual({
      ok: true,
      value: "-40.50",
      minor: -4050,
    });
  });

  it("refuses more decimals than the currency has, instead of rounding", () => {
    // The server would round this silently. Saying so here is the difference
    // between a user who typed 40.501 and a ledger holding 40.50 without
    // anybody knowing.
    const parsed = parseSignedAmount("-40.501", "EUR");
    expect(parsed.ok).toBe(false);
    expect(parsed.ok === false && parsed.error).toMatch(/2 decimal places/);

    const yen = parseSignedAmount("-1200.5", "JPY");
    expect(yen.ok).toBe(false);
  });

  it("rejects anything that is not a plain signed number", () => {
    for (const input of ["", "  ", "forty", "1.2.3", "1e3", "--4", "4-"]) {
      expect(parseSignedAmount(input, "EUR").ok, input).toBe(false);
    }
  });

  it("normalises a negative zero away", () => {
    // "-0.00 EUR" is a contradiction on a statement.
    expect(parseSignedAmount("-0.00", "EUR")).toEqual({
      ok: true,
      value: "0.00",
      minor: 0,
    });
  });
});

describe("todayIso", () => {
  it("uses local time, so the day does not shift across the UTC boundary", () => {
    // 23:30 local on the 1st is often the 2nd in UTC. toISOString() would post
    // the transaction on the wrong day.
    expect(todayIso(new Date(2026, 9, 1, 23, 30))).toBe("2026-10-01");
    expect(todayIso(new Date(2026, 0, 5, 0, 5))).toBe("2026-01-05");
  });
});
