import { useState, useEffect } from 'react';
import { Box, Terminal as TerminalIcon, ShieldCheck, HardDrive, FolderOpen, MessageSquare, ChevronLeft, ChevronRight, User, Plus, GitBranch, BarChart3, Loader2, Waves, Anchor, Container, Settings, X } from 'lucide-react';

import { streamEvents, fetchWorkspace, fetchHistory, fetchSessions, createSession, setAnchor, fetchAnchorTemplates } from './api';
import type { ActivityEvent, Session } from './api';
import ChatroomView from './components/ChatroomView';
import ExplorerView from './components/ExplorerView';
import ConchShellPanel from './components/ConchShellPanel';
import ProjectBoard from './components/ProjectBoard';
import DagView from './components/DagView';
import MetricsView from './components/MetricsView';
import SnorkelView from './components/SnorkelView';
import AnchorView from './components/AnchorView';

// No fallback session — all sessions must be dynamic

type TabId = 'chatroom' | 'explorer' | 'dag' | 'metrics' | 'snorkel' | 'anchor';

const RUNTIME_OPTIONS = [
  { value: 'native', label: '⚡ Native (local)', mode: '', requiresDocker: false },
  { value: 'claw-sidecar-python', label: '🐍 Python 3.12', mode: 'implicit_proxy', requiresDocker: true },
  { value: 'claw-sidecar-node', label: '🟢 Node.js 20', mode: 'implicit_proxy', requiresDocker: true },
  { value: 'custom', label: '📦 Custom sidecar...', mode: 'implicit_proxy', requiresDocker: true },
];

