/* Imports hook — the batch list and the upload.
 *
 * Same two corrections as the review hook: a failed fetch is kept rather than
 * turned into "no import batches", and the path is `/v1/imports` because every
 * router hangs off `/api/v1`.
 *
 * The upload is a MULTIPART POST, which is why it does not go through
 * `apiPost`. `apiClient`'s `request` sets `Content-Type: application/json` on
 * every call, and a multipart body needs the boundary the browser generates —
 * naming the content type by hand produces a request the server cannot parse,
 * and the failure it reports ("There was an error parsing the body") names the
 * proxy rather than the cause. So the boundary is left to `fetch` here, and
 * `ApiError` is constructed directly so a 4xx keeps the server's own words. */
import React from "react";
import { API_PATH, ApiError, apiGet, baseUrl, describeError, expectSchema } from "@/lib/apiClient";
import { ImportSummarySchema } from "./types";
import type { FileProvider, ImportBatch, ImportSummary, SectionAccountIds } from "./types";

export const useImports = () => {
  const [data, setData] = React.useState<ImportBatch[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [attempt, setAttempt] = React.useState(0);

  React.useEffect(() => {
    apiGet<ImportBatch[]>(`${API_PATH}/imports`)
      .then(setData)
      .catch((cause: unknown) => setError(describeError(cause)))
      .finally(() => setLoading(false));
  }, [attempt]);

  /* The list refetches after an upload, because an import that persisted 200
   * rows and left the batch list showing the previous 19 is a page claiming
   * something untrue about its own contents. */
  const reload = React.useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  return { data, loading, error, reload };
};

/** What one upload needs. Everything here is optional except the file. */
export type ImportUploadInput = {
  file: File;
  provider: FileProvider;
  /** The account the whole file belongs to, when it belongs to one. */
  accountId?: number | null;
  /** The account on the OTHER side of a card payment, when the caller names it.
   * Left alone otherwise, so the server's saved mapping is what decides. */
  cardPaymentAccountId?: number | null;
  /** Revolut only: section key -> local account id. Sent as one JSON form
   * field named `section_account_ids`; see `POST /imports/file`. */
  sectionAccountIds?: SectionAccountIds;
};

/**
 * Run one import and persist what it parsed.
 *
 * The JSON mapping is `JSON.stringify`d into a single form field rather than
 * sent as repeated `section_account_ids[]` entries, because the backend reads it
 * with `json.loads` on one string — a repeated field would arrive as
 * `FormData.get()`'s first value and silently drop every section after the
 * first, which is a mis-attributed statement rather than a visible error.
 *
 * An empty mapping is sent as an absent field, not as `{}`. The backend treats
 * both the same way (`_parse_section_account_ids` returns None for a blank
 * string), and absent is the one that cannot be misread.
 */
export const uploadImportFile = async (input: ImportUploadInput): Promise<ImportSummary> => {
  const form = new FormData();
  form.append("file", input.file);
  form.append("provider", input.provider);
  if (input.accountId != null) {
    form.append("account_id", String(input.accountId));
  }
  if (input.cardPaymentAccountId != null) {
    form.append("card_payment_account_id", String(input.cardPaymentAccountId));
  }
  const mapping = input.sectionAccountIds ?? {};
  if (Object.keys(mapping).length > 0) {
    form.append("section_account_ids", JSON.stringify(mapping));
  }

  const res = await fetch(`${baseUrl}${API_PATH}/imports/file`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }

  return expectSchema(ImportSummarySchema, await res.json(), "an import summary");
};

export type ImportsState = ReturnType<typeof useImports>;

/* The provider owns the single fetch; pages read through this context. */
export const ImportsContext = React.createContext<ImportsState | null>(null);

export const useImportsContext = () => {
  const state = React.useContext(ImportsContext);
  if (state === null) {
    throw new Error("useImportsContext must be used within an <ImportsProvider>");
  }
  return state;
};
