import { useRef, useEffect } from 'react';
import { History, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import type { ActivityEvent } from '../api';

interface ChatroomViewProps {
  events: ActivityEvent[];
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  onPromptClick: (content: string) => void;
}

export default function ChatroomView({ 
  events, 
  sidebarCollapsed, 
  onToggleSidebar, 
  onPromptClick 
}: ChatroomViewProps) {
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [events]);

  // Filter non-action events for the chatroom terminal
  const chatEvents = events.filter(e => e.type !== 'action');

  return (
    <div className={`main-content ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="card" style={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <History size={16} color="#a1a1aa" />
              <h3>Session History</h3>
            </div>
            <button 
              onClick={onToggleSidebar}
              className="sidebar-toggle-btn"
              title="Collapse Sidebar"
            >
              <PanelLeftClose size={16} />
            </button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {events.filter(e => e.type === 'user' || e.type === 'thought').reverse().map((e, i) => (
              <div key={i} className="history-item" onClick={() => onPromptClick(e.content)}>
                {e.content.slice(0, 50)}{e.content.length > 50 ? '...' : ''}
              </div>
            ))}
          </div>
        </div>
      </aside>

      {sidebarCollapsed && (
        <button 
          className="sidebar-expand-btn"
          onClick={onToggleSidebar}
          title="Expand Sidebar"
        >
          <PanelLeftOpen size={18} />
        </button>
      )}

      <section className="terminal-container">
        <div className="terminal-header">
          <div className="dot" style={{ backgroundColor: '#ff5f56' }} />
          <div className="dot" style={{ backgroundColor: '#ffbd2e' }} />
          <div className="dot" style={{ backgroundColor: '#27c93f' }} />
          <span style={{ marginLeft: '8px', fontSize: '0.75rem', color: '#6b7280', fontFamily: 'var(--font-mono)' }}>
            bash — containerclaw — 80x24
          </span>
        </div>
        <div className="terminal-body" ref={terminalRef}>
          <div className="log-line">
            <span className="log-time">[{new Date().toLocaleTimeString()}]</span>
            <span className="log-tag" style={{ color: '#4ade80' }}>[SYSTEM]</span>
            <span className="log-content">Ready for tasks.</span>
          </div>
          <AnimatePresence>
            {chatEvents.map((event, i) => (
              <motion.div 
                key={i} 
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="log-line"
              >
                <span className="log-time">[{new Date(event.timestamp || Date.now()).toLocaleTimeString()}]</span>
                <span className={`log-tag tag-${event.type}`}>
                  [{event.type.toUpperCase()}]
                </span>
                {event.actor_id && (
                  <span className={`actor-badge actor-${event.actor_id}`}>
                    {event.actor_id}
                  </span>
                )}
                <span className="log-content">{event.content}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </section>
    </div>
  );
}
