import { useState, useRef, useEffect } from 'react';
import type { KeyboardEvent } from 'react';
import { Loader2, Send, ChevronDown, ChevronRight, Vote } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import type { ActivityEvent } from '../api';
import { submitTask } from '../api';

interface ChatroomViewProps {
  events: ActivityEvent[];
  setEvents: React.Dispatch<React.SetStateAction<ActivityEvent[]>>;
  sessionId: string;
}

// Agent color mapping
const AGENT_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  Alice: { bg: 'rgba(167, 139, 250, 0.1)', text: '#a78bfa', border: 'rgba(167, 139, 250, 0.3)' },
  Bob: { bg: 'rgba(96, 165, 250, 0.1)', text: '#60a5fa', border: 'rgba(96, 165, 250, 0.3)' },
  Carol: { bg: 'rgba(244, 114, 182, 0.1)', text: '#f472b6', border: 'rgba(244, 114, 182, 0.3)' },
  David: { bg: 'rgba(251, 191, 36, 0.1)', text: '#fbbf24', border: 'rgba(251, 191, 36, 0.3)' },
  Eve: { bg: 'rgba(52, 211, 153, 0.1)', text: '#34d399', border: 'rgba(52, 211, 153, 0.3)' },
  Moderator: { bg: 'rgba(161, 161, 170, 0.08)', text: '#a1a1aa', border: 'rgba(161, 161, 170, 0.2)' },
  Human: { bg: 'rgba(59, 130, 246, 0.15)', text: '#60a5fa', border: 'rgba(59, 130, 246, 0.4)' },
};

const getAgentColor = (actorId: string | undefined) => {
  if (!actorId) return AGENT_COLORS.Moderator;
  return AGENT_COLORS[actorId] || AGENT_COLORS.Moderator;
};

