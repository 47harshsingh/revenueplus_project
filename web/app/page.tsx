"use client";

import { useEffect, useState } from "react";

type Status = "checking" | "waking" | "ok" | "error";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
const WAKING_AFTER_MS = 5_000; // past this, assume a Render cold start
const TIMEOUT_MS = 70_000; // a cold start can take ~1 minute

export default function Home() {
  const [status, setStatus] = useState<Status>("checking");
  const [attempt, setAttempt] = useState(0); // bump to re-run the check

  useEffect(() => {
    let cancelled = false; // set on unmount or re-run, so stale results are ignored
    const controller = new AbortController();
    const wakingTimer = setTimeout(() => setStatus("waking"), WAKING_AFTER_MS);
    const timeoutTimer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    fetch(`${API_URL}/health`, { signal: controller.signal })
      .then(async (res) => {
        const body = await res.json();
        if (!cancelled) setStatus(res.ok && body.status === "ok" ? "ok" : "error");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      })
      .finally(() => {
        clearTimeout(wakingTimer);
        clearTimeout(timeoutTimer);
      });

    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(wakingTimer);
      clearTimeout(timeoutTimer);
    };
  }, [attempt]);

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-2xl font-semibold">RevenuePulse</h1>
      {status === "checking" && <p>Checking API…</p>}
      {status === "waking" && <p>Waking up the API… (can take ~1 minute)</p>}
      {status === "ok" && <p className="text-green-600">API: ok</p>}
      {status === "error" && (
        <>
          <p className="text-red-600">API: unreachable</p>
          <button
            onClick={() => {
              setStatus("checking");
              setAttempt((n) => n + 1);
            }}
            className="rounded border px-4 py-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            Retry
          </button>
        </>
      )}
    </main>
  );
}
