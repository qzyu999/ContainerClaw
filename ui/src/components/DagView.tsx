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
  DONE: '#6b7280',
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

// Spacing constants for Vertical Spacetime
const TIERS_X_SPACING = 280;   // Distance between vertical lanes
const DEPTH_Y_SPACING = 160;   // Vertical movement forward in time
const START_X = 140;
const START_Y = 120;
const NODE_RADIUS = 32;

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

  // ── Chronological Vertical Layout Engine ───────────────────────
  const { nodes, layoutEdges, maxTier, maxDepth } = useMemo(() => {
    if (edges.length === 0) {
      return { nodes: [], layoutEdges: [], maxTier: 0, maxDepth: 0 };
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

      // Only set the parent label if the bridge explicitly sent one, 
      // or if we have literally no other name saved for it yet. 
      // This prevents overwriting a good name with a raw UUID fallback.
      if (e.parent_label || !nodeLabels.has(e.parent)) {
        nodeLabels.set(e.parent, e.parent_label || extractLabel(e.parent));
      }

      // The bridge ALWAYS sends the correct child label, so this safely overwrites 
      // any UUIDs that might have been temporarily saved when this node was a parent.
      nodeLabels.set(e.child, e.child_label || extractLabel(e.child));

      if (e.content) nodeContent.set(e.child, e.content);
      if (e.actor) nodeActor.set(e.child, e.actor);

      // Inherit/capture timestamps — assume child timestamp is reliable
      if (!nodeTimestamps.has(e.child)) nodeTimestamps.set(e.child, Number(e.ts));
      if (!nodeTimestamps.has(e.parent)) nodeTimestamps.set(e.parent, Number(e.ts) - 500); // Hack for root

      if (!childrenOf.has(e.parent)) childrenOf.set(e.parent, []);
      childrenOf.get(e.parent)!.push(e.child);
    });

    const childSet = new Set(edges.map((e: DagEdge) => e.child));
    const roots = [...nodeSet].filter(n => !childSet.has(n));

    // 1. Assign Basic Tiers using Semantic Roles
    const nodeTiers = new Map<string, number>();

    nodeSet.forEach(id => {
      const label = nodeLabels.get(id) || extractLabel(id);
      const l = label.toLowerCase();
      const content = (nodeContent.get(id) || '').toLowerCase();
      const actor = (nodeActor.get(id) || '').toLowerCase();
      const status = nodeStatus.get(id) || (roots.includes(id) ? 'ROOT' : 'ACTIVE');

      const isOrchestration = 
        status === 'ROOT' ||
        l === 'human' || 
        actor === 'human' ||
        l === 'checkpoint' || 
        l === 'election' || 
        l.includes('winner') || 
        l === 'task complete' ||
        content.includes('multi-agent system online') ||
        content.includes('automation halted') ||
        content.includes('/stop');

      if (isOrchestration) {
        nodeTiers.set(id, 0);
      } else if (l === 'moderator' || actor === 'moderator') {
        nodeTiers.set(id, 1); // Will be overwritten for banded items
      } else {
        nodeTiers.set(id, 2);
      }
    });

    // 2. Assign Y-Coordinates and Horizontal Banding
    const uniqueNodes = Array.from(nodeSet);
    const sortedNodes = uniqueNodes.sort((a, b) => {
      const tA = nodeTimestamps.get(a) || 0;
      const tB = nodeTimestamps.get(b) || 0;
      return tA - tB;
    });

    // Helper: Identify if a node is an internal voting detail
    const isElectionDetail = (id: string) => {
      const parentEdge = edges.find(e => e.child === id);
      if (!parentEdge) return false;

      const parentLabel = (nodeLabels.get(parentEdge.parent) || '').toLowerCase();
      const childLabel = (nodeLabels.get(id) || '').toLowerCase();

      return parentLabel === 'election'
        && !childLabel.includes('winner')
        && childLabel !== 'task complete';
    };

    const chronoRankMap = new Map<string, number>();
    let currentYRank = 0;
    let lastElectionParent: string | null = null;
    let electionDetailCount = 0;

    sortedNodes.forEach(id => {
      if (isElectionDetail(id)) {
        const parentEdge = edges.find(e => e.child === id);
        const parent = parentEdge?.parent;

        if (parent) {
          const parentRank = chronoRankMap.get(parent) || currentYRank;
          chronoRankMap.set(id, parentRank + 1);

          if (lastElectionParent !== parent) {
            lastElectionParent = parent;
            electionDetailCount = 1;
          } else {
            electionDetailCount++;
          }
          nodeTiers.set(id, electionDetailCount);
        } else {
          currentYRank = Math.max(currentYRank, ...Array.from(chronoRankMap.values())) + 1;
          chronoRankMap.set(id, currentYRank);
        }
      } else {
        const maxRankSoFar = chronoRankMap.size > 0 ? Math.max(...Array.from(chronoRankMap.values())) : -1;
        currentYRank = maxRankSoFar + 1;
        chronoRankMap.set(id, currentYRank);
      }
    });

    const isHalted = Array.from(nodeContent.values()).some(c => 
      c.toLowerCase().includes('automation halted') || c.toLowerCase().includes('/stop')
    );

    const nodeLayouts: NodeLayout[] = sortedNodes.map(id => {
      let currentStatus = (nodeStatus.get(id) || (roots.includes(id) ? 'ROOT' : 'ACTIVE')) as NodeLayout['status'];
      const hasChildren = (childrenOf.get(id) || []).length > 0;
      
      if (currentStatus !== 'ROOT') {
        // If a node has children, it's in the past. If halted, everything stops.
        if (hasChildren || isHalted) {
          currentStatus = 'DONE';
        }
      }

      return {
        id,
        label: nodeLabels.get(id) || extractLabel(id),
        tier: nodeTiers.get(id) || 0,
        x: START_X + (nodeTiers.get(id) || 0) * TIERS_X_SPACING,
        y: START_Y + (chronoRankMap.get(id) || 0) * DEPTH_Y_SPACING,
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
            The Spacetime DAG will populate top to down as activities occur.
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
          style={{ cursor: isDragging ? 'grabbing' : 'grab', height: '800px' }}
        >
          <svg width="100%" height="100%" className="dag-svg-root">
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
              {/* Vertical Tier Lanes */}
              {Array.from({ length: maxTier + 1 }, (_, tier) => {
                const laneX = START_X + tier * TIERS_X_SPACING;
                const laneHeight = Math.max(1400, (maxDepth + 5) * DEPTH_Y_SPACING);
                return (
                  <g key={`tier-lane-${tier}`}>
                    <rect
                      x={laneX - 100}
                      y={0}
                      width={200}
                      height={laneHeight}
                      fill={TIER_COLORS[Math.min(tier, TIER_COLORS.length - 1)]}
                      rx={16}
                    />
                    <text
                      x={laneX}
                      y={60}
                      fill="#71717a"
                      fontSize={14}
                      fontWeight={700}
                      textAnchor="middle"
                      style={{ opacity: 0.8, letterSpacing: '0.1em' }}
                    >
                      {tier === 0 ? 'CENTRAL TIMELINE' : `TIER ${tier}`}
                    </text>
                  </g>
                );
              })}

              {/* Edges */}
              {layoutEdges.map((edge, i) => {
                const isSpawn = edge.from.tier !== edge.to.tier;
                const isActive = edge.status === 'ACTIVE';
                const dy = edge.to.y - edge.from.y;

                // Hide extremely long ROOT edges (e.g. human /stop command crossing the whole graph)
                if (edge.from.id === 'ROOT' && dy > DEPTH_Y_SPACING * 2) return null;

                // Bezier for spawns and long causal links
                const cp1y = edge.from.y + dy * 0.4;
                const cp2y = edge.from.y + dy * 0.6;
                const cp1x = edge.from.x;
                const cp2x = edge.to.x;
                const d = `M ${edge.from.x} ${edge.from.y + NODE_RADIUS} C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${edge.to.x} ${edge.to.y - NODE_RADIUS}`;

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
                        // Triangle
                        const points = `${node.x},${node.y - r} ${node.x + r * 1.1},${node.y + r * 0.8} ${node.x - r * 1.1},${node.y + r * 0.8}`;
                        return <polygon points={points} {...strokeProps} strokeLinejoin="round" />;
                      } else if (isModerator) {
                        // Square (with slightly rounded corners)
                        return <rect x={node.x - r} y={node.y - r} width={r * 2} height={r * 2} rx={8} {...strokeProps} />;
                      } else {
                        // Circle (Agents)
                        return <circle cx={node.x} cy={node.y} r={r} {...strokeProps} />;
                      }
                    })()}

                    <text
                      x={node.x}
                      y={node.y}
                      textAnchor="middle"
                      dominantBaseline="central"
                      fill={color}
                      fontSize={isHovered ? 14 : 11}
                      fontWeight={700}
                      style={{ transition: 'font-size 0.2s' }}
                    >
                      {node.label.length > 15 ? node.label.slice(0, 14) + '…' : node.label}
                    </text>

                    <text
                      x={node.x}
                      y={node.y + NODE_RADIUS + 25}
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
          </div>
        </div>
      )}
      {selectedNodeId && (
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
      )}
    </div>
  );
}
