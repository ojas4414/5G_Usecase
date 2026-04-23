import { useId } from 'react';

type SparklineProps = {
  tone: 'teal' | 'amber' | 'coral' | 'slate';
  values: number[];
};

const toneMap = {
  amber: {
    fill: 'rgba(204, 106, 47, 0.18)',
    stroke: '#cc6a2f',
  },
  coral: {
    fill: 'rgba(207, 74, 74, 0.18)',
    stroke: '#cf4a4a',
  },
  slate: {
    fill: 'rgba(15, 29, 34, 0.16)',
    stroke: '#1f3842',
  },
  teal: {
    fill: 'rgba(17, 143, 128, 0.18)',
    stroke: '#118f80',
  },
} as const;

function buildSparkline(values: number[], width: number, height: number) {
  if (!values.length) {
    return {
      area: `0,${height} ${width},${height}`,
      line: `0,${height / 2} ${width},${height / 2}`,
    };
  }

  const maxValue = Math.max(...values);
  const minValue = Math.min(...values);
  const range = maxValue - minValue || 1;

  const points = values.map((value, index) => {
    const x = (index / Math.max(values.length - 1, 1)) * width;
    const y = height - ((value - minValue) / range) * (height - 8) - 4;
    return `${x},${y}`;
  });

  return {
    area: `0,${height} ${points.join(' ')} ${width},${height}`,
    line: points.join(' '),
  };
}

export function Sparkline({ tone, values }: SparklineProps) {
  const gradientId = useId();
  const { area, line } = buildSparkline(values, 240, 88);
  const palette = toneMap[tone];

  return (
    <svg
      aria-hidden="true"
      className="sparkline"
      viewBox="0 0 240 88"
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id={gradientId} x1="0%" x2="0%" y1="0%" y2="100%">
          <stop offset="0%" stopColor={palette.fill} />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </linearGradient>
      </defs>
      <path
        d={`M ${area}`}
        fill={`url(#${gradientId})`}
        stroke="none"
      />
      <polyline
        fill="none"
        points={line}
        stroke={palette.stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="3"
      />
    </svg>
  );
}
