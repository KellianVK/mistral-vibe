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
  ts?: string;
}

export interface FileClaim {
  role: string;
  path: string;
  ts: string;
}

export interface BlackboardState {
  agents: Record<string, AgentState>;
  decisions: Decision[];
  questions: Question[];
  claims: FileClaim[];
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
