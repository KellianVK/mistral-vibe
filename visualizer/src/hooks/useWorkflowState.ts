import { useEffect, useRef, useState } from "react";

export type AgentStatus = "idle" | "working" | "done" | "blocked" | "error";

export interface AgentState {
  status: AgentStatus;
  current_task: string | null;
  updated_at: string;
}

export interface Decision {
  role: string;
  summary: string;
  artifact: string | null;
  ts: string;
}

export interface Question {
  from: string;
  to: string;
  question: string;
  resolved: boolean;
}

export interface BlackboardState {
  agents: Record<string, AgentState>;
  decisions: Decision[];
  questions: Question[];
}

export interface RoleSpec {
  name: string;
  agent_profile: string;
  model: string | null;
  depends_on: string[];
}

export interface Manifest {
  project: { goal: string; gates: string[]; max_loop_iterations: number } | null;
  roles: RoleSpec[];
}

const API_BASE = "http://localhost:8787";
const WS_URL = "ws://localhost:8787/ws";
const POLL_INTERVAL_MS = 1500;
const RECONNECT_DELAY_MS = 3000;

const EMPTY_STATE: BlackboardState = { agents: {}, decisions: [], questions: [] };
const EMPTY_MANIFEST: Manifest = { project: null, roles: [] };

/**
 * Prefers a live WebSocket (server pushes on every Blackboard change);
 * falls back to polling GET /state if the socket never connects, and
 * keeps retrying the socket in the background so it can take back over.
 */
export function useWorkflowState() {
  const [manifest, setManifest] = useState<Manifest>(EMPTY_MANIFEST);
  const [state, setState] = useState<BlackboardState>(EMPTY_STATE);
  const [connected, setConnected] = useState(false);
  const pollHandle = useRef<number | null>(null);

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
          .then((s) => !cancelled && setState(s))
          .catch(() => {});
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
        setConnected(true);
        stopPolling();
      };
      ws.onmessage = (ev) => {
        try {
          setState(JSON.parse(ev.data));
        } catch {
          // ignore malformed frame
        }
      };
      ws.onerror = () => {
        startPolling();
      };
      ws.onclose = () => {
        if (cancelled) return;
        setConnected(false);
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

  return { manifest, state, connected };
}
