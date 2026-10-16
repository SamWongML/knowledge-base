import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { ErrorState } from "@/components/ui/States";
import { useDebounced } from "@/hooks/useDebounced";
import { useHotkeys } from "@/hooks/useHotkeys";
import { searchQuery } from "@/lib/api/queries";
import type { SearchRequest } from "@/lib/api/types";
import { updateSettings, useSettings } from "@/lib/settings";
import { FilterBar } from "./FilterBar";
import { ResultList, ResultsSkeleton } from "./ResultList";
import { SearchField } from "./SearchField";
import { SearchIntro } from "./SearchIntro";
import { TimingStrip } from "./TimingStrip";

export function SearchPage() {
  const params = useSearch({ from: "/" });
  const navigate = useNavigate({ from: "/" });
  const settings = useSettings();
  const mode = params.mode ?? settings.defaultMode;
  const q = params.q ?? "";

  const [draft, setDraft] = useState(q);
  const debounced = useDebounced(draft, 320);
  const inputRef = useRef<HTMLInputElement>(null);

  // Keep the field in sync when the URL changes from elsewhere (palette, back button).
  const lastQ = useRef(q);
  useEffect(() => {
    if (q !== lastQ.current) {
      lastQ.current = q;
      setDraft(q);
    }
  }, [q]);

  // Search as you type, replacing history so Back doesn't step through keystrokes.
  useEffect(() => {
    const next = debounced.trim();
    if (next === (lastQ.current ?? "").trim()) return;
    lastQ.current = next;
    void navigate({ search: (s) => ({ ...s, q: next || undefined }), replace: true });
  }, [debounced, navigate]);

  const request: SearchRequest = useMemo(
    () => ({
      query: q.trim(),
      mode,
      limit: 20,
      filters: {
        collections: params.collections ?? null,
        tags: params.tags ?? null,
      },
    }),
    [q, mode, params.collections, params.tags],
  );
  const result = useQuery(searchQuery(request));

  useHotkeys([
    {
      combo: "/",
      handler: (e) => {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      },
    },
    { combo: "i", handler: () => updateSettings({ showInspector: !settings.showInspector }) },
  ]);

  const submit = () => {
    const next = draft.trim();
    lastQ.current = next;
    void navigate({ search: (s) => ({ ...s, q: next || undefined }) });
  };

  const hasQuery = q.trim().length > 0;
  const data = result.data;
  const stale = result.isPlaceholderData;

  return (
    <div className="mx-auto max-w-[880px]">
      <header className={hasQuery ? "mb-6" : "mb-10 pt-[4vh]"}>
        <p className="label mb-3">
          <span className="text-accent">§</span> Search the commonplace
        </p>
        <SearchField
          ref={inputRef}
          value={draft}
          onChange={setDraft}
          onSubmit={submit}
          busy={result.isFetching}
          large={!hasQuery}
        />
        <FilterBar
          mode={mode}
          onMode={(m) => void navigate({ search: (s) => ({ ...s, mode: m }), replace: true })}
          collections={params.collections ?? []}
          onCollections={(c) =>
            void navigate({
              search: (s) => ({ ...s, collections: c.length ? c : undefined }),
              replace: true,
            })
          }
          tags={params.tags ?? []}
          onTags={(t) =>
            void navigate({
              search: (s) => ({ ...s, tags: t.length ? t : undefined }),
              replace: true,
            })
          }
          inspector={settings.showInspector}
          onInspector={(v) => updateSettings({ showInspector: v })}
        />
      </header>

      {!hasQuery ? (
        <SearchIntro />
      ) : result.isError ? (
        <ErrorState error={result.error} onRetry={() => void result.refetch()} />
      ) : !data ? (
        <ResultsSkeleton />
      ) : (
        <section
          aria-label="Search results"
          aria-busy={result.isFetching}
          className={stale ? "opacity-60 transition-opacity" : "transition-opacity"}
        >
          <TimingStrip response={data} />
          <ResultList hits={data.hits} query={data.query} inspector={settings.showInspector} />
        </section>
      )}
    </div>
  );
}
