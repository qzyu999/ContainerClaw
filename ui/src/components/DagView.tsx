import { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchDagEdges } from '../api';
import type { DagEdge } from '../api';

interface DagViewProps {
  sessionId: string;
}

interface NodeLayout {
  id: string;
  x: number;
  y: number;
  status: 'ACTIVE' | 'THINKING' | 'DONE' | 'ROOT';
}

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#4ade80',
  THINKING: '#fbbf24',
  DONE: '#6b7280',
  ROOT: '#60a5fa',
};

const STATUS_GLOW: Record<string, string> = {
  ACTIVE: 'rgba(74, 222, 128, 0.3)',
  THINKING: 'rgba(251, 191, 36, 0.3)',
  DONE: 'rgba(107, 114, 128, 0.15)',
  ROOT: 'rgba(96, 165, 250, 0.3)',
};

export default function DagView({ sessionId }: DagViewProps) {
  const [edges, setEdges] = useState<DagEdge[]>([]);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);

  const loadDag = useCallback(async () => {
    const data = await fetchDagEdges(sessionId);
    setEdges(data);
  }, [sessionId]);

  useEffect(() => {
    loadDag();
    const interval = setInterval(loadDag, 2000);
    return () => clearInterval(interval);
  }, [loadDag]);

  // Build the graph layout from edges
  const { nodes, layoutEdges } = useMemo(() => {
    if (edges.length === 0) {
      return { nodes: [] as NodeLayout[], layoutEdges: [] as { from: NodeLayout; to: NodeLayout; status: string }[] };
    }

    // Collect unique nodes
    const nodeSet = new Set<string>();
    const childStatus = new Map<string, string>();
    edges.forEach(e => {
      nodeSet.add(e.parent);
      nodeSet.add(e.child);
      childStatus.set(e.child, e.status);
    });

    // Find roots (nodes that are parents but never children)
    const children = new Set(edges.map(e => e.child));
    const roots = [...nodeSet].filter(n => !children.has(n));

    // BFS to assign layers
    const layers = new Map<string, number>();
    const queue = [...roots];
    roots.forEach(r => layers.set(r, 0));

    while (queue.length > 0) {
      const current = queue.shift()!;
      const currentLayer = layers.get(current)!;
      edges.filter(e => e.parent === current).forEach(e => {
        if (!layers.has(e.child)) {
          layers.set(e.child, currentLayer + 1);
          queue.push(e.child);
        }
      });
    }

    // Group nodes by layer
    const layerGroups = new Map<number, string[]>();
    layers.forEach((layer, node) => {
      if (!layerGroups.has(layer)) layerGroups.set(layer, []);
      layerGroups.get(layer)!.push(node);
    });

    const maxLayer = Math.max(...layerGroups.keys(), 0);

    // Position nodes
    const NODE_W = 140;
    const LAYER_H = 100;
    const PADDING_X = 80;
    const PADDING_Y = 60;

    const nodeLayouts: NodeLayout[] = [];
    layerGroups.forEach((group, layer) => {
      const totalWidth = group.length * NODE_W;
      const startX = PADDING_X + ((maxLayer + 1) * NODE_W - totalWidth) / 2;
      group.forEach((nodeId, idx) => {
        const status = childStatus.get(nodeId) || (roots.includes(nodeId) ? 'ROOT' : 'ACTIVE');
        nodeLayouts.push({
          id: nodeId,
          x: startX + idx * NODE_W + NODE_W / 2,
          y: PADDING_Y + layer * LAYER_H + 20,
          status: status as NodeLayout['status'],
        });
      });
    });

    // Build edge coordinates
    const nodeMap = new Map(nodeLayouts.map(n => [n.id, n]));
    const lEdges = edges
      .map(e => {
        const from = nodeMap.get(e.parent);
        const to = nodeMap.get(e.child);
        if (from && to) return { from, to, status: e.status };
        return null;
      })
      .filter(Boolean) as { from: NodeLayout; to: NodeLayout; status: string }[];

    return { nodes: nodeLayouts, layoutEdges: lEdges };
  }, [edges]);

  const svgWidth = Math.max(600, ...nodes.map(n => n.x + 80));
  const svgHeight = Math.max(300, ...nodes.map(n => n.y + 80));

  return (
    <div className="dag-container">
      {edges.length === 0 ? (
        <div className="dag-empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#3b3b44" strokeWidth="1.5">
            <circle cx="12" cy="5" r="2.5" />
            <circle cx="6" cy="17" r="2.5" />
            <circle cx="18" cy="17" r="2.5" />
            <line x1="12" y1="7.5" x2="6" y2="14.5" />
            <line x1="12" y1="7.5" x2="18" y2="14.5" />
          </svg>
          <div className="dag-empty-title">No DAG Data</div>
          <div className="dag-empty-sub">
            Agent interactions will appear here as a directed graph when telemetry is active.
          </div>
        </div>
      ) : (
        <div className="dag-scroll">
          <svg width={svgWidth} height={svgHeight} className="dag-svg">
            <defs>
              <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#52525b" />
              </marker>
              <marker id="arrowhead-active" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#4ade80" />
              </marker>
            </defs>

            {/* Edges */}
            {layoutEdges.map((edge, i) => {
              const isActive = edge.status === 'ACTIVE';
              return (
                <line
                  key={`edge-${i}`}
                  x1={edge.from.x}
                  y1={edge.from.y + 16}
                  x2={edge.to.x}
                  y2={edge.to.y - 16}
                  stroke={isActive ? '#4ade80' : '#333'}
                  strokeWidth={isActive ? 2 : 1.5}
                  markerEnd={isActive ? 'url(#arrowhead-active)' : 'url(#arrowhead)'}
                  opacity={isActive ? 1 : 0.5}
                />
              );
            })}

            {/* Nodes */}
            {nodes.map(node => {
              const color = STATUS_COLORS[node.status] || '#71717a';
              const glow = STATUS_GLOW[node.status] || 'transparent';
              const isHovered = hoveredNode === node.id;

              return (
                <g
                  key={node.id}
                  onMouseEnter={() => setHoveredNode(node.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                  style={{ cursor: 'pointer' }}
                >
                  {/* Glow */}
                  {(node.status === 'ACTIVE' || node.status === 'THINKING') && (
                    <circle cx={node.x} cy={node.y} r={isHovered ? 28 : 24} fill={glow}>
                      {node.status === 'ACTIVE' && (
                        <animate attributeName="r" values="22;26;22" dur="2s" repeatCount="indefinite" />
                      )}
                    </circle>
                  )}

                  {/* Node circle */}
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={isHovered ? 20 : 16}
                    fill="#18181b"
                    stroke={color}
                    strokeWidth={2}
                    style={{ transition: 'r 0.15s ease' }}
                  />

                  {/* Node label */}
                  <text
                    x={node.x}
                    y={node.y + 1}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fill={color}
                    fontSize={isHovered ? 10 : 9}
                    fontWeight={600}
                    fontFamily="'Inter', sans-serif"
                  >
                    {node.id.length > 10 ? node.id.slice(0, 9) + '…' : node.id}
                  </text>

                  {/* Status label below */}
                  <text
                    x={node.x}
                    y={node.y + 30}
                    textAnchor="middle"
                    fill="#52525b"
                    fontSize={8}
                    fontFamily="'Inter', sans-serif"
                  >
                    {node.status}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {/* Legend */}
      <div className="dag-legend">
        {Object.entries(STATUS_COLORS).map(([status, color]) => (
          <div key={status} className="dag-legend-item">
            <span className="dag-legend-dot" style={{ background: color }} />
            <span>{status}</span>
          </div>
        ))}
        <span className="dag-legend-count">{edges.length} edge{edges.length !== 1 ? 's' : ''}</span>
      </div>
    </div>
  );
}
