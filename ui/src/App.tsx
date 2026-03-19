import { useState, useEffect } from 'react';
import { Box, Loader2, Send, Terminal as TerminalIcon, ShieldCheck, HardDrive, FolderOpen, MessageSquare } from 'lucide-react';
import { motion } from 'framer-motion';
import { streamEvents, submitTask, fetchWorkspace } from './api';
import type { ActivityEvent } from './api';
import ChatroomView from './components/ChatroomView';
import ExplorerView from './components/ExplorerView';
import ConchShellPanel from './components/ConchShellPanel';
import ProjectBoard from './components/ProjectBoard';

const SESSION_ID = 'user-session';

type TabId = 'chatroom' | 'explorer';

export default function App() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [prompt, setPrompt] = useState('');
  const [status, setStatus] = useState('Idle');
  const [risk, setRisk] = useState(0.1);
  const [fileCount, setFileCount] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>('chatroom');
  const [conchShellCollapsed, setConchShellCollapsed] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

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
        setRefreshKey(k => k + 1);
      }
    });

    refreshWorkspace();
    return cleanup;
  }, []);

  const refreshWorkspace = async () => {
    try {
      const data = await fetchWorkspace(SESSION_ID);
      if (data.status === 'ok') setFileCount(data.files.length);
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
    } catch {
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
  const actionCount = events.filter(e => e.type === 'action').length;

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
          <div className="stat-value" style={{ fontSize: '1.5rem' }}>
            {fileCount}
          </div>
          <p className="stat-desc">{fileCount} files in sandbox.</p>
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

      {/* Tab Bar */}
      <div className="tab-bar">
        <button 
          className={`tab-item ${activeTab === 'chatroom' ? 'active' : ''}`}
          onClick={() => setActiveTab('chatroom')}
        >
          <MessageSquare size={14} />
          <span>Chatroom</span>
        </button>
        <button 
          className={`tab-item ${activeTab === 'explorer' ? 'active' : ''}`}
          onClick={() => setActiveTab('explorer')}
        >
          <FolderOpen size={14} />
          <span>Explorer</span>
          {fileCount > 0 && <span className="tab-badge">{fileCount}</span>}
        </button>
      </div>

      {/* Tab Content */}
      <div className="tab-content">
        {activeTab === 'chatroom' && (
          <>
            <ChatroomView 
              events={events} 
              onPromptClick={(content) => setPrompt(content)} 
            />
            <ProjectBoard sessionId={SESSION_ID} refreshKey={refreshKey} />
          </>
        )}
        {activeTab === 'explorer' && (
          <ExplorerView sessionId={SESSION_ID} refreshKey={refreshKey} />
        )}
      </div>

      {/* ConchShell Panel — always visible */}
      {actionCount > 0 && (
        <ConchShellPanel
          events={events}
          collapsed={conchShellCollapsed}
          onToggle={() => setConchShellCollapsed(!conchShellCollapsed)}
        />
      )}
    </div>
  );
}
