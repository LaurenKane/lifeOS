/* Merchants hook — owns the merchant read, the alias read and the five writes.
 *
 * THE TWO LISTS ARE READ WHOLE AND SEPARATELY, ON PURPOSE.
 * `GET /merchants` and `GET /merchant-aliases` are two routers rather than one
 * nested resource, and the reason is recorded in `routes/merchants.py`: an
 * alias's merchant is optional, not its parent, so `/merchants/{id}/aliases`
 * would lie about the shape. The screen keeps that separation — two panels, two
 * lists, two sets of errors — rather than flattening them into one list where
 * an alias would look like a merchant.
 *
 * FAILURES ARE STATE, NOT EMPTINESS.
 * `.catch(() => setData([]))` is how a dead backend becomes "you have no
 * merchants" — and on this screen that is a lie a user would act on, because
 * the obvious next move is to create the merchant they were about to curate,
 * and the ledger will then refuse a duplicate name with a 409.
 *
 * A PATCH THAT FAILS CHANGES NOTHING, AND THE ROW SAYS SO.
 * `setMerchantCategory` replaces the row only with what the server handed
 * back, never with what the form asked for. A category picker that moved to a
 * new value and then reported a failure would be claiming a filing that the
 * ledger does not have.
 *
 * A DELETED MERCHANT TAKES ITS ALIASES WITH IT.
 * `merchant_alias.merchant_id` is `ON DELETE CASCADE`, so removing one row
 * removes every alias that pointed at it. The screen says so before it asks,
 * and the local filter drops the cascaded aliases in the same pass — otherwise
 * the alias panel would list aliases whose merchant it had just reported gone. */
import React from "react";
import {
  API_PATH,
  ApiError,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  describeError,
  expectSchema,
} from "@/lib/apiClient";
import type { Category, CategoryKind } from "@/features/finance/categories/types";
import { KIND_LABEL, KIND_NOTE } from "@/features/finance/categories/types";
import {
  MerchantAliasListSchema,
  MerchantAliasSchema,
  MerchantListSchema,
  MerchantSchema,
} from "./types";
import type {
  Merchant,
  MerchantAlias,
  MerchantAliasCreateRequest,
  MerchantCreateRequest,
} from "./types";

/** What a delete attempt actually did.
 *
 * The refused branch is kept rather than dropped: a 404 means the row was
 * already gone, which is a different fact from "it is deleted", and reporting
 * the two the same way would let a typo look like an edit. Nothing is removed
 * locally in that branch, so the row stays exactly where it was. */
export type DeleteOutcome =
  | { kind: "deleted" }
  | { kind: "refused"; status: number; message: string };

export type MerchantsState = {
  merchants: Merchant[];
  merchantsLoading: boolean;
  merchantsError: string | null;
  aliases: MerchantAlias[];
  aliasesLoading: boolean;
  aliasesError: string | null;
  reloadMerchants: () => void;
  reloadAliases: () => void;
  createMerchant: (input: MerchantCreateRequest) => Promise<Merchant>;
  /** `null` CLEARS the category. The key is always sent, because the server
   *  reads `model_fields_set` and an absent `category_id` means "leave it
   *  alone" — the opposite of what an empty control means here. */
  setMerchantCategory: (
    id: number,
    categoryId: number | null,
  ) => Promise<Merchant>;
  deleteMerchant: (id: number) => Promise<DeleteOutcome>;
  createAlias: (input: MerchantAliasCreateRequest) => Promise<MerchantAlias>;
  deleteAlias: (id: number) => Promise<DeleteOutcome>;
};

