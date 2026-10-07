/* Vision — the board of images and phrases that says why.
 *
 * Each item is tagged with an Area so a relevant few can surface elsewhere
 * (GLOSSARY "Vision board"), and an inactive item is FADED, NOT GONE: the
 * board keeps it, one click brings it back, and only an explicit remove with
 * a confirmation takes it away. Deletion is the one irreversible act here,
 * so it names what it is about to remove before it happens.
 *
 * The upload is the module's only multipart request — `api.ts` owns the
 * content-type override — and a refusal from the server lands under the file
 * input in the server's own words rather than in a toast that vanishes.
 */
import React from "react";
import { AppShell } from "@/components/AppShell";
import {
  EmptyState,
  Field,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "@/components/primitives";
import { describeError } from "@/lib/apiClient";
import {
  addVisionImage,
  addVisionPhrase,
  deleteVisionItem,
  listVisionItems,
  mediaUrl,
  updateVisionItem,
} from "../api";
import { AREAS, type Area, type VisionItemSummary } from "../types";
import { useFetched } from "../use-fetched";

const VisionCard: React.FC<{
  item: VisionItemSummary;
  onRemove: (item: VisionItemSummary) => void;
  onWake: (item: VisionItemSummary) => void;
}> = ({ item, onRemove, onWake }) => {
  const alt = item.text ?? item.area ?? "vision board image";
  return (
    <figure
      className={
        "group flex flex-col overflow-hidden rounded-md bg-ground " +
        (item.is_active ? "" : "opacity-60")
      }
    >
      {item.kind === "image" && item.media_path !== null ? (
        <img
          src={mediaUrl(item.media_path)}
          alt={alt}
          loading="lazy"
          className="aspect-4/3 w-full object-cover"
        />
      ) : (
        <blockquote className="aspect-4/3 flex items-center justify-center p-4 text-center text-[0.875rem] leading-snug text-ink">
          {item.text ?? ""}
        </blockquote>
      )}
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-3 py-2">
        <span className="eyebrow text-ink-quiet">
          {item.area ?? (item.kind === "image" ? "image" : "phrase")}
          {!item.is_active && " · resting"}
        </span>
        <span className="flex gap-2">
          {!item.is_active && (
            <button
              type="button"
              onClick={() => onWake(item)}
              className="underline text-ink-quiet transition-colors hover:text-ink"
            >
              wake
            </button>
          )}
          <button
            type="button"
            onClick={() => onRemove(item)}
            className="underline text-ink-quiet transition-colors hover:text-ink"
          >
            remove
          </button>
        </span>
      </figcaption>
    </figure>
  );
};

const AddPhrase: React.FC<{
  area: Area | null;
  error: string | null;
  onArea: (area: Area | null) => void;
  onSubmit: (text: string) => Promise<boolean>;
}> = ({ area, error, onArea, onSubmit }) => {
  const [text, setText] = React.useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = text.trim();
    if (value === "") return;
    /* The box is only cleared once the server has taken it: a phrase a
     * refusal sent back is a phrase the user still has to type. */
    if (await onSubmit(value)) setText("");
  };

  return (
    <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
      <Field label="a phrase" htmlFor="vision-phrase" error={error}>
        <input
          id="vision-phrase"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="a kitchen with the window open and nowhere to be"
          className="w-full rounded-md bg-ground px-2.5 py-2 text-sm text-ink placeholder:text-ink-quieter"
        />
      </Field>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="area">
        {AREAS.map((candidate) => (
          <button
            key={candidate}
            type="button"
            onClick={() => onArea(candidate === area ? null : candidate)}
            aria-pressed={candidate === area}
            className={
              "rounded px-2 py-1 text-[0.75rem] tracking-[0.02em] transition-colors " +
              (candidate === area
                ? "bg-lime text-ink"
                : "bg-ground text-ink-quiet hover:text-ink")
            }
          >
            {candidate}
          </button>
        ))}
      </div>
      <div>
        <button
          type="submit"
          disabled={text.trim() === ""}
          className="rounded bg-panel text-ink text-[0.75rem] tracking-[0.02em] px-3 py-2 transition-colors hover:bg-ground disabled:opacity-50"
        >
          Add phrase
        </button>
      </div>
    </form>
  );
};

