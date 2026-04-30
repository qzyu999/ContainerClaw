import { useState, useEffect, useCallback } from 'react';
import { ClipboardList } from 'lucide-react';
import { fetchBoardData } from '../api';
import type { BoardItem, BoardComment } from '../api';

interface ProjectBoardProps {
  sessionId: string;
  refreshKey: number;
  collapsed: boolean;
  onToggle: () => void;
}

const STATUS_CONFIG: Record<string, { label: string; icon: string; color: string }> = {
  todo: { label: 'To Do', icon: '⬜', color: '#6b7280' },
  in_progress: { label: 'In Progress', icon: '🟡', color: '#f59e0b' },
  done: { label: 'Done', icon: '✅', color: '#4ade80' },
};

const AGENT_COLORS: Record<string, string> = {
  Alice: '#a78bfa',
  Bob: '#60a5fa',
  Carol: '#f472b6',
  David: '#fbbf24',
  Eve: '#34d399',
};

const CATEGORY_ICONS: Record<string, string> = {
  analysis: '🔍',
  finding: '💡',
  conclusion: '✅',
  blocker: '🚧',
  status_change: '🔄',
  summary: '📦',
};

function relativeTime(tsMs: number): string {
  const diff = (Date.now() - tsMs) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function CommentThread({ comments }: { comments: BoardComment[] }) {
  const active = comments.filter(c => !c.archived);
  if (active.length === 0) return null;

  return (
    <div className="board-comment-thread">
      {active.map(c => (
        <div key={c.comment_id} className="board-comment">
          <div className="board-comment-header">
            <span className="board-comment-icon">{CATEGORY_ICONS[c.category] || '💬'}</span>
            <span className="board-comment-category">{c.category}</span>
            <span 
              className="board-comment-author"
              style={{ color: AGENT_COLORS[c.author] || '#a1a1aa' }}
            >
              {c.author}
            </span>
            <span className="board-comment-time">{relativeTime(c.ts)}</span>
          </div>
          <div className="board-comment-content">{c.content}</div>
        </div>
      ))}
    </div>
  );
}

export default function ProjectBoard({ 
  sessionId, 
  refreshKey,
  collapsed,
  onToggle
}: ProjectBoardProps) {
  const [items, setItems] = useState<BoardItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchBoardData(sessionId);
      setItems(data);
    } catch {
      // Board may not exist yet
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const toggleExpand = (itemId: string) => {
    setExpandedItems(prev => {
      const next = new Set(prev);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  };

  const columns = ['todo', 'in_progress', 'done'];

  return (
    <div className={`board-container ${collapsed ? 'collapsed' : ''}`}>
      <div className="board-header" onClick={onToggle}>
        <ClipboardList size={14} color="#a1a1aa" />
        <span>Project Board</span>
        <button className="tree-refresh" onClick={(e) => { e.stopPropagation(); load(); }} title="Refresh">↻</button>
        <span className="board-toggle">{collapsed ? '▲' : '▼'}</span>
      </div>
      {!collapsed && (
        <div className="board-columns">
          {columns.map(status => {
            const config = STATUS_CONFIG[status];
            const columnItems = items.filter(i => i.status === status);
            return (
              <div key={status} className="board-column">
                <div className="board-column-header" style={{ borderBottomColor: config.color }}>
                  <span>{config.icon} {config.label}</span>
                  <span className="board-column-count">
                    {loading ? '...' : columnItems.length}
                  </span>
                </div>
                <div className="board-column-body">
                  {columnItems.map(item => {
                    const activeComments = (item.comments || []).filter(c => !c.archived);
                    const isExpanded = expandedItems.has(item.id);
                    const lastComment = activeComments.length > 0 ? activeComments[activeComments.length - 1] : null;

                    return (
                      <div 
                        key={item.id} 
                        className={`board-card ${isExpanded ? 'expanded' : ''}`}
                        onClick={() => activeComments.length > 0 && toggleExpand(item.id)}
                      >
                        <div className="board-card-id">{item.id}</div>
                        <div className="board-card-title">{item.title}</div>
                        {item.description && !isExpanded && (
                          <div className="board-card-desc">{item.description}</div>
                        )}
                        {item.assigned_to && (
                          <div 
                            className="board-card-assignee"
                            style={{ color: AGENT_COLORS[item.assigned_to] || '#a1a1aa' }}
                          >
                            → {item.assigned_to}
                          </div>
                        )}
                        {/* Comment summary (collapsed) */}
                        {!isExpanded && lastComment && (
                          <div className="board-card-comment-summary">
                            <span className="board-card-comment-count">💬 {activeComments.length}</span>
                            <span className="board-card-comment-preview">
                              {CATEGORY_ICONS[lastComment.category] || '💬'} "{lastComment.content.slice(0, 50)}{lastComment.content.length > 50 ? '…' : ''}"
                            </span>
                          </div>
                        )}
                        {item.last_reason && !isExpanded && (
                          <div className="board-card-reason">
                            🔄 {item.last_reason}
                          </div>
                        )}
                        {/* Expanded comment thread */}
                        {isExpanded && (
                          <CommentThread comments={item.comments || []} />
                        )}
                        {/* Expand indicator */}
                        {activeComments.length > 0 && (
                          <div className="board-card-expand-hint">
                            {isExpanded ? '▲ collapse' : `▼ ${activeComments.length} comment${activeComments.length !== 1 ? 's' : ''}`}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
