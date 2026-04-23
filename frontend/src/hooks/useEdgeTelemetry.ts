import type { RefObject } from 'react';
import { useEffect, useState } from 'react';
import { io } from 'socket.io-client';

type QueueBox = {
  color?: string;
  cx?: number;
  cy?: number;
  id?: number | string;
  queue_zone_id?: string;
  queue_zone_name?: string;
  status?: string;
  time?: number;
  x1?: number;
  x2?: number;
  y1?: number;
  y2?: number;
};

type QueueZonePayload = {
  area_sqm?: number;
  color?: string;
  density?: number;
  estimated_wait?: number;
  id?: string;
  name?: string;
  people_detected?: number;
  people_in_queue?: number;
  polygon?: number[][];
  queue_wait_threshold_sec?: number;
};

type MetadataPayload = {
  aggregate_wait_mode?: string;
  boxes?: QueueBox[];
  network?: {
    drop_prob?: number;
    latency_ms?: number;
    profile?: string;
    real_camera?: boolean;
  };
  queue_zones?: QueueZonePayload[];
  roi?: number[][];
};

type VideoFramePayload = {
  image?: string;
  latency_ms?: number;
  real_camera?: boolean;
};

export type TelemetryMetrics = {
  density: number;
  latency: number;
  people: number;
  queued: number;
  waitSec: number;
};

export type QueueZoneTelemetry = {
  areaSqm: number;
  color: string;
  density: number;
  estimatedWait: number;
  id: string;
  name: string;
  peopleDetected: number;
  peopleInQueue: number;
  polygon: number[][];
  queueWaitThresholdSec: number;
};

type HistorySeries = {
  density: number[];
  latency: number[];
  queue: number[];
  wait: number[];
};

type NetworkState = {
  dropProb: number;
  profile: string;
  realCamera: boolean;
};

type OverlayState = {
  polygonPoints: number;
  queueZones: number;
  trackedObjects: number;
};

const HISTORY_LENGTH = 28;
const DEFAULT_ZONE_COLOR = '#0f766e';

function createHistory() {
  return Array.from({ length: HISTORY_LENGTH }, () => 0);
}

function appendPoint(series: number[], value: number) {
  return [...series.slice(-(HISTORY_LENGTH - 1)), value];
}

function resolveBackendUrl() {
  const envUrl = import.meta.env.VITE_BACKEND_URL;

  if (envUrl && envUrl.trim()) {
    return envUrl.trim();
  }

  if (typeof window === 'undefined') {
    return 'http://localhost:5000';
  }

  return `${window.location.protocol}//${window.location.hostname}:5000`;
}

async function readPayloadBuffer(payload: unknown) {
  if (payload instanceof ArrayBuffer) {
    return payload;
  }

  if (payload instanceof Blob) {
    return payload.arrayBuffer();
  }

  if (ArrayBuffer.isView(payload)) {
    const start = payload.byteOffset;
    const end = start + payload.byteLength;
    return payload.buffer.slice(start, end);
  }

  return null;
}

function normalizeQueueZones(payload?: QueueZonePayload[]): QueueZoneTelemetry[] {
  if (!payload?.length) {
    return [];
  }

  return payload.map((queueZone, index) => ({
    areaSqm: queueZone.area_sqm ?? 0,
    color: queueZone.color ?? DEFAULT_ZONE_COLOR,
    density: queueZone.density ?? 0,
    estimatedWait: queueZone.estimated_wait ?? 0,
    id: queueZone.id ?? `queue_${index + 1}`,
    name: queueZone.name ?? `Queue ${index + 1}`,
    peopleDetected: queueZone.people_detected ?? 0,
    peopleInQueue: queueZone.people_in_queue ?? 0,
    polygon: queueZone.polygon ?? [],
    queueWaitThresholdSec: queueZone.queue_wait_threshold_sec ?? 0,
  }));
}

