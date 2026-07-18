import { useEffect, useRef, useState } from "react";
import type { BlackboardState, ConnectionState, Manifest } from "../types";

const API_BASE = "http://localhost:8787";
const WS_URL = "ws://localhost:8787/ws";
const POLL_INTERVAL_MS = 1500;
const RECONNECT_DELAY_MS = 3000;
const OFFLINE_AFTER_FAILED_POLLS = 3;

const EMPTY_STATE: BlackboardState = { agents: {}, decisions: [], questions: [], claims: [] };
const EMPTY_MANIFEST: Manifest = { project: null, roles: [] };

/**
 * Prefers a live WebSocket (server pushes on every Blackboard change).
 * If the socket can't connect, falls back to polling GET /state and reports
 * "reconnecting" while polling succeeds. If polling itself starts failing
 * too (server unreachable), reports "offline" — but keeps showing the last
 * known snapshot either way; state is only ever replaced by a fresh
 * snapshot, never optimistically mutated or cleared.
 */
export function useWorkflowState() {
  const [manifest, setManifest] = useState<Manifest>(EMPTY_MANIFEST);
  const [state, setState] = useState<BlackboardState>(EMPTY_STATE);
  const [connection, setConnection] = useState<ConnectionState>("reconnecting");
  const pollHandle = useRef<number | null>(null);
  const failedPolls = useRef(0);

  useEffect(() => {
    const refreshManifest = () =>
      fetch(`${API_BASE}/manifest`)
        .then((r) => r.json())
        .then(setManifest)
        .catch(() => {});
    refreshManifest();
    const id = window.setInterval(refreshManifest, 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let retryTimer: number | null = null;

    const startPolling = () => {
      if (pollHandle.current != null) return;
      pollHandle.current = window.setInterval(() => {
        fetch(`${API_BASE}/state`)
          .then((r) => r.json())
          .then((s) => {
            if (cancelled) return;
            setState(s);
            failedPolls.current = 0;
            setConnection((c) => (c === "live" ? c : "reconnecting"));
          })
          .catch(() => {
            if (cancelled) return;
            failedPolls.current += 1;
            if (failedPolls.current >= OFFLINE_AFTER_FAILED_POLLS) setConnection("offline");
          });
      }, POLL_INTERVAL_MS);
    };

    const stopPolling = () => {
      if (pollHandle.current != null) {
        clearInterval(pollHandle.current);
        pollHandle.current = null;
      }
    };

    const connect = () => {
      if (cancelled) return;
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        if (cancelled) return;
        setConnection("live");
        failedPolls.current = 0;
        stopPolling();
      };
      ws.onmessage = (ev) => {
        try {
          setState(JSON.parse(ev.data));
        } catch {
          // ignore malformed frame — keep the last good snapshot
        }
      };
      ws.onerror = () => {
        startPolling();
      };
      ws.onclose = () => {
        if (cancelled) return;
        setConnection((c) => (c === "live" ? "reconnecting" : c));
        startPolling();
        retryTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();

    return () => {
      cancelled = true;
      stopPolling();
      if (retryTimer != null) clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);

  return { manifest, state, connection };
}
