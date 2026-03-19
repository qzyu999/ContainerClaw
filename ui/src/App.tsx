import { useState, useEffect } from 'react';
import { Box, Terminal as TerminalIcon, ShieldCheck, HardDrive, FolderOpen, MessageSquare } from 'lucide-react';
import { motion } from 'framer-motion';
import { streamEvents, fetchWorkspace, fetchHistory } from './api';
import type { ActivityEvent } from './api';
import ChatroomView from './components/ChatroomView';
import ExplorerView from './components/ExplorerView';
import ConchShellPanel from './components/ConchShellPanel';
import ProjectBoard from './components/ProjectBoard';

const SESSION_ID = 'user-session';

type TabId = 'chatroom' | 'explorer';

export default function App() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [status, setStatus] = useState('Idle');
  const [risk, setRisk] = useState(0.1);
  const [fileCount, setFileCount] = useState(0);
  const [activeTab, setActiveTab] = useState<TabId>('chatroom');
  const [conchShellCollapsed, setConchShellCollapsed] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const init = async () => {
      const history = await fetchHistory(SESSION_ID);
      setEvents(history);
      refreshWorkspace();
    };

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

    init();
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
      </section>

      <div className={`main-layout ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
        <aside className="sidebar">
          {/* Agent Status Card */}
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <TerminalIcon size={14} color="#a1a1aa" />
              <h3>Agent Status</h3>
              <button 
                onClick={() => setSidebarCollapsed(true)}
                className="sidebar-toggle-btn"
                style={{ marginLeft: 'auto' }}
                title="Collapse Sidebar"
              >
                <MessageSquare size={14} />
              </button>
            </div>
            <div className="stat-value" style={{ color: status === 'Idle' ? '#fff' : '#4ade80' }}>
              {status}
            </div>
          </div>

          {/* Workspace Card */}
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <HardDrive size={14} color="#a1a1aa" />
              <h3>Workspace</h3>
            </div>
            <div className="stat-value">
              {fileCount}
            </div>
            <p className="stat-desc">{fileCount} files in sandbox.</p>
          </div>

          {/* Safety Check Card */}
          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <ShieldCheck size={14} color="#a1a1aa" />
              <h3>Safety Check</h3>
            </div>
            <div className="stat-value" style={{ color: riskColor, fontSize: '1rem' }}>
              {risk > 0.7 ? 'High Risk' : risk > 0.3 ? 'Medium Risk' : 'Low Risk'}
            </div>
            <div className="risk-bar">
              <div 
                className="risk-fill" 
                style={{ width: `${risk * 100}%`, backgroundColor: riskColor }} 
              />
            </div>
          </div>

          {/* Session History (Persistent in Sidebar) */}
          <div className="card" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <TerminalIcon size={14} color="#a1a1aa" />
              <h3>Session History</h3>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {events.filter(e => e.type === 'user' || e.type === 'thought' || e.type === 'output' || e.type === 'error').reverse().slice(0, 10).map((e, i) => (
                <div key={i} className={`history-item history-${e.type}`} style={{ fontSize: '0.75rem', padding: '6px' }}>
                  {e.content.slice(0, 40)}{e.content.length > 40 ? '...' : ''}
                </div>
              ))}
            </div>
          </div>
        </aside>

        {sidebarCollapsed && (
          <button 
            className="sidebar-expand-btn"
            onClick={() => setSidebarCollapsed(false)}
            title="Expand Sidebar"
          >
            <Box size={18} />
          </button>
        )}

        <main className="content" style={{ minHeight: 0 }}>
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
                  setEvents={setEvents}
                  sessionId={SESSION_ID}
                />
                <ProjectBoard sessionId={SESSION_ID} refreshKey={refreshKey} />
              </>
            )}
            {activeTab === 'explorer' && (
              <ExplorerView sessionId={SESSION_ID} refreshKey={refreshKey} />
            )}
          </div>
        </main>
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
