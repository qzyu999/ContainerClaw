import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import type { ActivityEvent } from '../api';
import { fetchSnorkelPerspective, fetchRawHistory } from '../api';
import type { PerspectiveMessage, RawHistoryEvent } from '../api';

interface SnorkelViewProps {
  events: ActivityEvent[];
  sessionId: string;
}

/** Determine the actor category for button rendering. */
function getActorType(actorId: string | undefined): 'agent' | 'human' | 'moderator' | 'subagent' {
  if (!actorId) return 'moderator';
  if (actorId === 'Moderator') return 'moderator';
  if (actorId === 'Human' || actorId.startsWith('Discord/')) return 'human';
  if (actorId.startsWith('Sub/')) return 'subagent';
  return 'agent';
}

export default function SnorkelView({ events, sessionId }: SnorkelViewProps) {
  const [selectedEventIndex, setSelectedEventIndex] = useState<number | null>(null);
  const [perspective, setPerspective] = useState<PerspectiveMessage[]>([]);
  const [rawHistory, setRawHistory] = useState<RawHistoryEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [viewMode, setViewMode] = useState<'dive' | 'raw' | null>(null);

  const handleDive = async (index: number, event: ActivityEvent) => {
    setSelectedEventIndex(index);
    setPerspective([]);
    setRawHistory([]);
    setIsLoading(true);
    setViewMode('dive');

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

  const handleViewRaw = async (index: number, event: ActivityEvent) => {
    setSelectedEventIndex(index);
    setPerspective([]);
    setRawHistory([]);
    setIsLoading(true);
    setViewMode('raw');

    try {
      const history = await fetchRawHistory(sessionId, event.timestamp);
      setRawHistory(history);
    } catch (err) {
      console.error('Failed to fetch raw history:', err);
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
              const actorType = getActorType(e.actor_id);
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
                    {actorType === 'agent' || actorType === 'subagent' ? (
                      <button 
                        className={`btn-dive ${isActive && viewMode === 'dive' ? 'active' : ''}`} 
                        onClick={() => handleDive(index, e)}
                      >
                        Dive
                      </button>
                    ) : actorType === 'human' ? (
                      <button 
                        className={`btn-view ${isActive && viewMode === 'raw' ? 'active' : ''}`}
                        onClick={() => handleViewRaw(index, e)}
                      >
                        View
                      </button>
                    ) : null /* Moderator: no button */}
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
              {viewMode === 'dive' ? (
                <>Snorkeling as: <strong>{events[selectedEventIndex].actor_id || 'system'}</strong></>
              ) : viewMode === 'raw' ? (
                <>Viewing as: <strong>{events[selectedEventIndex].actor_id || 'Human'}</strong> (plain history)</>
              ) : null}
            </div>
          )}
        </div>
        <div className="snorkel-hud-content">
          {isLoading ? (
            <div className="loading-overlay">
              <Loader2 className="animate-spin" size={32} color="#4ade80" />
              <span>{viewMode === 'raw' ? 'Loading History...' : 'Reconstructing Context...'}</span>
            </div>
          ) : viewMode === 'dive' && perspective.length > 0 ? (
             <div className="hud-messages">
               {perspective.map((msg, i) => (
                 <div key={i} className={`hud-message hud-message-${msg.role}`}>
                   <div className="hud-message-role">{msg.role}</div>
                   <div className="hud-message-body">{msg.content}</div>
                 </div>
               ))}
             </div>
          ) : viewMode === 'raw' && rawHistory.length > 0 ? (
            <div className="hud-messages">
              {rawHistory.map((evt, i) => (
                <div key={i} className={`hud-message hud-message-raw`}>
                  <div className="hud-message-role">{evt.actor_id}</div>
                  <div className="hud-message-body">{evt.content}</div>
                </div>
              ))}
            </div>
          ) : selectedEventIndex !== null ? (
            <div className="hud-empty">
              No context returned. The history may be empty or filtered out.
            </div>
          ) : (
            <div className="hud-empty">
              Select "Dive" on an agent event or "View" on a human event to inspect the context.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
