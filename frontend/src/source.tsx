import { useEffect, useState } from "react";
import { AlertCircle, Database, RefreshCw } from "lucide-react";
import { dataRefreshEvent, requestApi, type SourceStatus } from "./api";
import { stamp } from "./common";

// Share a pending request across React StrictMode's mount/effect replay.
// The server also coalesces requests within one instance and enforces its limits.
let pendingRefresh: Promise<SourceStatus> | null = null;
function refreshSource(force: boolean): Promise<SourceStatus> {
  if (pendingRefresh) return pendingRefresh;
  const request = requestApi<SourceStatus>(
    `/api/source/refresh?force=${force}`,
    { method: "POST" },
  ).then((status) => {
    window.dispatchEvent(new Event(dataRefreshEvent));
    return status;
  });
  pendingRefresh = request;
  void request.then(
    () => { pendingRefresh = null; },
    () => { pendingRefresh = null; },
  );
  return request;
}

export function sourceLabel(mode: SourceStatus["mode"] | undefined) {
  if (mode === "live") return "Fetched from Elexon API";
  if (mode === "cached") return "Stored Elexon cache";
  if (mode === "snapshot") return "Elexon startup snapshot";
  return "Elexon stored data";
}

export function SourceStatusBar({
  onStatus,
}: {
  onStatus: (status: SourceStatus) => void;
}) {
  const [status, setStatus] = useState<SourceStatus | null>(null);
  const [pending, setPending] = useState(true);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    let active = true;
    refreshSource(false)
      .then((result) => {
        if (active) {
          setStatus(result);
          onStatus(result);
        }
      })
      .catch((error: Error) => {
        if (active) setRequestError(error.message);
      })
      .finally(() => {
        if (active) setPending(false);
      });
    return () => { active = false; };
  }, [onStatus]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const attemptTime = status?.last_attempt_at
    ? new Date(status.last_attempt_at).getTime()
    : 0;
  const remaining = status
    ? Math.max(0, Math.ceil((attemptTime + status.cooldown_seconds * 1000 - now) / 1000))
    : 0;
  const error = requestError || status?.error;

  async function refresh() {
    setPending(true);
    setRequestError(null);
    try {
      const result = await refreshSource(true);
      setStatus(result);
      onStatus(result);
    } catch (error) {
      setRequestError(
        error instanceof Error ? error.message : "Unable to reach Elexon.",
      );
      // A failed HTTP response may still have consumed the server cooldown.
      // Reconcile with the authoritative status instead of guessing its timing.
      try {
        const result = await requestApi<SourceStatus>("/api/source-status");
        setStatus(result);
        onStatus(result);
      } catch {
        // Keep the previous known status and analysis if status is unreachable.
      }
    } finally {
      setNow(Date.now());
      setPending(false);
    }
  }

  return (
    <section className={`source-status ${error ? "source-warning" : ""}`} aria-label="Elexon connection and stored data">
      <div className="source-status-copy">
        <div className="source-status-heading" aria-live="polite">
          {error ? <AlertCircle size={16} /> : <Database size={16} />}
          <strong>{pending ? "Checking Elexon API…" : status ? sourceLabel(status.mode) : "Elexon source status unavailable"}</strong>
          <span className="source-status-scope">January 2026 decision window</span>
        </div>
        <p>
          {pending
            ? "Fetching the historical source data when due. Stored analysis remains available."
            : !status
              ? "Source freshness is unavailable. Showing the last loaded dataset."
              : status.last_success_at
                ? `Historical January data fetched: ${stamp(status.last_success_at)}.`
                : "No successful API fetch recorded. Showing the stored startup data."}
          {!pending && status && (
            <> {status.storage === "persistent" ? "Saved in the database." : "Server cache may reset on restart."} Automatic checks reuse data for {Math.round(status.ttl_seconds / 60)} minutes.</>
          )}
        </p>
        {error && (
          <p className="source-status-error" role="alert">
            Elexon refresh could not complete. Stored analysis is still available. {error}
          </p>
        )}
      </div>
      <div className="source-status-action">
        <button className="button" onClick={() => void refresh()} disabled={pending || remaining > 0}>
          <RefreshCw size={14} className={pending ? "source-refreshing" : undefined} />
          {pending ? "Fetching from Elexon…" : "Refresh from Elexon"}
        </button>
        {!pending && remaining > 0 && (
          <small>Refresh available in {remaining}s</small>
        )}
      </div>
    </section>
  );
}
