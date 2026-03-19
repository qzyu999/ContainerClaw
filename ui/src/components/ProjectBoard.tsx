import { useState, useEffect, useCallback } from 'react';
import { ClipboardList } from 'lucide-react';
import { fetchBoardData } from '../api';
import type { BoardItem } from '../api';

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

export default function ProjectBoard({ 
  sessionId, 
  refreshKey,
  collapsed,
  onToggle
}: ProjectBoardProps) {
  const [items, setItems] = useState<BoardItem[]>([]);
  const [loading, setLoading] = useState(false);

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
                  {columnItems.map(item => (
                    <div key={item.id} className="board-card">
                      <div className="board-card-id">{item.id}</div>
                      <div className="board-card-title">{item.title}</div>
                      {item.description && (
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
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
