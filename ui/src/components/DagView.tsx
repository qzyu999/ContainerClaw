import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { fetchDagEdges } from '../api';
import type { DagEdge } from '../api';

interface DagViewProps {
  sessionId: string;
}

interface NodeLayout {
  id: string;          // Compound key: "Actor|event_id"
  label: string;       // Display name: "Actor"
  x: number;
  y: number;
  tier: number;        // Nesting depth (0 = main timeline)
  ts: number;          // Chronological timestamp
  status: 'ACTIVE' | 'THINKING' | 'DONE' | 'ROOT';
  content?: string;
  actor?: string;
}

const STATUS_COLORS: Record<string, string> = {
  ACTIVE: '#4ade80',
  THINKING: '#fbbf24',
  DONE: '#a1a1aa',
  ROOT: '#60a5fa',
};

const STATUS_GLOW: Record<string, string> = {
  ACTIVE: 'rgba(74, 222, 128, 0.3)',
  THINKING: 'rgba(251, 191, 36, 0.3)',
  DONE: 'rgba(107, 114, 128, 0.15)',
  ROOT: 'rgba(96, 165, 250, 0.3)',
};

const TIER_COLORS = [
  'rgba(96, 165, 250, 0.05)',   // Tier 0 — main timeline (subtle blue)
  'rgba(74, 222, 128, 0.05)',   // Tier 1 — sub-agents (subtle green)
  'rgba(251, 191, 36, 0.05)',   // Tier 2 — sub-sub (subtle amber)
  'rgba(192, 132, 252, 0.05)',  // Tier 3+ — purple
];

// Spacing constants for Horizontal Spacetime
const DEPTH_X_SPACING = 140;   // Horizontal movement forward in time
const TIERS_Y_SPACING = 180;   // Vertical movement down into sub-tiers
const START_X = 140;
const START_Y = 160;
const NODE_RADIUS = 32;
const ELECTION_Y_STAGGER = 80; // Vertical gap between election nodes

function extractLabel(compoundId: string): string {
  const pipeIdx = compoundId.indexOf('|');
  return pipeIdx >= 0 ? compoundId.substring(0, pipeIdx) : compoundId;
}

