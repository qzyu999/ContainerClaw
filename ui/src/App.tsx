import { useState, useEffect, useRef } from 'react';
import { Terminal as TerminalIcon, ShieldCheck, HardDrive, History, Send, Box, Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { streamEvents, submitTask, fetchWorkspace } from './api';
import type { ActivityEvent } from './api';

const SESSION_ID = 'user-session';

export default function App() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [prompt, setPrompt] = useState('');
  const [status, setStatus] = useState('Idle');
  const [risk, setRisk] = useState(0.1);
  const [files, setFiles] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const cleanup = streamEvents(SESSION_ID, (event) => {
      setEvents(prev => [...prev, event]);
      
      if (event.risk_score !== undefined) {
        setRisk(event.risk_score);
      }

      if (event.type === 'thought') {
        setStatus('Thinking...');
      } else if (event.type === 'action') {
        setStatus('Executing...');
      } else if (event.type === 'output') {
        setStatus('Agent responding...');
      } else if (event.type === 'finish' || event.type === 'error') {
        setStatus('Idle');
        refreshWorkspace();
      }
    });

    refreshWorkspace();
    return cleanup;
  }, []);

  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [events]);

  const refreshWorkspace = async () => {
    try {
      const data = await fetchWorkspace(SESSION_ID);
      if (data.status === 'ok') setFiles(data.files);
    } catch (e) {
      console.error('Failed to fetch workspace', e);
    }
  };

  const handleExecute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt || isSubmitting) return;

    setIsSubmitting(true);
    setEvents(prev => [...prev, {
      timestamp: new Date().toISOString(),
      type: 'user',
      content: prompt
    }]);

    const currentPrompt = prompt;
    setPrompt('');

    try {
      const result = await submitTask(SESSION_ID, currentPrompt);
      if (result.status === 'error') {
        setEvents(prev => [...prev, {
          timestamp: new Date().toISOString(),
          type: 'error',
          content: result.message
        }]);
      }
    } catch (err) {
      setEvents(prev => [...prev, {
        timestamp: new Date().toISOString(),
        type: 'error',
        content: 'Failed to send task to bridge.'
      }]);
    } finally {
      setIsSubmitting(false);
    }
  };

  const riskColor = risk > 0.7 ? '#ef4444' : risk > 0.3 ? '#f59e0b' : '#4ade80';

  return (
    <div className="container">
      <div className="glass-bg" />
      
      <header className="dashboard-header">
        <div className="logo">
          <Box size={32} color="#4ade80" />
          <h1>ContainerClaw</h1>
        </div>
        <div className="status-badge">
          <span className={`pulse ${status === 'Idle' ? '' : 'active'}`} />
          {status}
        </div>
      </header>

      <section className="hero">
        <motion.h2
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          Secure Agent Sandbox
        </motion.h2>
        <p>Session <span style={{ color: '#fff', fontWeight: 600 }}>{SESSION_ID}</span> is isolated and ready.</p>
        
        <form onSubmit={handleExecute} className="task-form">
          <input 
            type="text" 
            placeholder="Ask your agent (e.g. 'check my files')" 
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            autoComplete="off"
            disabled={isSubmitting}
          />
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? <Loader2 className="animate-spin" size={20} /> : <Send size={20} />}
          </button>
        </form>
      </section>

      <div className="stats-grid">
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <TerminalIcon size={16} color="#a1a1aa" />
            <h3>Agent Status</h3>
          </div>
          <div className="stat-value" style={{ color: status === 'Idle' ? '#fff' : '#4ade80' }}>
            {status}
          </div>
          <p className="stat-desc">Execution lifecycle monitor.</p>
        </div>

        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <HardDrive size={16} color="#a1a1aa" />
            <h3>Workspace</h3>
          </div>
          <div className="stat-value" style={{ fontSize: '1rem', height: '1.5rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {files.length > 0 ? files.join(', ') : 'Empty'}
          </div>
          <p className="stat-desc">{files.length} files in sandbox.</p>
        </div>

        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <ShieldCheck size={16} color="#a1a1aa" />
            <h3>Safety Check</h3>
          </div>
          <div className="stat-value" style={{ color: riskColor }}>
            {risk > 0.7 ? 'High Risk' : risk > 0.3 ? 'Medium Risk' : 'Low Risk'}
          </div>
          <div className="risk-bar">
            <div 
              className="risk-fill" 
              style={{ width: `${risk * 100}%`, backgroundColor: riskColor }} 
            />
          </div>
        </div>
      </div>

      <main className="main-content">
        <aside className="sidebar">
          <div className="card" style={{ height: '100%', overflow: 'hidden', display: 'flex', flex: 1, flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
              <History size={16} color="#a1a1aa" />
              <h3>Session History</h3>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {events.filter(e => e.type === 'user' || e.type === 'thought').reverse().map((e, i) => (
                <div key={i} className="history-item" onClick={() => setPrompt(e.content)}>
                  {e.content.slice(0, 50)}{e.content.length > 50 ? '...' : ''}
                </div>
              ))}
            </div>
          </div>
        </aside>

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
              {events.map((event, i) => (
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
      </main>
    </div>
  );
}
