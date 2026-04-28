export interface ActivityEvent {
  timestamp: string;
  type: 'thought' | 'action' | 'error' | 'finish' | 'user' | 'system' | 'output' | 'voting';
  content: string;
  risk_score?: number;
  actor_id?: string;
}

export interface FileEntry {
  path: string;
  is_directory: boolean;
  size_bytes: number;
  modified_at: string;
}

export interface FileContent {
  content: string;
  language: string;
  path: string;
}

export interface DiffData {
  original: string;
  modified: string;
  diff_text: string;
}

export interface WorkspaceResponse {
  status: string;
  files: FileEntry[];
}

export interface TaskResponse {
  status: string;
  message: string;
}

export interface BoardItem {
  id: string;
  type: string;
  title: string;
  description: string;
  status: string;
  assigned_to: string | null;
  created_at: number;
}

export interface Session {
  session_id: string;
  title: string;
  created_at: number;
  last_active_at: number;
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

export const fetchHistory = async (sessionId: string): Promise<ActivityEvent[]> => {
  const resp = await fetch(`${BRIDGE_URL}/history/${sessionId}`);
  const data = await resp.json();
  if (data.status === 'ok') {
    return data.events || [];
  }
  return [];
};

export const fetchWorkspace = async (sessionId: string): Promise<WorkspaceResponse> => {
  const resp = await fetch(`${BRIDGE_URL}/workspace/${sessionId}`);
  return resp.json();
};

export const fetchFileTree = async (sessionId: string): Promise<FileEntry[]> => {
  const resp = await fetch(`${BRIDGE_URL}/workspace/${sessionId}/tree`);
  const data = await resp.json();
  return data.files || [];
};

export const fetchFileContent = async (sessionId: string, path: string): Promise<FileContent> => {
  const resp = await fetch(`${BRIDGE_URL}/workspace/${sessionId}/file?path=${encodeURIComponent(path)}`);
  const data = await resp.json();
  return { content: data.content, language: data.language, path: data.path };
};

export const fetchFileDiff = async (sessionId: string, path: string): Promise<DiffData> => {
  const resp = await fetch(`${BRIDGE_URL}/workspace/${sessionId}/diff?path=${encodeURIComponent(path)}`);
  const data = await resp.json();
  return { original: data.original, modified: data.modified, diff_text: data.diff_text };
};

export const fetchBoardData = async (sessionId: string): Promise<BoardItem[]> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/board/${sessionId}`);
    const data = await resp.json();
    if (data.status === 'ok') {
      return data.items || [];
    }
    return [];
  } catch {
    return [];
  }
};

export const fetchSessions = async (): Promise<Session[]> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/sessions`);
    const data = await resp.json();
    if (data.status === 'ok') return data.sessions;
    return [];
  } catch {
    return [];
  }
};

export const createSession = async (
  title?: string,
  runtime_image?: string,
  execution_mode?: string
): Promise<Session | null> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/sessions/new`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, runtime_image, execution_mode }),
    });
    const data = await resp.json();
    if (data.status === 'ok') return data.session;
    return null;
  } catch {
    return null;
  }
};

// ── Telemetry API ───────────────────────────────────────────────

export interface DagEdge {
  parent: string;
  child: string;
  parent_label?: string;
  child_label?: string;
  status: 'ACTIVE' | 'THINKING' | 'DONE';
  updated_at: number;
  ts?: number;
  content?: string;
  actor?: string;
}

export interface MetricsWindow {
  window_start: number;
  total_messages: number;
  tool_calls: number;
  tool_successes: number;
  avg_latency_ms: number;
}

export const fetchDagEdges = async (sessionId: string): Promise<DagEdge[]> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/telemetry/dag/${sessionId}`);
    const data = await resp.json();
    if (data.status === 'ok') return data.edges || [];
    return [];
  } catch {
    return [];
  }
};

export const fetchMetrics = async (sessionId: string): Promise<MetricsWindow[]> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/telemetry/metrics/${sessionId}`);
    const data = await resp.json();
    if (data.status === 'ok') return data.metrics || [];
    return [];
  } catch {
    return [];
  }
};

export interface PerspectiveMessage {
  role: string;
  content: string;
}

export const fetchSnorkelPerspective = async (
  sessionId: string,
  ts: string,
  actorId: string
): Promise<PerspectiveMessage[]> => {
  try {
    // Determine the query parameter structure for the backend
    const params = new URLSearchParams({ ts, actor_id: actorId });
    const resp = await fetch(`${BRIDGE_URL}/telemetry/snorkel/${sessionId}?${params.toString()}`);
    const data = await resp.json();
    if (data.status === 'ok') {
      return data.perspective || [];
    }
    return [];
  } catch {
    return [];
  }
};

export interface RawHistoryEvent {
  actor_id: string;
  content: string;
  ts: number;
}

export const fetchRawHistory = async (
  sessionId: string,
  ts: string
): Promise<RawHistoryEvent[]> => {
  try {
    const params = new URLSearchParams({ ts });
    const resp = await fetch(`${BRIDGE_URL}/telemetry/snorkel/${sessionId}/raw?${params.toString()}`);
    const data = await resp.json();
    if (data.status === 'ok') {
      return data.history || [];
    }
    return [];
  } catch {
    return [];
  }
};

export const fetchAnchor = async (sessionId: string): Promise<string> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/session/${sessionId}/anchor`);
    const data = await resp.json();
    if (data.status === 'ok') return data.content || '';
    return '';
  } catch {
    return '';
  }
};

export const setAnchor = async (sessionId: string, content: string): Promise<boolean> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/session/${sessionId}/anchor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, author: 'operator' }),
    });
    const data = await resp.json();
    return data.status === 'ok';
  } catch {
    return false;
  }
};

export const fetchAnchorTemplates = async (): Promise<{ label: string, text: string, default: boolean }[]> => {
  try {
    const resp = await fetch(`${BRIDGE_URL}/anchor/templates`);
    const data = await resp.json();
    if (data.status === 'ok') return data.templates || [];
    return [];
  } catch {
    return [];
  }
};
