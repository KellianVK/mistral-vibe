import { useMemo } from "react";
import { Background, Controls, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { buildGraph } from "../lib/buildGraph";
import type { BlackboardState, Manifest } from "../types";
import { AgentNode } from "./AgentNode";

const nodeTypes = { agent: AgentNode };

export function TeamCanvas({ manifest, state }: { manifest: Manifest; state: BlackboardState }) {
  // Re-derive on every state change, but buildGraph itself stays pure —
  // same (manifest, state) always yields the same nodes/edges.
  const { nodes, edges } = useMemo(() => buildGraph(manifest, state), [manifest, state]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      proOptions={{ hideAttribution: true }}
      colorMode="dark"
      nodesDraggable={false}
      nodesConnectable={false}
      minZoom={0.4}
      maxZoom={1.5}
    >
      <Background color="var(--surface-border)" gap={24} />
      <Controls showInteractive={false} position="bottom-left" />
    </ReactFlow>
  );
}
