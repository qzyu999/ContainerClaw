import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { ActivityEvent } from '../api';
import { fetchSnorkelPerspective } from '../api';
import type { PerspectiveMessage } from '../api';

interface SnorkelViewProps {
  events: ActivityEvent[];
  sessionId: string;
}

export default function SnorkelView({ events, sessionId }: SnorkelViewProps) {
  const [selectedEventIndex, setSelectedEventIndex] = useState<number | null>(null);
  const [perspective, setPerspective] = useState<PerspectiveMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // We should clearly identify the Actor being snorkeled. If not present, default to "system" or similar.
  const handleDive = async (index: number, event: ActivityEvent) => {
    setSelectedEventIndex(index);
    setPerspective([]);
    setIsLoading(true);

    const actorId = event.actor_id || 'system';
    try {
      const msgs = await fetchSnorkelPerspective(sessionId, event.timestamp, actorId);
      setPerspective(msgs);
    } catch (err) {
      console.error('Failed to fetch perspective:', err);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="snorkel-container">
      {/* Left Pane: Log Table */}
      <div className="snorkel-table-pane">
        <table className="snorkel-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Actor</th>
              <th>Type</th>
              <th>Content Snippet</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, index) => {
              const isActive = index === selectedEventIndex;
              return (
                <tr key={index} className={`snorkel-row ${isActive ? 'active-row' : ''}`}>
                  <td className="snorkel-cell-time">{new Date(e.timestamp || Date.now()).toLocaleTimeString()}</td>
                  <td className="snorkel-cell-actor">
                    {e.actor_id ? <span className={`actor-badge actor-${e.actor_id}`}>{e.actor_id}</span> : <span style={{color: '#a1a1aa'}}>—</span>}
                  </td>
                  <td className="snorkel-cell-type">
                    <span className={`log-tag tag-${e.type}`}>[{e.type.toUpperCase()}]</span>
                  </td>
                  <td className="snorkel-cell-content" title={e.content}>
                    {e.content.length > 80 ? e.content.substring(0, 80) + '...' : e.content}
                  </td>
                  <td className="snorkel-cell-action">
                    <button 
                      className={`btn-dive ${isActive ? 'active' : ''}`} 
                      onClick={() => handleDive(index, e)}
                    >
                      Dive
                    </button>
                  </td>
                </tr>
              );
            })}
            {events.length === 0 && (
              <tr>
                <td colSpan={5} style={{ textAlign: 'center', padding: '2rem', color: '#a1a1aa' }}>
                  No events to display.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Right Pane: Perspective HUD */}
      <div className="snorkel-hud-pane">
        <div className="snorkel-hud-header">
          <h3>Perspective HUD</h3>
          {selectedEventIndex !== null && events[selectedEventIndex] && (
            <div className="hud-meta">
              Snorkeling as: <strong>{events[selectedEventIndex].actor_id || 'system'}</strong>
            </div>
          )}
        </div>
        <div className="snorkel-hud-content">
          {isLoading ? (
            <div className="loading-overlay">
              <Loader2 className="animate-spin" size={32} color="#4ade80" />
              <span>Reconstructing Context...</span>
            </div>
          ) : perspective.length > 0 ? (
             <div className="hud-messages">
               {perspective.map((msg, i) => (
                 <div key={i} className={`hud-message hud-message-${msg.role}`}>
                   <div className="hud-message-role">{msg.role}</div>
                   <div className="hud-message-body">{msg.content}</div>
                 </div>
               ))}
             </div>
          ) : selectedEventIndex !== null ? (
            <div className="hud-empty">
              No context returned. The history may be empty or filtered out.
            </div>
          ) : (
            <div className="hud-empty">
              Select "Dive" on any event to view the reconstructed context window.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
