import { useState, useRef, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import type { ActivityEvent } from '../api';
import { submitTask } from '../api';

interface ChatroomViewProps {
  events: ActivityEvent[];
  setEvents: React.Dispatch<React.SetStateAction<ActivityEvent[]>>;
  sessionId: string;
}

export default function ChatroomView({ 
  events, 
  setEvents,
  sessionId,
}: ChatroomViewProps) {
  const [prompt, setPrompt] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const shouldAutoScroll = useRef(true);
  const handleScroll = () => {
    if (terminalRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = terminalRef.current;
      // If user is within 50px of the bottom, enable auto-scroll
      shouldAutoScroll.current = scrollHeight - scrollTop - clientHeight < 50;
    }
  };

  useEffect(() => {
    if (terminalRef.current && shouldAutoScroll.current) {
      terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
    }
  }, [events, isSubmitting]);

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
      // Re-focus input after submission
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  };

  // Filter non-action events for the chatroom terminal
  const chatEvents = events.filter(e => e.type !== 'action');

  return (
    <section className="terminal-container" style={{ height: '600px' }}>
        <div className="terminal-header">
          <div className="dot" style={{ backgroundColor: '#ff5f56' }} />
          <div className="dot" style={{ backgroundColor: '#ffbd2e' }} />
          <div className="dot" style={{ backgroundColor: '#27c93f' }} />
          <span style={{ marginLeft: '8px', fontSize: '0.75rem', color: '#6b7280', fontFamily: 'var(--font-mono)' }}>
            bash — containerclaw — 80x24
          </span>
        </div>
        <div className="terminal-body" ref={terminalRef} onScroll={handleScroll} onClick={() => inputRef.current?.focus()}>
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
                className={`log-line ${event.type === 'user' ? 'user-line' : ''}`}
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
          
          <form onSubmit={handleExecute} className="terminal-prompt-line">
            <span className="terminal-prompt-prefix">user@containerclaw:~$</span>
            <input
              ref={inputRef}
              type="text"
              className="terminal-input"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={isSubmitting}
              autoComplete="off"
              autoFocus
            />
            {isSubmitting && <Loader2 className="animate-spin" size={14} color="#4ade80" />}
          </form>
        </div>
      </section>
  );
}
