import { useRef } from 'react';
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  AlertTriangle,
  Clock3,
  Gauge,
  Radio,
  Server,
  Users,
  Wifi,
} from 'lucide-react';
import { TelemetryCanvas } from './components/TelemetryCanvas';
import type { QueueZoneTelemetry } from './hooks/useEdgeTelemetry';
import { useEdgeTelemetry } from './hooks/useEdgeTelemetry';

type Tone = 'neutral' | 'success' | 'warning' | 'danger';

type MetricCardDefinition = {
  icon: LucideIcon;
  label: string;
  note: string;
  tone: Tone;
  value: string;
};

type GuideItemDefinition = {
  body: string;
  title: string;
};

function formatCount(value: number) {
  return `${Math.round(value)}`;
}

function formatWait(value: number) {
  if (value >= 60) {
    return `${(value / 60).toFixed(1)} min`;
  }

  return `${value.toFixed(1)} s`;
}

function formatDensity(value: number) {
  return value.toFixed(2);
}

function formatLatency(value: number) {
  return `${Math.round(value)} ms`;
}

function formatDropRate(value: number) {
  return `${(value * 100).toFixed(2)}%`;
}

function formatTimestamp(value: number | null) {
  if (!value) {
    return 'Awaiting first frame';
  }

  return new Date(value).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function getPressureState(
  density: number,
  waitSec: number,
  connection: 'connecting' | 'live' | 'offline',
): { label: string; note: string; tone: Tone } {
  if (connection !== 'live') {
    return {
      label: 'Standby',
      note: 'The dashboard is ready and waiting for live telemetry from the edge node.',
      tone: 'neutral',
    };
  }

  if (density >= 0.8 || waitSec >= 300) {
    return {
      label: 'Critical',
      note: 'Queue pressure is high. Density or wait time has crossed the highest alert band.',
      tone: 'danger',
    };
  }

  if (density >= 0.55 || waitSec >= 120) {
    return {
      label: 'Elevated',
      note: 'Queue conditions are rising and should be monitored closely.',
      tone: 'warning',
    };
  }

  return {
    label: 'Stable',
    note: 'Queue activity is currently within the normal operating range.',
    tone: 'success',
  };
}

function getConnectionTone(
  connection: 'connecting' | 'live' | 'offline',
): Tone {
  if (connection === 'live') {
    return 'success';
  }

  if (connection === 'offline') {
    return 'danger';
  }

  return 'neutral';
}

function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const {
    connection,
    connectionLabel,
    lastFrameAt,
    metrics,
    network,
    overlay,
    queueZones,
  } = useEdgeTelemetry(canvasRef);

  const pressureState = getPressureState(
    metrics.density,
    metrics.waitSec,
    connection,
  );

  const liveMetrics: MetricCardDefinition[] = [
    {
      icon: Users,
      label: 'Detected people',
      note: 'All active people currently tracked in the frame.',
      tone: 'neutral',
      value: formatCount(metrics.people),
    },
    {
      icon: Activity,
      label: 'People in queue',
      note: 'Sum of queued people across all configured queue zones.',
      tone: 'warning',
      value: formatCount(metrics.queued),
    },
    {
      icon: Clock3,
      label: 'Longest queue wait',
      note: 'Highest predicted wait across the configured queue zones.',
      tone: 'danger',
      value: formatWait(metrics.waitSec),
    },
    {
      icon: Gauge,
      label: 'Crowd density',
      note: 'Combined density across all configured queue zones.',
      tone: 'success',
      value: formatDensity(metrics.density),
    },
    {
      icon: Radio,
      label: '5G latency',
      note: 'Current round-trip network latency reported by the node.',
      tone: 'neutral',
      value: formatLatency(metrics.latency),
    },
    {
      icon: AlertTriangle,
      label: 'Packet loss',
      note: 'Current packet drop probability exposed by the backend.',
      tone: 'warning',
      value: formatDropRate(network.dropProb),
    },
  ];

  const guideItems: GuideItemDefinition[] = [
    {
      title: 'Detected people',
      body: 'The total number of active tracked people visible in the camera view.',
    },
    {
      title: 'People in queue',
      body: 'The subset of tracked people that remain inside a configured queue zone long enough to count as waiting.',
    },
    {
      title: 'Longest queue wait',
      body: 'The aggregate wait KPI now reflects the slowest queue so the worst active lane is easy to spot.',
    },
    {
      title: 'Density, latency and packet loss',
      body: 'Use these together to understand both crowd pressure and the health of the transport link.',
    },
  ];

  return (
    <div className="app-shell">
      <main className="page">
        <header className="page-header">
          <div className="header-copy">
            <div className="eyebrow">Queue Monitoring Dashboard</div>
            <h1 className="page-title">5G Edge Queue Analytics</h1>
            <p className="page-subtitle">
              Live monitoring for queue occupancy, crowd density, wait-time
              estimation and network health from the configured edge camera.
            </p>
          </div>

          <div className="header-statuses">
            <StatusPill
              icon={Wifi}
              label={connectionLabel}
              tone={getConnectionTone(connection)}
            />
            <StatusPill
              icon={Radio}
              label={network.realCamera ? 'Real camera mode' : 'Simulation mode'}
              tone={network.realCamera ? 'success' : 'neutral'}
            />
            <StatusPill
              icon={Server}
              label={`Profile: ${network.profile}`}
              tone="neutral"
            />
          </div>
        </header>

        <section className="top-grid">
          <article className="panel-card feed-card">
            <div className="card-head">
              <div>
                <span className="card-kicker">Live feed</span>
                <h2>Camera preview with ROI overlay</h2>
              </div>
              <span className={`state-badge tone-${pressureState.tone}`}>
                {pressureState.label}
              </span>
            </div>

            <p className="card-description">
              The browser renders the preview frame and overlays the live queue
              region plus tracked detections from the edge stream.
            </p>

            <TelemetryCanvas canvasRef={canvasRef} connection={connection} />

            <div className="feed-details">
              <InfoTile
                label="Last rendered frame"
                value={formatTimestamp(lastFrameAt)}
              />
              <InfoTile
                label="Queue zones"
                value={`${overlay.queueZones}`}
              />
              <InfoTile
                label="Tracked boxes"
                value={`${overlay.trackedObjects}`}
              />
            </div>
          </article>

          <aside className="side-rail">
            <section className="panel-card metrics-panel">
              <div className="card-head stacked">
                <div>
                  <span className="card-kicker">Core metrics</span>
                  <h2>Live queue KPIs</h2>
                </div>
              </div>

              <p className="panel-note">
                These are the primary values to watch while the live feed is active.
              </p>

              <div className="metric-grid metric-grid-rail">
                {liveMetrics.map((metric) => (
                  <article key={metric.label} className={`metric-card tone-${metric.tone}`}>
                    <div className="metric-icon">
                      <metric.icon size={18} strokeWidth={2.2} />
                    </div>
                    <div className="metric-label">{metric.label}</div>
                    <div className="metric-value">{metric.value}</div>
                    <p className="metric-note">{metric.note}</p>
                  </article>
                ))}
              </div>
            </section>

            <section className="panel-card queue-zones-card">
              <div className="card-head stacked">
                <div>
                  <span className="card-kicker">Queue breakdown</span>
                  <h2>Per-queue live status</h2>
                </div>
              </div>

              <p className="context-summary">
                Each configured queue polygon is tracked independently. A person
                only counts for the queue zone that contains their tracked
                position, so different lanes can now be monitored separately.
              </p>

              <div className="zone-list">
                {queueZones.length > 0 ? (
                  queueZones.map((queueZone) => (
                    <QueueZoneCard
                      key={queueZone.id}
                      queueZone={queueZone}
                    />
                  ))
                ) : (
                  <div className="zone-empty-state">
                    Queue-zone telemetry will appear here once the backend starts
                    streaming metadata.
                  </div>
                )}
              </div>
            </section>
          </aside>
        </section>

        <section className="bottom-grid">
          <article className="panel-card guide-card">
            <div className="card-head stacked">
              <div>
                <span className="card-kicker">Metric guide</span>
                <h2>How to read this dashboard</h2>
              </div>
            </div>

            <div className="guide-list">
              {guideItems.map((item) => (
                <div key={item.title} className="guide-item">
                  <strong>{item.title}</strong>
                  <p>{item.body}</p>
                </div>
              ))}
            </div>
          </article>

          <article className="panel-card summary-card">
            <div className="card-head stacked">
              <div>
                <span className="card-kicker">System summary</span>
                <h2>Deployment overview</h2>
              </div>
            </div>

            <p className="summary-copy">
              Video is processed at the edge node, while the browser receives a
              live preview plus queue telemetry for immediate situational
              awareness. Multiple queue zones can now be defined and tracked
              independently, so the dashboard can distinguish between separate
              lanes instead of treating the whole scene as one standing area.
            </p>

            <div className="summary-grid">
              <SummaryStat
                label="Inference path"
                value="Edge processing"
              />
              <SummaryStat
                label="Client rendering"
                value="Queue zones + boxes in browser"
              />
              <SummaryStat
                label="Network profile"
                value={network.profile}
              />
              <SummaryStat
                label="Queue zones"
                value={`${queueZones.length}`}
              />
              <SummaryStat
                label="Latest update"
                value={formatTimestamp(lastFrameAt)}
              />
            </div>
          </article>
        </section>
      </main>
    </div>
  );
}

