/**
 * apiClient base-URL resolution.
 *
 * The module reads import.meta.env.VITE_API_BASE_URL once at module
 * init, so each case resets the module registry and stubs the env var
 * before re-importing. This asserts the two behaviours the rest of the
 * app depends on:
 *
 *   1. With no env var set, the base URL defaults to "/api".
 *   2. An absolute URL from the env is used verbatim as the prefix.
 *
 * A root-relative default matters: the app is served from the same
 * origin as the API in dev/prod, so requests go to /api/... not to a
 * hardcoded host.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Re-import a fresh copy of apiClient with VITE_API_BASE_URL set. */
async function loadApiClient(env: string | undefined) {
  vi.resetModules();
  if (env === undefined) {
    vi.stubEnv("VITE_API_BASE_URL", undefined as unknown as string);
  } else {
    vi.stubEnv("VITE_API_BASE_URL", env);
  }
  return await import("@/lib/apiClient");
}

describe("apiClient base URL", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_API_BASE_URL", undefined as unknown as string);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to /api when VITE_API_BASE_URL is not set", async () => {
    const { baseUrl } = await loadApiClient(undefined);
    expect(baseUrl).toBe("/api");
  });

  it("uses an absolute base URL from VITE_API_BASE_URL verbatim", async () => {
    const { baseUrl } = await loadApiClient("https://api.example.test/v1");
    expect(baseUrl).toBe("https://api.example.test/v1");
  });

  it("accepts a root-relative base URL from the environment", async () => {
    const { baseUrl } = await loadApiClient("/backend/api");
    expect(baseUrl).toBe("/backend/api");
  });

  it("prefixes request paths with the base URL", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_API_BASE_URL", "https://api.example.test/v1");
    const { apiGet } = await import("@/lib/apiClient");

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await apiGet<{ ok: boolean }>("/finance/transactions");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://api.example.test/v1/finance/transactions",
    );
  });

  it("throws with the status and body on a non-OK response", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_API_BASE_URL", "/api");
    const { apiGet } = await import("@/lib/apiClient");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        text: () => Promise.resolve("backend down"),
      }),
    );

    await expect(apiGet("/finance/budgets")).rejects.toThrow(
      "API error 503: backend down",
    );
  });
});

describe("Money typing", () => {
  it("rejects a fractional amount, enforcing integer minor units", async () => {
    const { Money } = await loadApiClient(undefined);

    // 10.5 major units would be a float; minor units must be integers.
    expect(Money.safeParse({ amountMinor: 10.5, currency: "EUR" }).success).toBe(false);
    expect(Money.safeParse({ amountMinor: 1050, currency: "EUR" }).success).toBe(true);
  });

  it("requires a 3-letter currency code", async () => {
    const { Money } = await loadApiClient(undefined);

    expect(Money.safeParse({ amountMinor: 1050, currency: "EURO" }).success).toBe(false);
    expect(Money.safeParse({ amountMinor: 1050, currency: "EUR" }).success).toBe(true);
  });
});
