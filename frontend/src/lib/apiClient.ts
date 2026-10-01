/**
 * API client base — fetch wrapper that reads the base URL from
 * import.meta.env.VITE_API_BASE_URL with a sane default of /api.
 *
 * No hardcoded host. Consumers import `apiGet`, `apiPost`, etc. from
 * this module; the base URL is injected via Vite's `import.meta.env`
 * at build time (default /api for local/dev against the FastAPI backend).
 */
import { z } from "zod";

const BaseUrlSchema = z.string().url().default("/api");

type BaseUrl = z.infer<typeof BaseUrlSchema>;

const baseUrl: BaseUrl = BaseUrlSchema.parse(
  // $FlowFixMe — validated at module init via z.parse, default is safe
  import.meta.env.VITE_API_BASE_URL
);

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

export const Amount = z.number().int();
export type Amount = z.infer<typeof Amount>;

export const Money = z.object({
  amount: Amount,
  currency: z.string().length(3),
});
export type Money = z.infer<typeof Money>;