// Format timestamp to short time
const formatTime = (timestamp: string | undefined) => {
  if (!timestamp) return '';
  return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

// Collapsible tool output component
function ToolCallCard({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  
  // Try to parse tool name from content (often starts with tool name or command)
  const firstLine = content.split('\n')[0].slice(0, 60);
  const hasMoreLines = content.includes('\n') || content.length > 60;
  
  return (
    <div className="chat-tool-card">
      <div 
        className="chat-tool-header"
        onClick={() => hasMoreLines && setExpanded(!expanded)}
        style={{ cursor: hasMoreLines ? 'pointer' : 'default' }}
      >
        <span className="chat-tool-icon">⚙</span>
        <span className="chat-tool-summary">{firstLine}{content.length > 60 && !expanded ? '...' : ''}</span>
        {hasMoreLines && (
          expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
        )}
      </div>
      {expanded && (
        <pre className="chat-tool-output">{content}</pre>
      )}
    </div>
  );
}

// Election banner component
function ElectionBanner({ content, timestamp }: { content: string; timestamp?: string }) {
  const [expanded, setExpanded] = useState(false);
  
  // Parse election result - typically "AgentName won (X/Y votes) - reason"
  const summaryMatch = content.match(/^(\w+)\s+(?:won|elected|selected).*?(?:\((\d+\/\d+)\s*votes?\))?/i);
  const summary = summaryMatch 
    ? `${summaryMatch[1]} elected${summaryMatch[2] ? ` (${summaryMatch[2]})` : ''}`
    : content.slice(0, 40);
  
  return (
    <div className="chat-election-banner">
      <div 
        className="chat-election-header"
        onClick={() => setExpanded(!expanded)}
      >
        <Vote size={14} className="chat-election-icon" />
        <span className="chat-election-summary">{summary}</span>
        <span className="chat-election-time">{formatTime(timestamp)}</span>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </div>
      {expanded && (
        <div className="chat-election-detail">{content}</div>
      )}
    </div>
  );
}

// Agent message bubble
function AgentBubble({ event }: { event: ActivityEvent }) {
  const colors = getAgentColor(event.actor_id);
  const isAction = event.type === 'action';
  const isThought = event.type === 'thought';
  
  return (
    <motion.div 
      className="chat-bubble chat-bubble-agent"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      style={{ 
        '--bubble-bg': colors.bg,
        '--bubble-border': colors.border,
        '--bubble-accent': colors.text,
      } as React.CSSProperties}
    >
      <div className="chat-bubble-header">
        <span className="chat-bubble-dot" style={{ background: colors.text }} />
        <span className="chat-bubble-name" style={{ color: colors.text }}>
          {event.actor_id || 'System'}
        </span>
        {isThought && <span className="chat-bubble-role">thinking</span>}
        <span className="chat-bubble-time">{formatTime(event.timestamp)}</span>
      </div>
      <div className="chat-bubble-body">
        {isAction ? (
          <ToolCallCard content={event.content} />
        ) : (
          <div className="chat-bubble-content">{event.content}</div>
        )}
      </div>
    </motion.div>
  );
}

// Human message bubble (right-aligned)
function HumanBubble({ event }: { event: ActivityEvent }) {
  return (
    <motion.div 
      className="chat-bubble chat-bubble-human"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="chat-bubble-content">{event.content}</div>
      <div className="chat-bubble-time-human">{formatTime(event.timestamp)}</div>
    </motion.div>
  );
}

// Error message
function ErrorBubble({ event }: { event: ActivityEvent }) {
  return (
    <motion.div 
      className="chat-bubble chat-bubble-error"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="chat-bubble-content">{event.content}</div>
    </motion.div>
  );
}

// System message (centered, muted)
function SystemMessage({ event }: { event: ActivityEvent }) {
  return (
    <motion.div 
      className="chat-system-message"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
    >
      <span className="chat-system-time">{formatTime(event.timestamp)}</span>
      <span className="chat-system-content">{event.content}</span>
    </motion.div>
  );
}

export default function ChatroomView({ 
  events, 
  setEvents,
  sessionId,
}: ChatroomViewProps) {
  const [prompt, setPrompt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState<'chat' | 'system'>('chat');
  const chatRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const shouldAutoScroll = useRef(true);
  const handleScroll = () => {
    if (chatRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = chatRef.current;
      shouldAutoScroll.current = scrollHeight - scrollTop - clientHeight < 50;
    }
  };

  useEffect(() => {
    if (chatRef.current && shouldAutoScroll.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [events, isSubmitting, activeTab]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + 'px';
    }
  }, [prompt]);

  const handleSubmit = async () => {
    if (!prompt.trim() || isSubmitting) return;

    setIsSubmitting(true);
    
    // Optimistic update
    const optimisticEvent: ActivityEvent = {
      timestamp: new Date().toISOString(),
      type: 'user',
      content: prompt.trim(),
      actor_id: 'Human',
    };
    setEvents(prev => [...prev, optimisticEvent]);

    const currentPrompt = prompt.trim();
    setPrompt('');

    try {
      const result = await submitTask(sessionId, currentPrompt);
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
      setTimeout(() => textareaRef.current?.focus(), 10);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // Filter events based on active tab
  const filteredEvents = events.filter(e => {
    if (activeTab === 'chat') {
      return ['user', 'output', 'thought', 'error', 'finish', 'action'].includes(e.type);
    } else {
      return ['system', 'voting'].includes(e.type);
    }
  });

  // Render appropriate bubble based on event type
  const renderEvent = (event: ActivityEvent, index: number) => {
    // Human messages
    if (event.type === 'user' || (event.actor_id === 'Human' && event.type === 'output')) {
      return <HumanBubble key={index} event={event} />;
    }
    
    // Election/voting results
    if (event.type === 'voting' || event.content.toLowerCase().includes('elected') || event.content.toLowerCase().includes('won')) {
      // Check if this looks like an election result
      if (event.actor_id === 'Moderator' || event.type === 'voting') {
        return <ElectionBanner key={index} content={event.content} timestamp={event.timestamp} />;
      }
    }
    
    // Error messages
    if (event.type === 'error') {
      return <ErrorBubble key={index} event={event} />;
    }
    
    // System tab messages
    if (event.type === 'system') {
      return <SystemMessage key={index} event={event} />;
    }
    
    // Agent messages (output, thought, action, finish)
    return <AgentBubble key={index} event={event} />;
  };

  return (
    <section className="chat-container">
      {/* Sub-tabs for Chat vs System */}
      <div className="chat-subtabs">
        <button 
          className={`chat-subtab ${activeTab === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveTab('chat')}
        >
          Chat
        </button>
        <button 
          className={`chat-subtab ${activeTab === 'system' ? 'active' : ''}`}
          onClick={() => setActiveTab('system')}
        >
          System & Voting
        </button>
      </div>

      {/* Messages area */}
      <div className="chat-messages" ref={chatRef} onScroll={handleScroll}>
        {filteredEvents.length === 0 && activeTab === 'system' && (
          <div className="chat-empty">No system logs yet.</div>
        )}
        {filteredEvents.length === 0 && activeTab === 'chat' && (
          <div className="chat-welcome">
            <div className="chat-welcome-title">Ready for tasks</div>
            <div className="chat-welcome-sub">
              Describe what you'd like the agents to work on.
            </div>
          </div>
        )}
        <AnimatePresence>
          {filteredEvents.map((event, i) => renderEvent(event, i))}
        </AnimatePresence>
        
        {/* Typing indicator */}
        {isSubmitting && (
          <div className="chat-typing">
            <Loader2 className="animate-spin" size={14} />
            <span>Agents are thinking...</span>
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="chat-input-area">
        <textarea
          ref={textareaRef}
          className="chat-textarea"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe your task or ask a question..."
          disabled={isSubmitting}
          rows={1}
        />
        <button 
          className="chat-send-btn"
          onClick={handleSubmit}
          disabled={isSubmitting || !prompt.trim()}
        >
          {isSubmitting ? (
            <Loader2 className="animate-spin" size={18} />
          ) : (
            <Send size={18} />
          )}
        </button>
      </div>
    </section>
  );
}
