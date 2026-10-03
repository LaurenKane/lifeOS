/**
 * Money — formatting signed minor units, and parsing what a person typed.
 *
 * ARCHITECTURE.md §6: an amount is a SIGNED INTEGER of minor units and
 * `currency.decimals` is the authoritative exponent. Both functions below are
 * integer-and-string arithmetic end to end. There is no `parseFloat`, no
 * `toFixed`, and no `* 100` anywhere in this file, because each of those
 * loses or invents precision on the way to the ledger.
 *
 * `formatMinorUnits` renders with an explicit sign on both sides — `−40.50` and
 * `+40.50` — because an amount whose direction is carried only by position or
 * only by colour is one misread away from being a wrong number. Zero carries
 * no sign: it is neither money in nor money out, and `+0.00` is noise.
 *
 * THE EXPONENT IS A MIRROR, NOT THE AUTHORITY
 * -------------------------------------------
 * `finance.currency.decimals` in the database is the authority (§6). There is
 * no endpoint that exposes the currency table — `GET /accounts/types` returns
 * the account taxonomy, not money — so the client cannot ask. The table below
 * is therefore a DISPLAY-SIDE MIRROR of the ISO 4217 exponents, used only to
 * decide where the decimal point goes in a string, and it defaults to 2. The
 * server re-derives every amount from its own currency row and refuses
 * anything it cannot state, so a wrong entry here shows a wrong number but can
 * never store one.
 */

/** ISO 4217 codes whose minor-unit exponent is not 2. */
const EXPONENTS: Readonly<Record<string, number>> = {
  BHD: 3,
  CLF: 4,
  IQD: 3,
  JOD: 3,
  JPY: 0,
  KMF: 0,
  KRW: 0,
  KWD: 3,
  OMR: 3,
  PYG: 0,
  RWF: 0,
  TND: 3,
  UGX: 0,
  UYI: 0,
  VND: 0,
  VUV: 0,
  XAF: 0,
  XOF: 0,
  XPF: 0,
};

/** What an unknown code is assumed to be. The server still has the last word. */
export const DEFAULT_DECIMALS = 2;

/** Minor units per major unit for `code`, e.g. 0 for JPY, 3 for KWD. */
export const currencyDecimals = (code: string): number =>
  EXPONENTS[code.trim().toUpperCase()] ?? DEFAULT_DECIMALS;

/** U+2212 MINUS SIGN, not a hyphen. It is the same width as a digit in most UI
 * fonts, so a column of amounts stays aligned, and it cannot be mistaken for
 * a dash in running text. */
const MINUS = "−";

/** Group the integer part in threes: `1234567` → `1,234,567`. */
const groupThousands = (digits: string): string =>
  digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/**
 * `(-4050, "EUR")` → `"−40.50 EUR"`.
 *
 * `amountMinor` is an integer count of minor units. Non-integers are rejected
 * rather than rounded: a fractional minor unit is not a display problem, it is
 * a value that should never have left the ledger, and quietly rounding it
 * would hide that.
 */
export const formatMinorUnits = (amountMinor: number, currency: string): string => {
  if (!Number.isInteger(amountMinor)) {
    throw new TypeError(
      `formatMinorUnits expects integer minor units, got ${amountMinor}`,
    );
  }

  const code = currency.trim().toUpperCase();
  const decimals = currencyDecimals(code);
  const digits = Math.abs(amountMinor).toString().padStart(decimals + 1, "0");
  const whole = groupThousands(digits.slice(0, digits.length - decimals));
  const fraction = decimals === 0 ? "" : `.${digits.slice(digits.length - decimals)}`;
  const sign = amountMinor < 0 ? MINUS : amountMinor > 0 ? "+" : "";

  return `${sign}${whole}${fraction} ${code}`;
};

/** The same value split for layout: a display layer needs the currency code in
 * a quieter weight than the digits, and `formatMinorUnits` returns one string. */
export type AmountParts = { sign: string; digits: string; code: string };

/** `(-4050, "EUR")` → `{ sign: "−", digits: "40.50", code: "EUR" }`. */
export const amountParts = (amountMinor: number, currency: string): AmountParts => {
  const [body = "", code = ""] = formatMinorUnits(amountMinor, currency).split(" ");
  const sign = body.startsWith(MINUS) || body.startsWith("+") ? body.slice(0, 1) : "";
  return { sign, digits: sign === "" ? body : body.slice(1), code };
};

/** A parsed amount: the canonical wire string, and the minor units it means. */
export type ParsedAmount =
  | { ok: true; value: string; minor: number }
  | { ok: false; error: string };

/**
 * Parse what a person typed into the canonical major-unit decimal string the
 * API wants, plus the minor units it will become.
 *
 * `POST /transactions` takes `amount` as a STRING, signed: `"-40.50"` is money
 * out, `"40.50"` is money in. That string is stored as typed in `raw_data`, so
 * what is sent is shown here as well as sent to the server — the form echoes
 * the exact payload rather than a re-rendered version of it.
 *
 * A comma is accepted as the decimal separator (`-40,50`), because this ledger
 * is denominated in EUR and `40,50` is how the amount is written down. It is
 * normalised to a dot on the way out.
 */
export const parseSignedAmount = (
  input: string,
  currency: string,
): ParsedAmount => {
  const code = currency.trim().toUpperCase();
  const decimals = currencyDecimals(code);
  const cleaned = input.trim().replace(/\s+/g, "").replace(",", ".");

  if (cleaned === "") {
    return { ok: false, error: "Enter an amount." };
  }

  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(cleaned);
  if (match === null) {
    return {
      ok: false,
      error: "Use a plain number with an optional sign, for example -40.50.",
    };
  }

  const [, sign = "", whole = "0", fraction = ""] = match;
  if (fraction.length > decimals) {
    return {
      ok: false,
      error:
        decimals === 0
          ? `${code} has no minor units, so the amount is a whole number.`
          : `${code} has ${decimals} decimal places, not ${fraction.length}.`,
    };
  }
  if (Number(whole) > 999_999_999_999) {
    return { ok: false, error: "That is too large to record." };
  }

  const padded = fraction.padEnd(decimals, "0");
  const minor =
    Number(whole) * 10 ** decimals + (padded === "" ? 0 : Number(padded));
  const signed = sign === "-" ? -minor : minor;

  // A negative zero is a contradiction on a statement; normalise it away.
  const value =
    signed === 0
      ? `${minor}${decimals === 0 ? "" : `.${padded}`}`
      : `${sign === "-" ? "-" : ""}${whole}${decimals === 0 ? "" : `.${padded}`}`;

  return { ok: true, value, minor: signed === 0 ? 0 : signed };
};

/** Today's date as `YYYY-MM-DD`, from the browser's clock, in local time.
 * `toISOString` would shift the day for anyone east or west of UTC, which is
 * the kind of off-by-one that books a transaction on the wrong date. */
export const todayIso = (now: Date = new Date()): string => {
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
};