function drawZone(
  ctx: CanvasRenderingContext2D,
  queueZone: QueueZoneTelemetry,
  index: number,
) {
  if (queueZone.polygon.length < 2) {
    return;
  }

  const strokeColor = queueZone.color || DEFAULT_ZONE_COLOR;
  const firstPoint = queueZone.polygon[0];

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(firstPoint[0], firstPoint[1]);
  for (let pointIndex = 1; pointIndex < queueZone.polygon.length; pointIndex += 1) {
    ctx.lineTo(queueZone.polygon[pointIndex][0], queueZone.polygon[pointIndex][1]);
  }
  ctx.closePath();
  ctx.lineWidth = 3;
  ctx.strokeStyle = strokeColor;
  ctx.setLineDash([10, 8]);
  ctx.stroke();
  ctx.restore();

  const label = queueZone.name || `Queue ${index + 1}`;
  const labelX = firstPoint[0] + 8;
  const labelY = Math.max(24, firstPoint[1] - 12);

  ctx.save();
  ctx.font = 'bold 12px Segoe UI, sans-serif';
  const labelWidth = ctx.measureText(label).width + 16;
  ctx.fillStyle = 'rgba(8, 15, 26, 0.75)';
  ctx.fillRect(labelX - 6, labelY - 14, labelWidth, 22);
  ctx.fillStyle = strokeColor;
  ctx.fillText(label, labelX, labelY);
  ctx.restore();
}

function drawOverlay(
  ctx: CanvasRenderingContext2D,
  metadata: MetadataPayload | null,
) {
  if (!metadata) {
    return;
  }

  const queueZones = normalizeQueueZones(metadata.queue_zones);

  if (queueZones.length > 0) {
    queueZones.forEach((queueZone, index) => {
      drawZone(ctx, queueZone, index);
    });
  } else {
    const roi = metadata.roi ?? [];
    if (roi.length > 1) {
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(roi[0][0], roi[0][1]);
      for (let index = 1; index < roi.length; index += 1) {
        ctx.lineTo(roi[index][0], roi[index][1]);
      }
      ctx.closePath();
      ctx.lineWidth = 3;
      ctx.strokeStyle = '#f4efe6';
      ctx.setLineDash([10, 8]);
      ctx.stroke();
      ctx.restore();
    }
  }

  metadata.boxes?.forEach((box) => {
    const x1 = box.x1 ?? 0;
    const y1 = box.y1 ?? 0;
    const x2 = box.x2 ?? 0;
    const y2 = box.y2 ?? 0;
    const color = box.color ?? (box.status === 'queued' ? '#ff8c42' : '#1cc7b1');

    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2.5;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

    const labelParts = [];
    if (box.queue_zone_name) {
      labelParts.push(box.queue_zone_name);
    }
    labelParts.push(`ID ${box.id ?? '?'}`);
    if (typeof box.time === 'number') {
      labelParts.push(`${Math.round(box.time)}s`);
    }
    if (box.status) {
      labelParts.push(box.status.toUpperCase());
    }

    ctx.font = 'bold 13px Consolas, monospace';
    ctx.fillText(labelParts.join(' | '), x1, Math.max(18, y1 - 10));

    if (typeof box.cx === 'number' && typeof box.cy === 'number') {
      ctx.beginPath();
      ctx.arc(box.cx, box.cy, 4, 0, Math.PI * 2);
      ctx.fillStyle = '#f4efe6';
      ctx.fill();
    }

    ctx.restore();
  });
}