export default function DagView({ sessionId }: DagViewProps) {
  const [edges, setEdges] = useState<DagEdge[]>([]);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Navigation state (Pan & Zoom)
  const [viewState, setViewState] = useState({ x: 0, y: 0, scale: 1.0 });
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const loadDag = useCallback(async () => {
    try {
      const data = await fetchDagEdges(sessionId);
      setEdges(data);
    } catch (err) {
      console.error('Failed to fetch DAG:', err);
    }
  }, [sessionId]);

  useEffect(() => {
    loadDag();
    const interval = setInterval(loadDag, 2000);
    return () => clearInterval(interval);
  }, [loadDag]);

  // ── Horizontal Layout Engine ─────────────────────────────────────
  const { nodes, layoutEdges, electionBands, maxTier, maxDepth } = useMemo(() => {
    if (edges.length === 0) {
      return { nodes: [], layoutEdges: [], electionBands: [], maxTier: 0, maxDepth: 0 };
    }

    const nodeSet = new Set<string>();
    const nodeStatus = new Map<string, string>();
    const nodeLabels = new Map<string, string>();
    const nodeTimestamps = new Map<string, number>();
    const nodeContent = new Map<string, string>();
    const nodeActor = new Map<string, string>();
    const childrenOf = new Map<string, string[]>();

    edges.forEach((e: DagEdge) => {
      nodeSet.add(e.parent);
      nodeSet.add(e.child);
      nodeStatus.set(e.child, e.status);

      if (e.parent_label || !nodeLabels.has(e.parent)) {
        nodeLabels.set(e.parent, e.parent_label || extractLabel(e.parent));
      }
      nodeLabels.set(e.child, e.child_label || extractLabel(e.child));

      if (e.content) nodeContent.set(e.child, e.content);
      if (e.actor) nodeActor.set(e.child, e.actor);

      nodeTimestamps.set(e.child, Number(e.ts));

      if (!nodeTimestamps.has(e.parent)) {
        nodeTimestamps.set(e.parent, Number(e.ts) - 500);
      } else if (e.parent === 'ROOT') {
        nodeTimestamps.set('ROOT', Math.min(nodeTimestamps.get('ROOT') as number, Number(e.ts) - 500));
      }

      if (!childrenOf.has(e.parent)) childrenOf.set(e.parent, []);
      childrenOf.get(e.parent)!.push(e.child);
    });

    const childSet = new Set(edges.map((e: DagEdge) => e.child));
    const roots = [...nodeSet].filter(n => !childSet.has(n));

    // 1. Check for Halted or Completed State
    let isHalted = false;
    let isCompleted = false;
    nodeContent.forEach((c) => {
      const text = c.toLowerCase();
      if (text.includes('automation halted') || text.includes('/stop')) {
        isHalted = true;
      }
      if (text.includes('consensus: task complete')) {
        isCompleted = true;
      }
    });

    // 2. Assign Basic Tiers
    const nodeTiers = new Map<string, number>();

    nodeSet.forEach(id => {
      const content = (nodeContent.get(id) || '').toLowerCase();
      const actor = (nodeActor.get(id) || '').toLowerCase();

      const isToolExecution =
        (content.includes('🔱 spawned subagent') ||
          content.includes('🏁 subagent completed') ||
          content.includes('[tool result'))
        && actor === 'moderator';

      const isSubagent = actor.startsWith('sub/');

      if (isToolExecution || isSubagent) {
        nodeTiers.set(id, 1);
      } else {
        nodeTiers.set(id, 0);
      }
    });

    // 3. Assign X-Coordinates (Time) and Vertical Stacking (Y-Offset)
    const uniqueNodes = Array.from(nodeSet);
    const sortedNodes = uniqueNodes.sort((a, b) => {
      const tA = nodeTimestamps.get(a) || 0;
      const tB = nodeTimestamps.get(b) || 0;
      return tA - tB;
    });

    const isElectionDetail = (id: string) => {
      const parentEdge = edges.find((e: DagEdge) => e.child === id);
      if (!parentEdge) return false;

      const parentLabel = (nodeLabels.get(parentEdge.parent) || '').toLowerCase();
      const childLabel = (nodeLabels.get(id) || '').toLowerCase();

      return parentLabel === 'election'
        && !childLabel.includes('winner')
        && childLabel !== 'task complete';
    };

    const chronoRankMap = new Map<string, number>();
    const bandOffsetMap = new Map<string, number>();
    const electionBands = new Map<number, { x: number, count: number }>();

    let currentXRank = 0;

    sortedNodes.forEach(id => {
      if (isElectionDetail(id)) {
        const parentEdge = edges.find((e: DagEdge) => e.child === id);
        const parent = parentEdge?.parent;

        if (parent) {
          const parentRank = chronoRankMap.get(parent) || currentXRank;
          const xRank = parentRank + 1; // Push to next time column
          chronoRankMap.set(id, xRank);

          const siblings = edges
            .filter((e: DagEdge) => e.parent === parent && isElectionDetail(e.child))
            .map((e: DagEdge) => e.child);

          siblings.sort((a: string, b: string) => (nodeTimestamps.get(a) || 0) - (nodeTimestamps.get(b) || 0));

          const offset = siblings.indexOf(id); // 0-based index for vertical drop
          bandOffsetMap.set(id, offset);

          electionBands.set(xRank, {
            x: START_X + xRank * DEPTH_X_SPACING,
            count: siblings.length
          });

        } else {
          currentXRank = Math.max(currentXRank, ...Array.from(chronoRankMap.values())) + 1;
          chronoRankMap.set(id, currentXRank);
        }
      } else {
        const maxRankSoFar = chronoRankMap.size > 0 ? Math.max(...Array.from(chronoRankMap.values())) : -1;
        currentXRank = maxRankSoFar + 1;
        chronoRankMap.set(id, currentXRank);
      }
    });

    const nodeLayouts: NodeLayout[] = sortedNodes.map(id => {
      let currentStatus = (nodeStatus.get(id) || (roots.includes(id) ? 'ROOT' : 'ACTIVE')) as NodeLayout['status'];
      const hasChildren = (childrenOf.get(id) || []).length > 0;
      const content = (nodeContent.get(id) || '').toLowerCase();

      if (currentStatus !== 'ROOT') {
        const isSystemLog =
          content.includes('[tool result') ||
          content.includes('🏁 subagent completed') ||
          content.includes('election summary:') ||
          content.includes('tally:') ||
          content.startsWith('✅');

        if (hasChildren || isHalted || isCompleted || isSystemLog) {
          currentStatus = 'DONE';
        }
      }

      const tier = nodeTiers.get(id) || 0;
      const bandOffset = bandOffsetMap.get(id) || 0;

      // X handles Time, Y handles Tier + Stack Offset
      const x = START_X + (chronoRankMap.get(id) || 0) * DEPTH_X_SPACING;
      const y = START_Y + (tier * TIERS_Y_SPACING) + (bandOffset * ELECTION_Y_STAGGER);

      return {
        id,
        label: nodeLabels.get(id) || extractLabel(id),
        tier,
        x,
        y,
        ts: nodeTimestamps.get(id) || 0,
        status: currentStatus,
        content: nodeContent.get(id),
        actor: nodeActor.get(id),
      };
    });

    const lMap = new Map(nodeLayouts.map(n => [n.id, n]));
    const lEdges = edges.map((e: DagEdge) => {
      const from = lMap.get(e.parent);
      const to = lMap.get(e.child);
      if (from && to) return { from, to, status: e.status };
      return null;
    }).filter(Boolean);

    return {
      nodes: nodeLayouts,
      layoutEdges: lEdges as { from: NodeLayout; to: NodeLayout; status: string }[],
      electionBands: Array.from(electionBands.values()),
      maxTier: Math.max(0, ...Array.from(nodeTiers.values())),
      maxDepth: sortedNodes.length,
    };
  }, [edges]);

  // ── Navigation Handlers ──────────────────────────────────────────
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    setIsDragging(true);
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setViewState(prev => ({
      ...prev,
      x: prev.x + e.movementX,
      y: prev.y + e.movementY,
    }));
  };

  const handleMouseUp = () => setIsDragging(false);

  const handleWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      const scaleDelta = e.deltaY > 0 ? 0.95 : 1.05;
      setViewState((prev: { x: number; y: number; scale: number }) => ({
        ...prev,
        scale: Math.min(Math.max(prev.scale * scaleDelta, 0.1), 4),
      }));
    } else {
      setViewState((prev: { x: number; y: number; scale: number }) => ({
        ...prev,
        x: prev.x - e.deltaX,
        y: prev.y - e.deltaY,
      }));
    }
  };

  const resetView = () => setViewState({ x: 0, y: 0, scale: 1.0 });

  const zoomIn = () => {
    setViewState((prev: { x: number; y: number; scale: number }) => ({
      ...prev,
      scale: Math.min(prev.scale * 1.2, 4)
    }));
  };

  const zoomOut = () => {
    setViewState((prev: { x: number; y: number; scale: number }) => ({
      ...prev,
      scale: Math.max(prev.scale / 1.2, 0.1)
    }));
  };

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
          <div className="dag-empty-title">Waiting for Stream...</div>
          <div className="dag-empty-sub">
            The Spacetime DAG will populate as activities occur.
          </div>
        </div>
      ) : (
        <div
          className="dag-canvas-area"
          ref={containerRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onWheel={handleWheel}
          onClick={() => setSelectedNodeId(null)}
          style={{ cursor: isDragging ? 'grabbing' : 'grab', height: '100%', minHeight: '800px' }}
        >
          <svg width="100%" height="100%" className="dag-svg-root" style={{ minHeight: '800px' }}>
            <defs>
              <marker id="arrowhead" markerWidth="10" markerHeight="8" refX="10" refY="4" orient="auto">
                <polygon points="0 0, 10 4, 0 8" fill="#52525b" />
              </marker>
              <marker id="arrowhead-active" markerWidth="10" markerHeight="8" refX="10" refY="4" orient="auto">
                <polygon points="0 0, 10 4, 0 8" fill="#4ade80" />
              </marker>
              <marker id="arrowhead-spawn" markerWidth="10" markerHeight="8" refX="10" refY="4" orient="auto">
                <polygon points="0 0, 10 4, 0 8" fill="#fbbf24" />
              </marker>
            </defs>

            <g transform={`translate(${viewState.x}, ${viewState.y}) scale(${viewState.scale})`}>
              {/* Horizontal Tier Lanes */}
              {Array.from({ length: maxTier + 1 }, (_, tier) => {
                const laneY = START_Y + tier * TIERS_Y_SPACING;
                const laneWidth = Math.max(2000, (maxDepth + 5) * DEPTH_X_SPACING);
                return (
                  <g key={`tier-lane-${tier}`}>
                    <rect
                      x={0}
                      y={laneY - 80}
                      width={laneWidth}
                      height={160}
                      fill={TIER_COLORS[Math.min(tier, TIER_COLORS.length - 1)]}
                      rx={16}
                    />
                    <text
                      x={40}
                      y={laneY - 50}
                      fill="#71717a"
                      fontSize={14}
                      fontWeight={700}
                      textAnchor="start"
                      style={{ opacity: 0.8, letterSpacing: '0.1em' }}
                    >
                      {tier === 0 ? 'MAIN STREAM' : `SUBAGENT STREAM ${tier}`}
                    </text>
                  </g>
                );
              })}

              {/* Vertical Election Phase Bands */}
              {electionBands.map((band: { x: number; count: number }, i: number) => {
                const boxHeight = NODE_RADIUS * 2 + 60 + ((band.count - 1) * ELECTION_Y_STAGGER);
                return (
                  <g key={`election-band-${i}`}>
                    <rect
                      x={band.x - NODE_RADIUS - 20}
                      y={START_Y - NODE_RADIUS - 30}
                      width={NODE_RADIUS * 2 + 40}
                      height={boxHeight}
                      fill="rgba(255, 255, 255, 0.03)"
                      stroke="#3f3f46"
                      strokeWidth={1}
                      strokeDasharray="4 4"
                      rx={12}
                    />
                    <text
                      x={band.x}
                      y={START_Y - NODE_RADIUS - 40}
                      fill="#a1a1aa"
                      fontSize={10}
                      fontWeight={700}
                      letterSpacing="0.1em"
                      textAnchor="middle"
                    >
                      ELECTION
                    </text>
                  </g>
                );
              })}

              {/* Edges */}
              {layoutEdges.map((edge, i) => {
                const isSpawn = edge.from.tier !== edge.to.tier;
                const isActive = edge.status === 'ACTIVE';
                const dx = edge.to.x - edge.from.x;

                // Hide extremely long ROOT edges
                if (edge.from.id === 'ROOT' && dx > DEPTH_X_SPACING * 2) return null;

                // Bezier curves adjusted for Left-to-Right flow
                const cp1x = edge.from.x + dx * 0.4;
                const cp2x = edge.from.x + dx * 0.6;
                const cp1y = edge.from.y;
                const cp2y = edge.to.y;

                const startX = edge.from.x + NODE_RADIUS;
                const endX = edge.to.x - NODE_RADIUS;

                const d = `M ${startX} ${edge.from.y} C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${endX} ${edge.to.y}`;

                return (
                  <path
                    key={`edge-${i}`}
                    d={d}
                    fill="none"
                    stroke={isSpawn ? '#fbbf24' : (isActive ? '#4ade80' : '#333')}
                    strokeWidth={isActive ? 3 : 2}
                    strokeDasharray={isSpawn ? "8,4" : "none"}
                    markerEnd={isSpawn ? "url(#arrowhead-spawn)" : (isActive ? "url(#arrowhead-active)" : "url(#arrowhead)")}
                  />
                );
              })}

              {/* Nodes */}
              {nodes.map(node => {
                const color = STATUS_COLORS[node.status] || '#71717a';
                const isHovered = hoveredNode === node.id;

                return (
                  <g
                    key={node.id}
                    onMouseEnter={() => setHoveredNode(node.id)}
                    onMouseLeave={() => setHoveredNode(null)}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectedNodeId(node.id);
                    }}
                    style={{ cursor: 'pointer' }}
                  >
                    {/* Node Glow */}
                    {(node.status === 'ACTIVE' || node.status === 'THINKING') && (
                      <circle cx={node.x} cy={node.y} r={isHovered ? NODE_RADIUS * 1.5 : NODE_RADIUS * 1.3} fill={STATUS_GLOW[node.status]}>
                        {node.status === 'ACTIVE' && (
                          <animate attributeName="r" values={`${NODE_RADIUS * 1.1};${NODE_RADIUS * 1.4};${NODE_RADIUS * 1.1}`} dur="2s" repeatCount="indefinite" />
                        )}
                      </circle>
                    )}

                    {(() => {
                      const l = node.label.toLowerCase();
                      const actor = (node.actor || '').toLowerCase();

                      const isHuman = l === 'human' || actor === 'human';
                      const isModerator = l === 'moderator' || actor === 'moderator' || l === 'checkpoint' || l === 'election' || l === 'task complete' || l.includes('winner');

                      const r = isHovered ? NODE_RADIUS * 1.2 : NODE_RADIUS;
                      const strokeProps = {
                        fill: "#09090b",
                        stroke: color,
                        strokeWidth: 3,
                        style: { transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)' }
                      };

                      if (isHuman) {
                        // Triangle pointing UP
                        const points = `${node.x},${node.y - r} ${node.x + r * 1.1},${node.y + r * 0.8} ${node.x - r * 1.1},${node.y + r * 0.8}`;
                        return <polygon points={points} {...strokeProps} strokeLinejoin="round" />;
                      } else if (isModerator) {
                        return <rect x={node.x - r} y={node.y - r} width={r * 2} height={r * 2} rx={8} {...strokeProps} />;
                      } else {
                        return <circle cx={node.x} cy={node.y} r={r} {...strokeProps} />;
                      }
                    })()}

                    <text
                      x={node.x}
                      y={node.y + NODE_RADIUS + 20}
                      textAnchor="middle"
                      fill={color}
                      fontSize={isHovered ? 14 : 11}
                      fontWeight={700}
                      style={{ transition: 'font-size 0.2s' }}
                    >
                      {node.label.length > 15 ? node.label.slice(0, 14) + '…' : node.label}
                    </text>

                    <text
                      x={node.x}
                      y={node.y + NODE_RADIUS + 34}
                      textAnchor="middle"
                      fill="#a1a1aa"
                      fontSize={10}
                      fontWeight={500}
                      style={{ pointerEvents: 'none' }}
                    >
                      {node.status}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>

          <div className="dag-controls">
            <button onClick={zoomIn} className="dag-btn" title="Zoom In">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
            <button onClick={zoomOut} className="dag-btn" title="Zoom Out">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
            <button onClick={resetView} className="dag-btn" title="Reset View">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                <path d="M3 3v5h5" />
              </svg>
            </button>
            <div className="dag-zoom-info">{(viewState.scale * 100).toFixed(0)}%</div>
          </div>

          <div className="dag-legend">
            <div className="dag-legend-item">
              <span className="dag-legend-dot" style={{ background: '#60a5fa' }}></span>
              <span>Root</span>
            </div>
            <div className="dag-legend-item">
              <span className="dag-legend-dot" style={{ background: '#4ade80' }}></span>
              <span>Active</span>
            </div>
            <div className="dag-legend-item">
              <span className="dag-legend-dot" style={{ background: '#fbbf24' }}></span>
              <span>Thinking</span>
            </div>
            <div className="dag-legend-item">
              <span className="dag-legend-dot" style={{ background: '#6b7280' }}></span>
              <span>Done</span>
            </div>
            <div className="dag-legend-item" style={{ marginLeft: '12px', opacity: 0.6 }}>
              <svg width="24" height="2" style={{ marginRight: '6px' }}><line x1="0" y1="1" x2="24" y2="1" stroke="#52525b" strokeWidth="2" /></svg>
              <span>Causal</span>
            </div>
            <div className="dag-legend-item" style={{ opacity: 0.6 }}>
              <svg width="24" height="2" style={{ marginRight: '6px' }}><line x1="0" y1="1" x2="24" y2="1" stroke="#fbbf24" strokeDasharray="4,2" strokeWidth="2" /></svg>
              <span>Spawn</span>
            </div>
            <div className="dag-legend-item" style={{ marginLeft: '12px', opacity: 0.6 }}>
              <svg width="14" height="14" style={{ marginRight: '6px', overflow: 'visible' }}>
                <polygon points="7,0 14,12 0,12" fill="none" stroke="#a1a1aa" strokeWidth="2" strokeLinejoin="round" />
              </svg>
              <span>Human</span>
            </div>
            <div className="dag-legend-item" style={{ opacity: 0.6 }}>
              <svg width="14" height="14" style={{ marginRight: '6px' }}>
                <rect x="1" y="1" width="12" height="12" rx="3" fill="none" stroke="#a1a1aa" strokeWidth="2" />
              </svg>
              <span>Moderator</span>
            </div>
            <div className="dag-legend-item" style={{ opacity: 0.6 }}>
              <svg width="14" height="14" style={{ marginRight: '6px' }}>
                <circle cx="7" cy="7" r="6" fill="none" stroke="#a1a1aa" strokeWidth="2" />
              </svg>
              <span>Agent</span>
            </div>
          </div>
        </div>
        </div>
  )
}
{
  selectedNodeId && (
    <div className="dag-metadata-panel" onClick={(e) => e.stopPropagation()}>
      {(() => {
        const n = nodes.find(n => n.id === selectedNodeId);
        if (!n) return null;
        return (
          <>
            <div className="dag-metadata-header">
              <h3>{n.label}</h3>
              <button onClick={() => setSelectedNodeId(null)}>✕</button>
            </div>
            <div className="dag-metadata-body">
              <div className="meta-row"><strong>Actor:</strong> {n.actor || 'System'}</div>
              <div className="meta-row"><strong>Status:</strong> {n.status}</div>
              <div className="meta-row"><strong>Time:</strong> {new Date(n.ts).toLocaleTimeString()}</div>
              <div className="meta-row"><strong>Event ID:</strong> <span className="mono">{n.id.split('-')[0]}...</span></div>
              <div className="meta-content">
                <strong>Content:</strong>
                <pre>{n.content || 'No content available.'}</pre>
              </div>
            </div>
          </>
        );
      })()}
    </div>
  )
}
    </div >
  );
}