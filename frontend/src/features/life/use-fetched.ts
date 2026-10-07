/* The one async-fetch hook the life pages share.
 *
 * Same shape as finance's per-feature hooks (data / loading / error), so a
 * page's failure states stay identical across modules. `reload()` re-runs the
 * fetcher so a mutation doesn't juggle local duplicates of server state. */
import React from "react";
import { describeError } from "@/lib/apiClient";

export type UseFetched<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
};

export const useFetched = <T,>(
  fetcher: () => Promise<T>,
  deps: React.DependencyList,
): UseFetched<T> => {
  const [data, setData] = React.useState<T | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [tick, setTick] = React.useState(0);

  const reload = React.useCallback(() => setTick((value) => value + 1), []);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetcher()
      .then((value) => {
        if (alive) setData(value);
      })
      .catch((cause: unknown) => {
        if (alive) setError(describeError(cause));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, loading, error, reload };
};