export function useEdgeTelemetry(
  canvasRef: RefObject<HTMLCanvasElement>,
) {
  const [backendUrl] = useState(resolveBackendUrl);
  const [connection, setConnection] = useState<'connecting' | 'live' | 'offline'>(
    'connecting',
  );
  const [lastFrameAt, setLastFrameAt] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<TelemetryMetrics>({
    density: 0,
    latency: 0,
    people: 0,
    queued: 0,
    waitSec: 0,
  });
  const [history, setHistory] = useState<HistorySeries>({
    density: createHistory(),
    latency: createHistory(),
    queue: createHistory(),
    wait: createHistory(),
  });
  const [network, setNetwork] = useState<NetworkState>({
    dropProb: 0,
    profile: 'Awaiting profile',
    realCamera: false,
  });
  const [overlay, setOverlay] = useState<OverlayState>({
    polygonPoints: 0,
    queueZones: 0,
    trackedObjects: 0,
  });
  const [queueZones, setQueueZones] = useState<QueueZoneTelemetry[]>([]);

  useEffect(() => {
    const image = new Image();
    const metadataRef: { current: MetadataPayload | null } = { current: null };
    let disposed = false;
    let drawing = false;

    image.onload = () => {
      const canvas = canvasRef.current;
      if (disposed || !canvas) {
        drawing = false;
        return;
      }

      const context = canvas.getContext('2d');
      if (!context) {
        drawing = false;
        return;
      }

      if (canvas.width !== image.width || canvas.height !== image.height) {
        canvas.width = image.width;
        canvas.height = image.height;
      }

      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      drawOverlay(context, metadataRef.current);
      drawing = false;
    };

    image.onerror = () => {
      drawing = false;
    };

    const socket = io(backendUrl, {
      timeout: 4000,
      transports: ['websocket'],
    });

    socket.on('connect', () => {
      setConnection('live');
    });

    socket.on('disconnect', () => {
      setConnection('offline');
    });

    socket.on('connect_error', () => {
      setConnection('offline');
    });

    socket.on('ai_metadata', (payload: MetadataPayload) => {
      metadataRef.current = payload;
      const normalizedQueueZones = normalizeQueueZones(payload.queue_zones);
      setQueueZones(normalizedQueueZones);
      setOverlay({
        polygonPoints: normalizedQueueZones.reduce(
          (total, queueZone) => total + queueZone.polygon.length,
          0,
        ),
        queueZones: normalizedQueueZones.length,
        trackedObjects: payload.boxes?.length ?? 0,
      });

      if (payload.network) {
        setNetwork((previous) => ({
          dropProb: payload.network?.drop_prob ?? previous.dropProb,
          profile: payload.network?.profile ?? previous.profile,
          realCamera: payload.network?.real_camera ?? previous.realCamera,
        }));
      }
    });

    socket.on('video_frame', (payload: VideoFramePayload) => {
      if (typeof payload.latency_ms === 'number') {
        const nextLatency = payload.latency_ms;
        setMetrics((previous) => ({
          ...previous,
          latency: nextLatency,
        }));
        setHistory((previous) => ({
          ...previous,
          latency: appendPoint(previous.latency, nextLatency),
        }));
      }

      if (typeof payload.real_camera === 'boolean') {
        setNetwork((previous) => ({
          ...previous,
          realCamera: payload.real_camera ?? previous.realCamera,
        }));
      }

      if (!payload.image || drawing || disposed) {
        return;
      }

      drawing = true;
      setLastFrameAt(Date.now());
      image.src = `data:image/jpeg;base64,${payload.image}`;
    });

    socket.on('telemetry_stream', async (payload: unknown) => {
      const buffer = await readPayloadBuffer(payload);
      if (!buffer || disposed) {
        return;
      }

      const view = new DataView(buffer);
      const people = view.getInt32(0, false);
      const queued = view.getInt32(4, false);
      const density = view.getFloat32(8, false);
      const waitSec = view.getFloat32(12, false);

      setMetrics((previous) => ({
        ...previous,
        density,
        people,
        queued,
        waitSec,
      }));

      setHistory((previous) => ({
        density: appendPoint(previous.density, density),
        latency: previous.latency,
        queue: appendPoint(previous.queue, queued),
        wait: appendPoint(previous.wait, waitSec),
      }));
    });

    return () => {
      disposed = true;
      socket.disconnect();
    };
  }, [backendUrl, canvasRef]);

  const connectionLabel =
    connection === 'live'
      ? 'Socket link active'
      : connection === 'connecting'
        ? 'Connecting to edge node'
        : 'Edge node unavailable';

  return {
    backendUrl,
    connection,
    connectionLabel,
    history,
    lastFrameAt,
    metrics,
    network,
    overlay,
    queueZones,
  };
}
