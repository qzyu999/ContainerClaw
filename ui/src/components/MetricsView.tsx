import { useState, useEffect, useCallback } from 'react';
import { fetchMetrics } from '../api';
import type { MetricsWindow } from '../api';

interface MetricsViewProps {
  sessionId: string;
}

function Sparkline({ data, color, label, max }: { data: number[]; color: string; label: string; max?: number }) {
  const width = 280;
  const height = 48;
  const padding = 2;

  if (data.length === 0) {
    return (
      <div className="sparkline-container">
        <div className="sparkline-label">{label}</div>
        <div className="sparkline-empty">No data</div>
      </div>
    );
  }

  const maxVal = max || Math.max(...data, 1);
  const step = (width - padding * 2) / Math.max(data.length - 1, 1);

  const points = data.map((v, i) => {
    const x = padding + i * step;
    const y = height - padding - ((v / maxVal) * (height - padding * 2));
    return `${x},${y}`;
  }).join(' ');

  const areaPoints = `${padding},${height - padding} ${points} ${padding + (data.length - 1) * step},${height - padding}`;

  const current = data[data.length - 1];

  return (
    <div className="sparkline-container">
      <div className="sparkline-header">
        <span className="sparkline-label">{label}</span>
        <span className="sparkline-value" style={{ color }}>{current}</span>
      </div>
      <svg width={width} height={height} className="sparkline-svg">
        {/* Gradient fill */}
        <defs>
          <linearGradient id={`grad-${label}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.2" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={areaPoints} fill={`url(#grad-${label})`} />
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        {/* Current value dot */}
        {data.length > 0 && (
          <circle
            cx={padding + (data.length - 1) * step}
            cy={height - padding - ((current / maxVal) * (height - padding * 2))}
            r="3"
            fill={color}
          >
            <animate attributeName="opacity" values="1;0.4;1" dur="2s" repeatCount="indefinite" />
          </circle>
        )}
      </svg>
    </div>
  );
}

function StatCard({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color: string }) {
  return (
    <div className="metrics-stat-card">
      <div className="metrics-stat-label">{label}</div>
      <div className="metrics-stat-value" style={{ color }}>{value}</div>
      {sub && <div className="metrics-stat-sub">{sub}</div>}
    </div>
  );
}

export default function MetricsView({ sessionId }: MetricsViewProps) {
  const [metrics, setMetrics] = useState<MetricsWindow[]>([]);

  const loadMetrics = useCallback(async () => {
    const data = await fetchMetrics(sessionId);
    // Reverse so oldest first for sparkline rendering (data comes DESC from API)
    setMetrics([...data].reverse());
  }, [sessionId]);

  useEffect(() => {
    loadMetrics();
    const interval = setInterval(loadMetrics, 2000);
    return () => clearInterval(interval);
  }, [loadMetrics]);

  // Aggregate stats
  const totalMessages = metrics.reduce((sum, m) => sum + m.total_messages, 0);
  const totalToolCalls = metrics.reduce((sum, m) => sum + m.tool_calls, 0);
  const totalSuccesses = metrics.reduce((sum, m) => sum + m.tool_successes, 0);
  const toolEfficiency = totalToolCalls > 0 ? Math.round((totalSuccesses / totalToolCalls) * 100) : 0;

  // Sparkline data arrays
  const messageData = metrics.map(m => m.total_messages);
  const toolCallData = metrics.map(m => m.tool_calls);
  const successData = metrics.map(m => m.tool_successes);

  return (
    <div className="metrics-container">
      {metrics.length === 0 ? (
        <div className="metrics-empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#3b3b44" strokeWidth="1.5">
            <polyline points="22,6 13.5,14.5 8.5,9.5 2,16" />
            <polyline points="16,6 22,6 22,12" />
          </svg>
          <div className="metrics-empty-title">No Metrics Yet</div>
          <div className="metrics-empty-sub">
            Live pipeline metrics will stream here when telemetry is active and the Flink job is processing events.
          </div>
        </div>
      ) : (
        <>
          {/* Summary Cards */}
          <div className="metrics-summary">
            <StatCard label="Total Messages" value={totalMessages} color="#4ade80" />
            <StatCard label="Tool Calls" value={totalToolCalls} color="#60a5fa" />
            <StatCard label="Successes" value={totalSuccesses} color="#a78bfa" />
            <StatCard label="Tool Efficiency" value={`${toolEfficiency}%`} sub={totalToolCalls > 0 ? `${totalSuccesses}/${totalToolCalls}` : '—'} color={toolEfficiency >= 80 ? '#4ade80' : toolEfficiency >= 50 ? '#fbbf24' : '#ef4444'} />
          </div>

          {/* Sparklines */}
          <div className="metrics-sparklines">
            <Sparkline data={messageData} color="#4ade80" label="Messages / Window" />
            <Sparkline data={toolCallData} color="#60a5fa" label="Tool Calls / Window" />
            <Sparkline data={successData} color="#a78bfa" label="Tool Successes / Window" />
          </div>

          {/* Raw Data Table */}
          <div className="metrics-table-wrap">
            <table className="metrics-table">
              <thead>
                <tr>
                  <th>Window</th>
                  <th>Messages</th>
                  <th>Tool Calls</th>
                  <th>Successes</th>
                </tr>
              </thead>
              <tbody>
                {[...metrics].reverse().slice(0, 20).map((m, i) => (
                  <tr key={i}>
                    <td className="metrics-ts">
                      {new Date(m.window_start).toLocaleTimeString()}
                    </td>
                    <td>{m.total_messages}</td>
                    <td>{m.tool_calls}</td>
                    <td>{m.tool_successes}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
