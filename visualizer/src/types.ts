export type AgentStatus = "idle" | "working" | "done" | "blocked" | "error";

export interface AgentState {
  status: AgentStatus;
  current_task: string | null;
  updated_at: string;
}

export interface Decision {
  id?: number;
  role: string;
  topic?: string | null;
  summary: string;
  artifact: string | null;
  ts: string;
}

export interface Question {
  id?: number;
  from: string;
  to: string;
  question: string;
  answer?: string | null;
  resolved: boolean;
  ts?: string;
}

export interface FileClaim {
  role: string;
  path: string;
  ts: string;
}

export interface RoleTiming {
  spawned_at: string | null;
  first_action_s: number | null;
  turns: number;
  avg_turn_s: number | null;
  total_s: number | null;
}

export interface RunInfo {
  id: number;
  goal: string;
  started_at: string;
}

export interface TeamMessage {
  id: number;
  ts: string;
  from: string;
  to: string;
  content: string;
  read: boolean;
}

export interface TeamBroadcast {
  id: number;
  ts: string;
  from: string;
  content: string;
}

export interface FileChange {
  id: number;
  ts: string;
  role: string;
  path: string;
  action: "created" | "modified" | "deleted";
}

export interface BlackboardState {
  goal?: string | null;
  run?: RunInfo | null;
  agents: Record<string, AgentState>;
  decisions: Decision[];
  questions: Question[];
  claims: FileClaim[];
  messages?: TeamMessage[];
  broadcasts?: TeamBroadcast[];
  changes?: FileChange[];
  timings?: Record<string, RoleTiming>;
}

export interface LogEntry {
  role: string;
  content: string;
  tools: string[];
  tool_name: string | null;
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

export type ConnectionState = "live" | "reconnecting" | "offline";
