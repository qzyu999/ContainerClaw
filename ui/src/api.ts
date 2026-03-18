export interface ActivityEvent {
  timestamp: string;
  type: 'thought' | 'action' | 'error' | 'finish' | 'user' | 'system' | 'output';
  content: string;
  risk_score?: number;
  actor_id?: string;
}

export interface WorkspaceResponse {
  status: string;
  files: string[];
}

export interface TaskResponse {
  status: string;
  message: string;
}

const BRIDGE_URL = 'http://localhost:5001';

export const streamEvents = (sessionId: string, onEvent: (event: ActivityEvent) => void) => {
  const eventSource = new EventSource(`${BRIDGE_URL}/events/${sessionId}`);
  
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onEvent(data);
    } catch (err) {
      console.error('Failed to parse event data', err);
    }
  };

  eventSource.onerror = (err) => {
    console.error('EventSource failed', err);
    onEvent({
      timestamp: new Date().toISOString(),
      type: 'error',
      content: 'Connection to bridge lost. Retrying...',
    });
  };

  return () => eventSource.close();
};

export const submitTask = async (sessionId: string, prompt: string): Promise<TaskResponse> => {
  const resp = await fetch(`${BRIDGE_URL}/task`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, session_id: sessionId }),
  });
  return resp.json();
};

export const fetchWorkspace = async (sessionId: string): Promise<WorkspaceResponse> => {
  const resp = await fetch(`${BRIDGE_URL}/workspace/${sessionId}`);
  return resp.json();
};
