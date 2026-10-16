import { useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { FieldLabel, Input } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import { DEFAULT_SETTINGS, updateSettings, useSettings } from "@/lib/settings";

/** Per-browser preferences, stored in localStorage. */
export function SettingsForm() {
  const settings = useSettings();
  const qc = useQueryClient();
  const [token, setToken] = useState(settings.apiToken);
  const [reveal, setReveal] = useState(false);

  return (
    <section aria-labelledby="settings-h">
      <h2 id="settings-h" className="label mb-4 border-b border-border pb-2">
        Settings <span className="normal-case tracking-normal text-faint">· this browser only</span>
      </h2>
      <div className="space-y-6">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            updateSettings({ apiToken: token.trim() });
            void qc.invalidateQueries();
            toast.success(token.trim() ? "API token saved" : "API token cleared");
          }}
        >
          <FieldLabel
            label="API token"
            hint="Sent as a bearer token. Needed for writes when the server sets KB_API_TOKEN."
          >
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  type={reveal ? "text" : "password"}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="not set"
                  className="pr-9 font-mono text-[12.5px]"
                />
                <button
                  type="button"
                  onClick={() => setReveal((r) => !r)}
                  aria-label={reveal ? "Hide token" : "Show token"}
                  className="absolute top-1/2 right-2 -translate-y-1/2 text-faint hover:text-foreground"
                >
                  {reveal ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                </button>
              </div>
              <Button type="submit" disabled={token.trim() === settings.apiToken}>
                Save
              </Button>
            </div>
          </FieldLabel>
        </form>

        <Row label="Default answer engine">
          <Segmented
            label="Default answer engine"
            value={settings.defaultEngine}
            onChange={(v) => updateSettings({ defaultEngine: v })}
            options={[
              { value: "local", label: "Local" },
              { value: "cloud", label: "Cloud" },
            ]}
          />
        </Row>
        <Row label="Default search mode">
          <Segmented
            label="Default search mode"
            value={settings.defaultMode}
            onChange={(v) => updateSettings({ defaultMode: v })}
            options={[
              { value: "hybrid", label: "Hybrid" },
              { value: "lexical", label: "Lexical" },
              { value: "semantic", label: "Semantic" },
            ]}
          />
        </Row>
        <Row label="Theme">
          <Segmented
            label="Theme"
            value={settings.theme}
            onChange={(v) => updateSettings({ theme: v })}
            options={[
              { value: "system", label: "System" },
              { value: "light", label: "Paper" },
              { value: "dark", label: "Ink" },
            ]}
          />
        </Row>
        <Row label="Score inspector">
          <Segmented
            label="Score inspector"
            value={settings.showInspector ? "on" : "off"}
            onChange={(v) => updateSettings({ showInspector: v === "on" })}
            options={[
              { value: "off", label: "Hidden" },
              { value: "on", label: "Shown" },
            ]}
          />
        </Row>
        <button
          type="button"
          className="label hover:text-destructive"
          onClick={() => {
            updateSettings(DEFAULT_SETTINGS);
            setToken("");
            toast("Settings reset");
          }}
        >
          Reset to defaults
        </button>
      </div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <span className="text-[13px] text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}