export const VisionPage: React.FC = () => {
  const items = useFetched(listVisionItems, []);
  const [area, setArea] = React.useState<Area | null>(null);
  /* Two error slots, on purpose: a refusal belongs under the control that
   * caused it, and only a failure with no control of its own (removing,
   * waking) earns the notice at the top of the page. */
  const [error, setError] = React.useState<string | null>(null);
  const [phraseError, setPhraseError] = React.useState<string | null>(null);
  const [uploadError, setUploadError] = React.useState<string | null>(null);
  const [uploading, setUploading] = React.useState(false);

  const onPhrase = React.useCallback(
    async (text: string): Promise<boolean> => {
      setPhraseError(null);
      try {
        await addVisionPhrase(text, area ?? undefined);
        await items.reload();
        return true;
      } catch (cause) {
        setPhraseError(describeError(cause));
        return false;
      }
    },
    [area, items],
  );

  const onFile = React.useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (file === undefined) return;
      setUploadError(null);
      setUploading(true);
      try {
        await addVisionImage(file, area ?? undefined);
        await items.reload();
      } catch (cause) {
        setUploadError(describeError(cause));
      } finally {
        setUploading(false);
        /* Cleared so the same file can be picked again after a refusal —
         * the browser fires no `change` for an unchanged selection. */
        event.target.value = "";
      }
    },
    [area, items],
  );

  const onWake = React.useCallback(
    (item: VisionItemSummary) => {
      void updateVisionItem(item.id, { is_active: true })
        .then(() => items.reload())
        .catch((cause: unknown) => setError(describeError(cause)));
    },
    [items],
  );

  const onRemove = React.useCallback(
    (item: VisionItemSummary) => {
      const named = item.text ?? "this image";
      if (!window.confirm(`Remove “${named}” from the vision board? This cannot be undone.`)) {
        return;
      }
      setError(null);
      void deleteVisionItem(item.id)
        .then(() => items.reload())
        .catch((cause: unknown) => setError(describeError(cause)));
    },
    [items],
  );

  return (
    <AppShell>
      <PageHeader
        title="Vision board"
        description="Images and phrases worth waking toward, tagged by area so a relevant few can surface on the first screen. The why belongs where you will see it."
      />

      {error !== null && (
        <div className="mb-6">
          <Notice tone="error" label="the board refused">{error}</Notice>
        </div>
      )}

      <div className="grid gap-8">
        <Panel title="What's on the board" description="Faded items are resting — kept, one click from back.">
          {items.error !== null ? (
            <Notice tone="error" label="the board didn't load">{items.error}</Notice>
          ) : items.data === null ? (
            <Skeleton label="loading the vision board" rows={3} />
          ) : items.data.length === 0 ? (
            <EmptyState title="The vision board is empty.">
              One phrase is enough to start — the why this is all for, said in your
              own words.
            </EmptyState>
          ) : (
            <div className="grid grid-cols-2 gap-4 px-6 pb-6 sm:grid-cols-3 lg:grid-cols-4">
              {items.data.map((item) => (
                <VisionCard key={item.id} item={item} onRemove={onRemove} onWake={onWake} />
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Add to the board" description="A phrase, or an image from this device.">
          <div className="grid gap-6 px-6 pb-6 lg:grid-cols-2">
            <AddPhrase
              area={area}
              error={phraseError}
              onArea={setArea}
              onSubmit={onPhrase}
            />
            <div>
              <Field
                label="an image"
                htmlFor="vision-image"
                error={uploadError}
                hint="PNG, JPEG or WebP. It is stored on the server and served back through the media route."
              >
                <input
                  id="vision-image"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={uploading}
                  onChange={(event) => void onFile(event)}
                  className="field file:mr-3 file:rounded-md file:border-0 file:bg-ground file:px-2 file:py-1 file:text-ink file:text-xs"
                />
              </Field>
              {uploading && (
                <p className="mt-1.5 text-xs text-ink-quiet" role="status">
                  putting it on the board…
                </p>
              )}
            </div>
          </div>
        </Panel>
      </div>
    </AppShell>
  );
};
