import { ArrowRight, Search } from "lucide-react";
import { forwardRef } from "react";
import { Kbd } from "@/components/ui/Kbd";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  busy?: boolean;
  large?: boolean;
}

/** The editorial search field: a serif line on a single hairline rule. */
export const SearchField = forwardRef<HTMLInputElement, Props>(function SearchField(
  { value, onChange, onSubmit, busy, large },
  ref,
) {
  return (
    // biome-ignore lint/a11y/useSemanticElements: <search> wrapper adds no value over role here
    <form
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="group relative flex items-center gap-3 border-b border-border-strong pb-2 transition-colors focus-within:border-foreground"
    >
      <Search
        aria-hidden
        className={cn("shrink-0 text-faint transition-all", large ? "size-7" : "size-5")}
        strokeWidth={1.25}
      />
      <input
        ref={ref}
        type="search"
        // biome-ignore lint/a11y/noAutofocus: search is the primary action on this page
        autoFocus
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            // Keep the query (Chrome clears search inputs on Escape); just leave the field.
            e.preventDefault();
            e.currentTarget.blur();
          }
        }}
        placeholder="Search notes, papers, passages…"
        aria-label="Search query"
        enterKeyHint="search"
        spellCheck={false}
        className={cn(
          "min-w-0 flex-1 bg-transparent font-display tracking-[-0.01em] text-foreground placeholder:text-faint/80 focus:outline-none [&::-webkit-search-cancel-button]:hidden",
          large
            ? "text-[40px] leading-[1.2] sm:text-[56px]"
            : "text-[30px] leading-[1.2] sm:text-[36px]",
        )}
      />
      {busy ? (
        <Spinner className="text-muted-foreground" label="Searching" />
      ) : value ? (
        <button
          type="submit"
          aria-label="Search"
          className="grid size-8 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-foreground hover:text-background"
        >
          <ArrowRight className="size-4" strokeWidth={1.5} />
        </button>
      ) : (
        <Kbd className="hidden sm:inline-flex">/</Kbd>
      )}
    </form>
  );
});
