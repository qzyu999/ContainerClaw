import { useState, useEffect } from 'react';
import { Anchor, MessageSquare, Loader2, Info } from 'lucide-react';
import { fetchAnchor, setAnchor, fetchAnchorTemplates } from '../api';
import type { ActivityEvent } from '../api';

interface AnchorViewProps {
  sessionId: string;
  events: ActivityEvent[];
}


export default function AnchorView({ sessionId, events }: AnchorViewProps) {
  const [anchorText, setAnchorText] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState({ text: '', type: '' });
  const [templates, setTemplates] = useState<{ label: string, text: string, default: boolean }[]>([]);

  // Fetch initial anchor and templates on load
  useEffect(() => {
    const load = async () => {
      setIsLoading(true);
      const [text, tmpls] = await Promise.all([
        fetchAnchor(sessionId),
        fetchAnchorTemplates()
      ]);
      setAnchorText(text);
      setTemplates(tmpls);
      setIsLoading(false);
    };
    load();
  }, [sessionId]);

  const handleDropAnchor = async () => {
    setIsSaving(true);
    const ok = await setAnchor(sessionId, anchorText);
    if (ok) {
      setStatusMsg({ text: 'Anchor dropped successfully!', type: 'success' });
      setTimeout(() => setStatusMsg({ text: '', type: '' }), 3000);
    } else {
      setStatusMsg({ text: 'Failed to drop anchor.', type: 'error' });
    }
    setIsSaving(false);
  };

  const applyTemplate = (text: string) => {
    setAnchorText(text);
  };

  // Filter events to show a clean tail of the conversation
  const chatTail = events.filter(e => e.type === 'user' || e.type === 'thought' || e.type === 'output').slice(-15);

  return (
    <div className="anchor-container">
      {/* Top Pane: Context Tail */}
      <div className="anchor-pane anchor-context-pane">
        <div className="pane-header">
          <MessageSquare size={16} />
          <h3>Latest Chatroom Messages (Context Tail)</h3>
        </div>
        <div className="anchor-context-list">
          {chatTail.map((e, i) => (
            <div key={i} className={`anchor-context-item actor-${e.actor_id || 'system'}`}>
              <span className="item-actor">{e.actor_id || 'System'}:</span>
              <span className="item-content">{e.content.length > 200 ? e.content.substring(0, 200) + '...' : e.content}</span>
            </div>
          ))}
          {chatTail.length === 0 && <div className="pane-empty">No messages in history yet.</div>}
        </div>
      </div>

      {/* Bottom Pane: Steering Input */}
      <div className="anchor-pane anchor-input-pane">
        <div className="pane-header">
          <Anchor size={16} />
          <h3>Current Anchoring Message (Live Steering)</h3>
          {isLoading && <Loader2 className="animate-spin" size={14} />}
        </div>
        
        <div className="anchor-input-body">
          <div className="anchor-form">
            <textarea
              className="anchor-textarea"
              placeholder="Enter steering instructions for the agents..."
              value={anchorText}
              onChange={(e) => setAnchorText(e.target.value)}
              disabled={isLoading || isSaving}
            />
            
            <div className="anchor-actions">
              <div className="template-picker">
                <span className="picker-label">Templates:</span>
                <div className="template-buttons">
                  {templates.map((t, i) => (
                    <button 
                      key={i} 
                      className={`btn-template ${t.default ? 'default-tmpl' : ''}`}
                      onClick={() => applyTemplate(t.text)}
                      title={t.text}
                    >
                      {t.label} {t.default && <span className="default-badge">DEFAULT</span>}
                    </button>
                  ))}
                </div>
              </div>
              
              <button 
                className={`btn-drop-anchor ${isSaving ? 'loading' : ''}`}
                onClick={handleDropAnchor}
                disabled={isLoading || isSaving || !anchorText.trim()}
              >
                {isSaving ? <Loader2 className="animate-spin" /> : <Anchor size={16} />}
                <span>Drop Anchor</span>
              </button>
            </div>
            
            {statusMsg.text && (
              <div className={`status-msg status-${statusMsg.type}`}>
                <Info size={14} />
                <span>{statusMsg.text}</span>
              </div>
            )}
          </div>

          <div className="anchor-explanation">
            <h4><Info size={14} /> How it works</h4>
            <p>
              The Anchor Message is postpended to every inference call. Unlike standard chat history, 
              it is <strong>persistent</strong> and <strong>high-priority</strong>. Use it to keep the agent 
              swarm on track when they start to drift or lose context.
            </p>
          </div>
        </div>
      </div>

      <style>{`
        .anchor-container {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: #09090b;
        }
        .anchor-pane {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-height: 0;
          border-bottom: 1px solid #18181b;
        }
        .pane-header {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 12px 16px;
          background: #111114;
          border-bottom: 1px solid #18181b;
        }
        .pane-header h3 {
          font-size: 0.85rem;
          font-weight: 600;
          color: #e4e4e7;
          margin: 0;
        }
        .anchor-context-list {
          flex: 1;
          overflow-y: auto;
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .anchor-context-item {
          font-size: 0.75rem;
          padding: 8px;
          background: #18181b;
          border-radius: 4px;
          display: flex;
          gap: 8px;
        }
        .item-actor {
          font-weight: 700;
          color: #4ade80;
          white-space: nowrap;
        }
        .item-content {
          color: #d4d4d8;
        }
        .anchor-input-body {
          flex: 1;
          display: flex;
          gap: 24px;
          padding: 20px;
          background: #0c0c0e;
        }
        .anchor-form {
          flex: 3;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .anchor-textarea {
          flex: 1;
          background: #18181b;
          border: 1px solid #27272a;
          border-radius: 6px;
          color: #fff;
          padding: 16px;
          font-size: 0.9rem;
          font-family: inherit;
          resize: none;
          transition: border-color 0.2s;
        }
        .anchor-textarea:focus {
          outline: none;
          border-color: #4ade80;
        }
        .anchor-actions {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          gap: 16px;
        }
        .template-picker {
          flex: 1;
        }
        .picker-label {
          display: block;
          font-size: 0.7rem;
          color: #71717a;
          margin-bottom: 8px;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .template-buttons {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }
        .btn-template {
          padding: 6px 10px;
          background: #18181b;
          border: 1px solid #27272a;
          border-radius: 4px;
          color: #a1a1aa;
          font-size: 0.7rem;
          cursor: pointer;
          transition: all 0.2s;
        }
        .btn-template:hover {
          border-color: #4ade80;
          color: #fff;
          background: #1f2937;
        }
        .btn-template.default-tmpl {
          border-color: #10b981;
          color: #10b981;
        }
        .default-badge {
          font-size: 0.5rem;
          background: #10b981;
          color: #fff;
          padding: 1px 3px;
          border-radius: 2px;
          margin-left: 4px;
          font-weight: 800;
        }
        .btn-drop-anchor {
          display: flex;
          align-items: center;
          gap: 10px;
          background: #10b981;
          color: #fff;
          border: none;
          border-radius: 6px;
          padding: 12px 24px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.2s;
        }
        .btn-drop-anchor:hover:not(:disabled) {
          background: #059669;
        }
        .btn-drop-anchor:disabled {
          background: #064e3b;
          opacity: 0.6;
          cursor: not-allowed;
        }
        .status-msg {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 0.8rem;
          padding: 8px 12px;
          border-radius: 4px;
        }
        .status-success {
          background: #064e3b;
          color: #4ade80;
        }
        .status-error {
          background: #450a0a;
          color: #f87171;
        }
        .anchor-explanation {
          flex: 1;
          padding: 20px;
          background: #111114;
          border: 1px solid #18181b;
          border-radius: 6px;
          height: fit-content;
        }
        .anchor-explanation h4 {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 0.8rem;
          margin: 0 0 12px 0;
          color: #4ade80;
        }
        .anchor-explanation p {
          font-size: 0.75rem;
          line-height: 1.6;
          color: #a1a1aa;
          margin: 0;
        }
        .pane-empty {
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100%;
          color: #71717a;
          font-size: 0.8rem;
        }
        .animate-spin {
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
