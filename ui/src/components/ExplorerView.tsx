import { useState, useEffect, useCallback } from 'react';
import Editor, { DiffEditor } from '@monaco-editor/react';
import { FolderOpen, FileCode, ChevronRight, ChevronDown, GitCompare, Eye } from 'lucide-react';
import { fetchFileTree, fetchFileContent, fetchFileDiff } from '../api';
import type { FileEntry, FileContent, DiffData } from '../api';

interface ExplorerViewProps {
  sessionId: string;
  refreshKey: number; // increment to trigger refresh
}

interface TreeNode {
  name: string;
  path: string;
  is_directory: boolean;
  size_bytes: number;
  children: TreeNode[];
}

function buildTree(files: FileEntry[]): TreeNode[] {
  const root: TreeNode[] = [];
  const nodeMap = new Map<string, TreeNode>();

  // Sort so directories come first, then alphabetically
  const sorted = [...files].sort((a, b) => {
    if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
    return a.path.localeCompare(b.path);
  });

  for (const file of sorted) {
    const parts = file.path.split('/');
    const name = parts[parts.length - 1];
    const node: TreeNode = {
      name,
      path: file.path,
      is_directory: file.is_directory,
      size_bytes: file.size_bytes,
      children: [],
    };
    nodeMap.set(file.path, node);

    if (parts.length === 1) {
      root.push(node);
    } else {
      const parentPath = parts.slice(0, -1).join('/');
      const parent = nodeMap.get(parentPath);
      if (parent) {
        parent.children.push(node);
      } else {
        root.push(node);
      }
    }
  }

  return root;
}

function FileTreeItem({ node, depth, selectedPath, onSelect }: {
  node: TreeNode;
  depth: number;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 2);

  if (node.is_directory) {
    return (
      <div>
        <div 
          className={`file-entry file-entry-dir`}
          style={{ paddingLeft: `${12 + depth * 16}px` }}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <FolderOpen size={14} color="#f59e0b" />
          <span>{node.name}</span>
        </div>
        {expanded && node.children.map(child => (
          <FileTreeItem 
            key={child.path} 
            node={child} 
            depth={depth + 1} 
            selectedPath={selectedPath}
            onSelect={onSelect}
          />
        ))}
      </div>
    );
  }

  return (
    <div 
      className={`file-entry ${selectedPath === node.path ? 'file-entry-selected' : ''}`}
      style={{ paddingLeft: `${12 + depth * 16}px` }}
      onClick={() => onSelect(node.path)}
    >
      <FileCode size={14} color="#60a5fa" />
      <span>{node.name}</span>
      <span className="file-size">{formatSize(node.size_bytes)}</span>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

export default function ExplorerView({ sessionId, refreshKey }: ExplorerViewProps) {
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [fileContent, setFileContent] = useState<FileContent | null>(null);
  const [diffData, setDiffData] = useState<DiffData | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadTree = useCallback(async () => {
    try {
      const tree = await fetchFileTree(sessionId);
      setFiles(tree);
    } catch (e) {
      console.error('Failed to load file tree', e);
    }
  }, [sessionId]);

  useEffect(() => {
    loadTree();
  }, [loadTree, refreshKey]);

  const handleSelect = async (path: string) => {
    setSelectedPath(path);
    setLoading(true);
    try {
      const content = await fetchFileContent(sessionId, path);
      setFileContent(content);

      const diff = await fetchFileDiff(sessionId, path);
      setDiffData(diff);
    } catch (e) {
      console.error('Failed to load file', e);
    } finally {
      setLoading(false);
    }
  };

  const treeNodes = buildTree(files);

  return (
    <div className="explorer-container">
      {/* File Tree */}
      <div className="file-tree">
        <div className="file-tree-header">
          <FolderOpen size={14} color="#f59e0b" />
          <span>WORKSPACE</span>
          <button className="tree-refresh" onClick={loadTree} title="Refresh">↻</button>
        </div>
        <div className="file-tree-body">
          {treeNodes.length === 0 ? (
            <div className="file-tree-empty">No files in workspace</div>
          ) : (
            treeNodes.map(node => (
              <FileTreeItem 
                key={node.path} 
                node={node} 
                depth={0} 
                selectedPath={selectedPath}
                onSelect={handleSelect}
              />
            ))
          )}
        </div>
      </div>

      {/* Editor Pane */}
      <div className="editor-pane">
        {selectedPath ? (
          <>
            <div className="editor-header">
              <span className="editor-filename">{selectedPath}</span>
              <div className="editor-actions">
                <button 
                  className={`editor-action-btn ${!showDiff ? 'active' : ''}`}
                  onClick={() => setShowDiff(false)}
                  title="View file"
                >
                  <Eye size={14} />
                  <span>View</span>
                </button>
                <button 
                  className={`editor-action-btn ${showDiff ? 'active' : ''}`}
                  onClick={() => setShowDiff(true)}
                  title="Show diff vs HEAD"
                  disabled={!diffData?.original && !diffData?.diff_text}
                >
                  <GitCompare size={14} />
                  <span>Diff</span>
                </button>
              </div>
            </div>
            <div className="editor-body">
              {loading ? (
                <div className="editor-loading">Loading...</div>
              ) : showDiff && diffData ? (
                <DiffEditor
                  original={diffData.original}
                  modified={diffData.modified}
                  language={fileContent?.language || 'plaintext'}
                  theme="vs-dark"
                  options={{
                    readOnly: true,
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    renderSideBySide: true,
                  }}
                />
              ) : fileContent ? (
                <Editor
                  value={fileContent.content}
                  language={fileContent.language}
                  theme="vs-dark"
                  options={{
                    readOnly: true,
                    minimap: { enabled: true },
                    fontSize: 13,
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    wordWrap: 'on',
                  }}
                />
              ) : null}
            </div>
          </>
        ) : (
          <div className="editor-empty">
            <FileCode size={48} color="#3b3b44" />
            <p>Select a file to view its contents</p>
          </div>
        )}
      </div>
    </div>
  );
}
