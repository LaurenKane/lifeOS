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

// Re-export a small typed fetch wrapper
export const apiGet = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const res = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }

  return res.json() as Promise<T>;
};

export const apiPost = async <T, R>(path: string, body: T, init?: RequestInit): Promise<R> => {
  const res = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }

  return res.json() as Promise<R>;
};

export const apiPut = async <T, R>(path: string, body: T, init?: RequestInit): Promise<R> => {
  const res = await fetch(`${baseUrl}${path}`, {
    method: "PUT",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }

  return res.json() as Promise<R>;
};

export const apiDelete = async <R>(path: string, init?: RequestInit): Promise<R> => {
  const res = await fetch(`${baseUrl}${path}`, {
    method: "DELETE",
    ...init,
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }

  return res.json() as Promise<R>;
};

// —————————————————————————————————————————————
// Validation helpers (Zod) for API responses
// —————————————————————————————————————————————

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