export const useMerchants = (): MerchantsState => {
  const [merchants, setMerchants] = React.useState<Merchant[]>([]);
  const [merchantsLoading, setMerchantsLoading] = React.useState(true);
  const [merchantsError, setMerchantsError] = React.useState<string | null>(null);
  const [merchantAttempt, setMerchantAttempt] = React.useState(0);

  const [aliases, setAliases] = React.useState<MerchantAlias[]>([]);
  const [aliasesLoading, setAliasesLoading] = React.useState(true);
  const [aliasesError, setAliasesError] = React.useState<string | null>(null);
  const [aliasAttempt, setAliasAttempt] = React.useState(0);

  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/merchants`)
      .then((payload) => {
        if (current) {
          setMerchants(expectSchema(MerchantListSchema, payload, "merchants"));
          setMerchantsError(null);
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setMerchantsError(describeError(cause));
        }
      })
      .finally(() => {
        if (current) {
          setMerchantsLoading(false);
        }
      });

    return () => {
      current = false;
    };
  }, [merchantAttempt]);

  React.useEffect(() => {
    let current = true;

    apiGet<unknown>(`${API_PATH}/merchant-aliases`)
      .then((payload) => {
        if (current) {
          setAliases(expectSchema(MerchantAliasListSchema, payload, "merchant aliases"));
          setAliasesError(null);
        }
      })
      .catch((cause: unknown) => {
        if (current) {
          setAliasesError(describeError(cause));
        }
      })
      .finally(() => {
        if (current) {
          setAliasesLoading(false);
        }
      });

    return () => {
      current = false;
    };
  }, [aliasAttempt]);

  /* Retrying resets the flags in the click rather than synchronously inside the
   * effect, for the reason `use-transactions.ts` gives: the effect makes the
   * request, the click is what starts it. */
  const reloadMerchants = React.useCallback(() => {
    setMerchantsError(null);
    setMerchantsLoading(true);
    setMerchantAttempt((n) => n + 1);
  }, []);

  const reloadAliases = React.useCallback(() => {
    setAliasesError(null);
    setAliasesLoading(true);
    setAliasAttempt((n) => n + 1);
  }, []);

  const createMerchant = React.useCallback(
    async (input: MerchantCreateRequest): Promise<Merchant> => {
      const created = await apiPost<unknown, unknown>(`${API_PATH}/merchants`, input);
      const merchant = expectSchema(MerchantSchema, created, "the new merchant");
      setMerchants((previous) => [...previous, merchant]);
      return merchant;
    },
    [],
  );

  const setMerchantCategory = React.useCallback(
    async (id: number, categoryId: number | null): Promise<Merchant> => {
      /* `apiPatch`, and the key is present even when its value is null. The
       * server reads `model_fields_set`, so this exact body is what clears the
       * category; `apiPut` would have made "unfile this" and "change nothing"
       * the same request. */
      const updated = await apiPatch<unknown, unknown>(
        `${API_PATH}/merchants/${id}`,
        { category_id: categoryId },
      );
      const merchant = expectSchema(MerchantSchema, updated, "the updated merchant");
      /* Replaced by what came back, not by what was asked for. The response is
       * the server's own account of the row, and using it means a later reload
       * cannot contradict this screen. */
      setMerchants((previous) => previous.map((row) => (row.id === id ? merchant : row)));
      return merchant;
    },
    [],
  );

  const deleteMerchant = React.useCallback(async (id: number): Promise<DeleteOutcome> => {
    try {
      await apiDelete<void>(`${API_PATH}/merchants/${id}`);
    } catch (cause) {
      return {
        kind: "refused",
        status: cause instanceof ApiError ? cause.status : 0,
        message: describeError(cause),
      };
    }
    /* Only after the server has confirmed it. The alias filter is in the same
     * pass as the merchant filter, and for the same reason: the cascade is a
     * fact about the database that the DELETE did not restate, so leaving the
     * aliases on screen would show rows whose merchant no longer exists. */
    setMerchants((previous) => previous.filter((row) => row.id !== id));
    setAliases((previous) => previous.filter((row) => row.merchant_id !== id));
    return { kind: "deleted" };
  }, []);

  const createAlias = React.useCallback(
    async (input: MerchantAliasCreateRequest): Promise<MerchantAlias> => {
      const created = await apiPost<unknown, unknown>(
        `${API_PATH}/merchant-aliases`,
        input,
      );
      const alias = expectSchema(MerchantAliasSchema, created, "the new merchant alias");
      setAliases((previous) => [...previous, alias]);
      return alias;
    },
    [],
  );

  const deleteAlias = React.useCallback(async (id: number): Promise<DeleteOutcome> => {
    try {
      await apiDelete<void>(`${API_PATH}/merchant-aliases/${id}`);
    } catch (cause) {
      return {
        kind: "refused",
        status: cause instanceof ApiError ? cause.status : 0,
        message: describeError(cause),
      };
    }
    /* An alias's delete removes only the alias. The link points one way, so the
     * merchant it named stays exactly as it was. */
    setAliases((previous) => previous.filter((row) => row.id !== id));
    return { kind: "deleted" };
  }, []);

  return {
    merchants,
    merchantsLoading,
    merchantsError,
    aliases,
    aliasesLoading,
    aliasesError,
    reloadMerchants,
    reloadAliases,
    createMerchant,
    setMerchantCategory,
    deleteMerchant,
    createAlias,
    deleteAlias,
  };
};

/* The provider owns the fetch; pages read through this context. */
export const MerchantsContext = React.createContext<MerchantsState | null>(null);

export const useMerchantsContext = (): MerchantsState => {
  const state = React.useContext(MerchantsContext);
  if (state === null) {
    throw new Error("useMerchantsContext must be used within a <MerchantsProvider>");
  }
  return state;
};

/* ── Grouping ─────────────────────────────────────────────────────────────
 * BY THE KIND OF THE CATEGORY THE ROW POINTS AT, which is what the category
 * picker groups by and for the same reason: `kind` is the classification the
 * ledger computes every total from, and it decides a balance's sign. A user
 * filing merchants under "Savings" needs to know whether that is an investment
 * or a transfer before they file, not afterwards.
 *
 * TWO GROUPS BEYOND THE FOUR KINDS, and neither of them is decoration.
 *
 * "Not filed" is a merchant or an alias with `category_id: null`, and
 * `finance.ingestion.rules` skips such rows: they are stored, read back
 * faithfully, and match nothing. It is listed rather than hidden because the
 * alternative is a user who typed a name, saw it appear, and never learned that
 * it does nothing — and a name they cannot see working is a name they will not
 * fix.
 *
 * "Category not read" is a row filed at a `category_id` this build could not
 * read back, which happens when the category list failed while the merchant
 * list succeeded. It is kept apart from "Not filed" because the two say
 * opposite things: one is a real state the user chose, the other is this
 * screen failing to resolve an id it holds. */
export const UNFILED_GROUP = "not-filed";
export const UNREAD_GROUP = "category-not-read";

export type CurationGroupKey = CategoryKind | typeof UNFILED_GROUP | typeof UNREAD_GROUP;

export type CurationGroup<T> = {
  key: CurationGroupKey;
  label: string;
  /** What this group MEANS, said once at the header rather than on every row. */
  note: string;
  rows: T[];
};

/** The four kinds in ledger order, then the two exception groups.
 *
 * A build-time constant, not a parameter, so the two exceptions cannot be
 * reordered or dropped by a caller passing a short kind list. */
const KINDS: ReadonlyArray<CategoryKind> = [
  "expense",
  "income",
  "transfer",
  "investment",
];
const EXCEPTIONS: ReadonlyArray<CurationGroupKey> = [UNFILED_GROUP, UNREAD_GROUP];

/** The kinds to group by, then the two exceptions.
 *
 * The kind order is this build's own and narrowed to the kinds the server
 * actually reported. Never the other way round: a kind the server did not list is
 * not offered, because `CategoryKindSchema` would refuse it at the edge. A kind
 * that exists only server-side therefore cannot appear in a group — the same
 * trade `categories/page.tsx` makes, and for the same reason.
 *
 * The two exceptions always come last, whatever the kind order is: they are not
 * classifications, they are the absence of one, and burying them under four kinds
 * of real category is how they get missed. */
const groupOrderFor = (order: ReadonlyArray<CategoryKind>): CurationGroupKey[] => [
  ...KINDS.filter((kind) => order.includes(kind)),
  ...EXCEPTIONS,
];

const GROUP_LABEL: Record<CurationGroupKey, string> = {
  ...KIND_LABEL,
  [UNFILED_GROUP]: "Not filed",
  [UNREAD_GROUP]: "Category not read",
};

const GROUP_NOTE: Record<CurationGroupKey, string> = {
  ...KIND_NOTE,
  [UNFILED_GROUP]:
    "The name is remembered and matches nothing. The engine skips any merchant or alias carrying no category.",
  [UNREAD_GROUP]:
    "Filed at a category id this screen could not read back, so its kind is unknown here.",
};

/** Anything on this screen carries a category id, and that is what it is grouped by. */
export type Filed = { category_id: number | null };

/**
 * Group merchants or aliases by the kind of the category they point at.
 *
 * `order` is passed rather than read from `KIND_ORDER` for the same reason the
 * category picker passes it: a kind that exists only server-side must stay
 * selectable, and one the server did not report must not be invented.
 *
 * `find` resolves a category id to a category. A row is grouped by the kind of
 * the category it resolves to, and one that resolves to nothing goes to
 * "Category not read" rather than being lumped in with "Not filed".
 */
export const groupByCategoryKind = <T extends Filed>(
  rows: readonly T[],
  find: (id: number) => Category | undefined,
  order: ReadonlyArray<CategoryKind>,
): CurationGroup<T>[] =>
  ([...groupOrderFor(order)] as ReadonlyArray<CurationGroupKey>).map((key) => ({
      key,
      label: GROUP_LABEL[key],
      note: GROUP_NOTE[key],
      rows: rows.filter((row) => groupKeyOf(row, find) === key),
    }))
    .filter((group) => group.rows.length > 0);

const groupKeyOf = <T extends Filed>(
  row: T,
  find: (id: number) => Category | undefined,
): CurationGroupKey => {
  if (row.category_id === null) {
    return UNFILED_GROUP;
  }
  const category = find(row.category_id);
  return category === undefined ? UNREAD_GROUP : category.kind;
};

/** Every alias pointing at one merchant. `DELETE /merchants/{id}` cascades to
 * them, so the count is stated before the delete rather than discovered after
 * it — the same reason `rules-page.tsx` counts rules sharing a pattern. */
export const aliasesForMerchant = (
  aliases: readonly MerchantAlias[],
  merchantId: number,
): MerchantAlias[] => aliases.filter((alias) => alias.merchant_id === merchantId);

/** Alphabetical, case- and accent-insensitive, with an explicit numeric
 * collation so "Store 2" sorts after "Store 10".
 *
 * `GET /merchants` already answers in `name` order, so this changes nothing
 * about a page that was only read — it earns its place in the one case where
 * the wire order is wrong, which is immediately after a create, when the new
 * row is appended to a list the server had sorted for us. */
export const byText = (left: string, right: string): number =>
  left.localeCompare(right, "en", { numeric: true, sensitivity: "base" });