type StatusPillProps = {
  icon: LucideIcon;
  label: string;
  tone: Tone;
};

function StatusPill({ icon: Icon, label, tone }: StatusPillProps) {
  return (
    <span className={`status-pill tone-${tone}`}>
      <Icon size={14} strokeWidth={2.3} />
      {label}
    </span>
  );
}

type InfoTileProps = {
  label: string;
  value: string;
};

function InfoTile({ label, value }: InfoTileProps) {
  return (
    <div className="info-tile">
      <span className="info-label">{label}</span>
      <strong className="info-value">{value}</strong>
    </div>
  );
}

type QueueZoneCardProps = {
  queueZone: QueueZoneTelemetry;
};

function QueueZoneCard({ queueZone }: QueueZoneCardProps) {
  return (
    <article className="zone-card">
      <div className="zone-card-head">
        <div className="zone-title-group">
          <span
            className="zone-color-dot"
            style={{ backgroundColor: queueZone.color }}
          />
          <strong>{queueZone.name}</strong>
        </div>
        <span className="zone-threshold">
          {queueZone.queueWaitThresholdSec.toFixed(1)}s threshold
        </span>
      </div>

      <div className="zone-metric-grid">
        <ZoneMetric label="Detected" value={`${queueZone.peopleDetected}`} />
        <ZoneMetric label="Queued" value={`${queueZone.peopleInQueue}`} />
        <ZoneMetric label="Density" value={queueZone.density.toFixed(2)} />
        <ZoneMetric label="Wait" value={formatWait(queueZone.estimatedWait)} />
      </div>
    </article>
  );
}

type ZoneMetricProps = {
  label: string;
  value: string;
};

function ZoneMetric({ label, value }: ZoneMetricProps) {
  return (
    <div className="zone-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

type SummaryStatProps = {
  label: string;
  value: ReactNode;
};

function SummaryStat({ label, value }: SummaryStatProps) {
  return (
    <div className="summary-stat">
      <span className="summary-label">{label}</span>
      <strong className="summary-value">{value}</strong>
    </div>
  );
}

export default App;
