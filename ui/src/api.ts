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
    const resp = await fetch(`${BRIDGE_URL}/workspace/${sessionId}/file?path=${encodeURIComponent('.conchshell/board.json')}`);
    const data = await resp.json();
    if (data.content) {
      return JSON.parse(data.content);
    }
    return [];
  } catch {
    return [];
  }
};
