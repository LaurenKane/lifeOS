/**
 * API client base — fetch wrapper that reads the base URL from
 * import.meta.env.VITE_API_BASE_URL with a sane default of /api.
 *
 * No hardcoded host. Consumers import `apiGet`, `apiPost`, etc. from
 * this module; the base URL is injected via Vite's `import.meta.env`
 * at build time (default /api for local/dev against the FastAPI backend).
 */
import { z } from "zod";

/* The base URL may be a root-relative path ("/api" — the local/dev
 * default, proxied to the FastAPI backend) or an absolute URL
 * ("https://api.example.test/v1" in deployed environments). A plain
 * z.string().url() would reject the relative default, so accept both
 * and reject anything that is neither. */
const BaseUrlSchema = z
  .string()
  .refine(
    (value) => value.startsWith("/") || URL.canParse(value),
    { message: "must be a root-relative path or an absolute URL" },
  )
  .default("/api");

type BaseUrl = z.infer<typeof BaseUrlSchema>;

export const baseUrl: BaseUrl = BaseUrlSchema.parse(import.meta.env.VITE_API_BASE_URL);

/**
 * The version segment of every finance path.
 *
 * `backend/config.py` mounts every router under `API_V1_PREFIX` = `/api/v1`,
 * and the base URL above already ends in `/api`, so a feature path is
 * `API_PATH + "/accounts"` → `/api/v1/accounts`. It lives here, next to the
 * base URL it composes with, because these two are one fact split across two
 * files and getting the pairing wrong is a 404 that looks like an empty
 * database.
 */
export const API_PATH = "/v1";

/**
 * A failed request, with the parts of it a UI can actually use.
 *
 * `message` keeps the historical `API error <status>: <body>` shape, because a
 * raw body is the last thing that helps when a server sends prose. `detail` is
 * the part of that body worth showing a person: FastAPI's `detail` string, or
 * its 422 validation list rendered one field per line.
 *
 * The whole point is that a refusal is never swallowed. `status` is what lets
 * a caller say "the ledger refused this, and here is its own words" instead of
 * "something went wrong".
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, body: string) {
    super(`API error ${status}: ${body}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = humanDetail(body);
  }
}

/**
 * The sentence to show a person for any thrown value.
 *
 * A refused request keeps the server's own words. A request that never
 * arrived — the backend is down, the dev server is not proxying — has no
 * server words, and saying "Failed to fetch" helps nobody, so the case is
 * named instead.
 */
export const describeError = (error: unknown): string => {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof TypeError) {
    return "Could not reach the API. Is the backend running on port 8000?";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
};

/** The most truthful short string available for an error response body. */
const humanDetail = (body: string): string => {
  const trimmed = body.trim();
  if (trimmed === "") {
    return "The server sent no explanation.";
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
      if (Array.isArray(detail)) {
        return detail
          .map((entry) => {
            const field = Array.isArray(entry) ? entry[1] : undefined;
            const message =
              typeof entry === "object" && entry !== null && "msg" in entry
                ? String((entry as { msg: unknown }).msg)
                : null;
            if (message === null) {
              return "invalid value";
            }
            return typeof field === "string"
              ? `${field.split(".").pop() ?? field}: ${message}`
              : message;
          })
          .join("\n");
      }
    }
  } catch {
    // Not JSON. The body is the message, which is the normal case for a proxy
    // or a crash: return it rather than pretending the server said nothing.
  }
  return trimmed;
};

/** One request, one place where the response is turned into either a value or
 * an `ApiError`. `apiGet`/`apiPost`/... below are thin wrappers over this so
 * that PATCH and 204 handling cannot drift between them. */
async function request<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  // 204 No Content has no body, and `res.json()` on an empty body throws a
  // SyntaxError that looks nothing like the request having succeeded.
  if (res.status === 204) {
    return undefined as T;
  }

  return res.json() as Promise<T>;
}

// Re-export a small typed fetch wrapper
export const apiGet = async <T>(path: string, init?: RequestInit): Promise<T> =>
  request<T>(path, { ...init, method: "GET" });

export const apiPost = async <T, R>(path: string, body: T, init?: RequestInit): Promise<R> =>
  request<R>(path, { ...init, method: "POST", body: JSON.stringify(body) });

export const apiPut = async <T, R>(path: string, body: T, init?: RequestInit): Promise<R> =>
  request<R>(path, { ...init, method: "PUT", body: JSON.stringify(body) });

/** PATCH is not a typo for PUT here: `ManualTransactionUpdate` treats an absent
 * field as "leave alone", so a PUT would make "do not change the category" and
 * "clear the category" indistinguishable. */
export const apiPatch = async <T, R>(path: string, body: T, init?: RequestInit): Promise<R> =>
  request<R>(path, { ...init, method: "PATCH", body: JSON.stringify(body) });

export const apiDelete = async <R>(path: string, init?: RequestInit): Promise<R> =>
  request<R>(path, { ...init, method: "DELETE" });

// —————————————————————————————————————————————
// Validation helpers (Zod) for API responses
// —————————————————————————————————————————————

/**
 * Check a response against the hand-maintained schema for it, or fail loudly.
 *
 * There is no generated client, so these schemas are the contract. Parsing is
 * what makes them one: an amount that arrives as a float, or a status the build
 * has never heard of, becomes a stated error instead of a `NaN` in an amount
 * column. The failure names the field so the mismatch can be found in one step.
 */
export const expectSchema = <T>(
  schema: z.ZodType<T>,
  value: unknown,
  what: string,
): T => {
  const result = schema.safeParse(value);
  if (result.success) {
    return result.data;
  }
  const issue = result.error.issues[0];
  const where = issue === undefined ? "" : ` at ${issue.path.join(".")}`;
  throw new Error(
    `The server's ${what} does not match this build's contract${where}: ` +
      `${issue?.message ?? "unrecognised shape"}`,
  );
};


/** Amounts are signed integer MINOR units (e.g. cents) — never floats.
 * `currency` is an ISO-4217 code whose decimal count is the
 * authoritative exponent. See ARCHITECTURE.md §6. */
export const AmountMinor = z.number().int();
export type AmountMinor = z.infer<typeof AmountMinor>;

export const Money = z.object({
  amountMinor: AmountMinor,
  currency: z.string().length(3),
});
export type Money = z.infer<typeof Money>;
