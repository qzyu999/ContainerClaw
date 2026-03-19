import { useState, useRef, useEffect } from 'react';
import { Terminal } from 'lucide-react';
import type { ActivityEvent } from '../api';

const AGENT_NAMES = ['Alice', 'Bob', 'Carol', 'David', 'Eve'];

const AGENT_COLORS: Record<string, string> = {
  Alice: '#a78bfa',
  Bob: '#60a5fa',
  Carol: '#f472b6',
  David: '#fbbf24',
  Eve: '#34d399',
};

interface ConchShellPanelProps {
  events: ActivityEvent[];
  collapsed: boolean;
  onToggle: () => void;
}

export default function ConchShellPanel({ events, collapsed, onToggle }: ConchShellPanelProps) {
  const [activeAgent, setActiveAgent] = useState('Alice');
  const termRef = useRef<HTMLDivElement>(null);

  // Filter action events for the selected agent
  const actionEvents = events.filter(
    e => e.type === 'action' && e.actor_id === activeAgent
  );

  // Count actions per agent for badges
  const counts: Record<string, number> = {};
  for (const e of events) {
    if (e.type === 'action' && e.actor_id) {
      counts[e.actor_id] = (counts[e.actor_id] || 0) + 1;
    }
  }

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight;
    }
  }, [actionEvents.length, activeAgent]);

  return (
    <div className={`conchshell-panel ${collapsed ? 'collapsed' : ''}`}>
      <div className="conchshell-header" onClick={onToggle}>
        <div className="conchshell-title">
          <Terminal size={14} />
          <span>🐚 ConchShell</span>
          <span className="conchshell-count">
            {events.filter(e => e.type === 'action').length} actions
          </span>
        </div>
        <span className="conchshell-toggle">{collapsed ? '▲' : '▼'}</span>
      </div>

      {!collapsed && (
        <>
          <div className="conchshell-tabs">
            {AGENT_NAMES.map(name => (
              <button
                key={name}
                className={`conchshell-tab ${activeAgent === name ? 'active' : ''}`}
                onClick={() => setActiveAgent(name)}
                style={{
                  borderBottomColor: activeAgent === name ? AGENT_COLORS[name] : 'transparent',
                }}
              >
                {name}
                {(counts[name] || 0) > 0 && (
                  <span className="conchshell-badge" style={{ background: AGENT_COLORS[name] }}>
                    {counts[name]}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="conchshell-terminal" ref={termRef}>
            {actionEvents.length === 0 ? (
              <div className="conchshell-empty">
                No actions from {activeAgent} yet.
              </div>
            ) : (
              actionEvents.map((event, i) => (
                <div key={i} className="conchshell-line">
                  <span className="conchshell-time">
                    [{new Date(event.timestamp || Date.now()).toLocaleTimeString()}]
                  </span>
                  <span className="conchshell-output">{event.content}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