interface SessionMeta {
  runtime: string;
  mode: string;
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [status, setStatus] = useState('Idle');
  const [risk, setRisk] = useState(0.1);
  const [fileCount, setFileCount] = useState(0);
  const [activeTab, setActiveTab] = useState<TabId>('chatroom');
  const [conchShellCollapsed, setConchShellCollapsed] = useState(false);
  const [projectBoardCollapsed, setProjectBoardCollapsed] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isInitializing, setIsInitializing] = useState(false);

  // Session creation dialog state
  const [showNewSessionDialog, setShowNewSessionDialog] = useState(false);
  const [newSessionName, setNewSessionName] = useState('');
  const [selectedRuntime, setSelectedRuntime] = useState('native');
  const [customImage, setCustomImage] = useState('');
  const [selectedDirective, setSelectedDirective] = useState('');
  const [anchorTemplates, setAnchorTemplates] = useState<{ label: string; text: string; default: boolean }[]>([]);

  // Client-side runtime metadata (not stored in backend yet)
  const [sessionMeta, setSessionMeta] = useState<Map<string, SessionMeta>>(new Map());

  useEffect(() => {
    let mounted = true;
    const loadSessions = async () => {
      if (isInitializing) return;
      setIsInitializing(true);
      try {
        const list = await fetchSessions();
        if (!mounted) return;
        setSessions(list);
        
        if (list.length > 0 && !activeSessionId) {
          setActiveSessionId(list[0].session_id);
        } else if (list.length === 0) {
          // Auto-create first session if none exist
          const newSess = await createSession("First Session");
          if (newSess && mounted) {
            setSessions([newSess]);
            setActiveSessionId(newSess.session_id);
          }
        }
      } finally {
        if (mounted) setIsInitializing(false);
      }
    };
    loadSessions();
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;

    const init = async () => {
      const history = await fetchHistory(activeSessionId);
      setEvents(history);
      refreshWorkspace();
    };

    const cleanup = streamEvents(activeSessionId, (event) => {
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
  }, [activeSessionId]);

  const refreshWorkspace = async () => {
    if (!activeSessionId) return;
    try {
      const data = await fetchWorkspace(activeSessionId);
      if (data.status === 'ok') setFileCount(data.files.length);
    } catch (e) {
      console.error('Failed to fetch workspace', e);
    }
  };

  const openNewSessionDialog = async () => {
    setNewSessionName(`Chat ${sessions.length + 1}`);
    setSelectedRuntime('native');
    setCustomImage('');
    setSelectedDirective('');
    // Load anchor templates for directive picker
    const templates = await fetchAnchorTemplates();
    setAnchorTemplates(templates);
    if (templates.length > 0) {
      const defaultTpl = templates.find(t => t.default);
      setSelectedDirective(defaultTpl ? defaultTpl.text : templates[0].text);
    }
    setShowNewSessionDialog(true);
  };

  const handleCreateSession = async () => {
    const runtimeOpt = RUNTIME_OPTIONS.find(r => r.value === selectedRuntime);
    const runtimeImage = selectedRuntime === 'native' ? '' :
                         selectedRuntime === 'custom' ? customImage :
                         selectedRuntime;
    const executionMode = runtimeOpt?.mode || '';

    const newSess = await createSession(newSessionName, runtimeImage, executionMode);
    if (newSess) {
      setSessions([newSess, ...sessions]);
      setActiveSessionId(newSess.session_id);
      // Store runtime metadata client-side
      setSessionMeta(prev => {
        const next = new Map(prev);
        next.set(newSess.session_id, {
          runtime: selectedRuntime === 'custom' ? customImage : (runtimeOpt?.label || 'native'),
          mode: executionMode || 'native',
        });
        return next;
      });
      // Auto-apply selected directive as anchor
      if (selectedDirective) {
        await setAnchor(newSess.session_id, selectedDirective);
      }
    }
    setShowNewSessionDialog(false);
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



      <div className={`main-layout ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
        <aside className="sidebar">
          {/* Collapse button */}
          <button 
            onClick={() => setSidebarCollapsed(true)}
            className="sidebar-toggle-btn"
            title="Collapse Sidebar"
          >
            <ChevronLeft size={16} />
          </button>

          {/* Session Info */}
          <div className="card card-compact">
            <User size={14} color="#a1a1aa" />
            <span className="compact-label">Active</span>
            <span className="compact-value">
              {sessions.find(s => s.session_id === activeSessionId)?.title || '...'}
            </span>
          </div>

          {/* Agent Status Card */}
          <div className="card card-compact">
            <TerminalIcon size={14} color="#a1a1aa" />
            <span className="compact-label">Status</span>
            <span className="compact-value" style={{ color: status === 'Idle' ? '#fff' : '#4ade80' }}>
              {status}
            </span>
          </div>

          {/* Workspace Card */}
          <div className="card card-compact">
            <HardDrive size={14} color="#a1a1aa" />
            <span className="compact-label">Files</span>
            <span className="compact-value">{fileCount}</span>
          </div>

          {/* Safety Check Card */}
          <div className="card card-compact">
            <ShieldCheck size={14} color="#a1a1aa" />
            <span className="compact-label">Safety</span>
            <span className="compact-value" style={{ color: riskColor }}>
              {risk > 0.7 ? 'High' : risk > 0.3 ? 'Medium' : 'Low'}
            </span>
            <div className="risk-bar-inline">
              <div 
                className="risk-fill" 
                style={{ width: `${risk * 100}%`, backgroundColor: riskColor }} 
              />
            </div>
          </div>

          {/* Session Navigation */}
          <div className="session-nav">
            <div className="session-nav-header">
              <h3>Sessions</h3>
              <button className="btn-new-session" onClick={openNewSessionDialog}>
                <Plus size={12} />
                New
              </button>
            </div>
            <div className="session-list">
              {sessions.map(s => {
                const meta = sessionMeta.get(s.session_id);
                return (
                  <div 
                    key={s.session_id} 
                    className={`session-item ${activeSessionId === s.session_id ? 'active' : ''}`}
                    onClick={() => setActiveSessionId(s.session_id)}
                  >
                    <span className="session-item-title">{s.title}</span>
                    <span className="session-item-meta">
                      {meta ? (
                        <span className="session-runtime-badge">{meta.runtime}</span>
                      ) : (
                        <span className="session-runtime-badge">⚡ Native</span>
                      )}
                      {' │ '}
                      {new Date(s.created_at).toLocaleDateString()}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Local History (Contextual) */}
          <div className="card" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', marginTop: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <TerminalIcon size={14} color="#a1a1aa" />
              <h3>Local History</h3>
            </div>
            <div className="history-list">
              {events.filter(e => e.type === 'user' || e.type === 'thought' || e.type === 'output' || e.type === 'error').reverse().map((e, i) => (
                <div key={i} className={`history-item history-${e.type}`} style={{ fontSize: '0.7rem', padding: '4px 6px', marginBottom: '4px' }}>
                  {e.content.slice(0, 40)}{e.content.length > 40 ? '...' : ''}
                </div>
              ))}
              {events.length === 0 && <div className="conchshell-empty">No activity in this session</div>}
            </div>
          </div>
        </aside>

        {sidebarCollapsed && (
          <button 
            className="sidebar-expand-btn"
            onClick={() => setSidebarCollapsed(false)}
            title="Expand Sidebar"
          >
            <ChevronRight size={18} />
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
            <button 
              className={`tab-item ${activeTab === 'dag' ? 'active' : ''}`}
              onClick={() => setActiveTab('dag')}
            >
              <GitBranch size={14} />
              <span>DAG</span>
            </button>
            <button 
              className={`tab-item ${activeTab === 'metrics' ? 'active' : ''}`}
              onClick={() => setActiveTab('metrics')}
            >
              <BarChart3 size={14} />
              <span>Metrics</span>
            </button>
            <button 
              className={`tab-item ${activeTab === 'snorkel' ? 'active' : ''}`}
              onClick={() => setActiveTab('snorkel')}
            >
              <Waves size={14} />
              <span>Snorkel</span>
            </button>
            <button 
              className={`tab-item ${activeTab === 'anchor' ? 'active' : ''}`}
              onClick={() => setActiveTab('anchor')}
            >
              <Anchor size={14} />
              <span>Anchor</span>
            </button>
          </div>

          {/* Tab Content */}
          <div className="tab-content">
            {(!activeSessionId || isInitializing) ? (
              <div className="loading-overlay">
                <Loader2 className="animate-spin" size={32} color="#4ade80" />
                <span>Initializing Session...</span>
              </div>
            ) : (
              <>
                {activeTab === 'chatroom' && (
                  <>
                    <ChatroomView 
                      events={events} 
                      setEvents={setEvents}
                      sessionId={activeSessionId}
                    />
                    <ProjectBoard 
                      sessionId={activeSessionId} 
                      refreshKey={refreshKey} 
                      collapsed={projectBoardCollapsed}
                      onToggle={() => setProjectBoardCollapsed(!projectBoardCollapsed)}
                    />
                  </>
                )}
                {activeTab === 'explorer' && (
                  <ExplorerView sessionId={activeSessionId} refreshKey={refreshKey} />
                )}
                {activeTab === 'dag' && (
                  <DagView sessionId={activeSessionId} />
                )}
                {activeTab === 'metrics' && (
                  <MetricsView sessionId={activeSessionId} />
                )}
                {activeTab === 'snorkel' && (
                  <SnorkelView events={events} sessionId={activeSessionId} />
                )}
                {activeTab === 'anchor' && (
                  <AnchorView sessionId={activeSessionId} events={events} />
                )}
              </>
            )}
          </div>
        </main>
      </div>

      {/* ConchShell Panel — always visible */}
      <ConchShellPanel
        events={events}
        collapsed={conchShellCollapsed}
        onToggle={() => setConchShellCollapsed(!conchShellCollapsed)}
      />

      {/* New Session Dialog */}
      {showNewSessionDialog && (
        <div className="modal-overlay" onClick={() => setShowNewSessionDialog(false)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2><Container size={20} /> New Studio</h2>
              <button className="modal-close" onClick={() => setShowNewSessionDialog(false)}>
                <X size={18} />
              </button>
            </div>

            <div className="modal-body">
              <label className="modal-label">Name</label>
              <input
                className="modal-input"
                value={newSessionName}
                onChange={e => setNewSessionName(e.target.value)}
                placeholder="Session name..."
                autoFocus
              />

              <label className="modal-label" style={{ marginTop: '16px' }}>
                <Settings size={14} /> Runtime
              </label>
              <div className="runtime-picker">
                {RUNTIME_OPTIONS.map(opt => (
                  <label
                    key={opt.value}
                    className={`runtime-option ${selectedRuntime === opt.value ? 'selected' : ''}`}
                  >
                    <input
                      type="radio"
                      name="runtime"
                      value={opt.value}
                      checked={selectedRuntime === opt.value}
                      onChange={() => setSelectedRuntime(opt.value)}
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
              {selectedRuntime === 'custom' && (
                <input
                  className="modal-input"
                  value={customImage}
                  onChange={e => setCustomImage(e.target.value)}
                  placeholder="e.g. ghcr.io/my-org/my-image:latest"
                  style={{ marginTop: '8px' }}
                />
              )}
              {RUNTIME_OPTIONS.find(r => r.value === selectedRuntime)?.requiresDocker && (
                <div className="docker-hint">
                  <Container size={12} />
                  <span>
                    Requires a running sidecar container on the Docker network.
                    Without one, falls back to native mode.
                  </span>
                </div>
              )}

              {anchorTemplates.length > 0 && (
                <>
                  <label className="modal-label" style={{ marginTop: '16px' }}>
                    <Anchor size={14} /> Directive
                  </label>
                  <div className="directive-picker">
                    {anchorTemplates.map((tpl, i) => (
                      <label
                        key={i}
                        className={`directive-option ${selectedDirective === tpl.text ? 'selected' : ''}`}
                      >
                        <input
                          type="radio"
                          name="directive"
                          checked={selectedDirective === tpl.text}
                          onChange={() => setSelectedDirective(tpl.text)}
                        />
                        {tpl.label}
                      </label>
                    ))}
                  </div>
                </>
              )}
            </div>

            <div className="modal-footer">
              <button className="modal-btn-cancel" onClick={() => setShowNewSessionDialog(false)}>
                Cancel
              </button>
              <button className="modal-btn-create" onClick={handleCreateSession}>
                Create Studio
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
