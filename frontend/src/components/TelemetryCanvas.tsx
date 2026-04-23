type TelemetryCanvasProps = {
  canvasRef: React.RefObject<HTMLCanvasElement>;
  connection: 'connecting' | 'live' | 'offline';
};

export function TelemetryCanvas({
  canvasRef,
  connection,
}: TelemetryCanvasProps) {
  return (
    <div className="telemetry-stage">
      <div className="telemetry-stage-frame">
        <canvas ref={canvasRef} className="telemetry-canvas" />

        {connection !== 'live' ? (
          <div className="telemetry-empty-state">
            <span className="telemetry-empty-kicker">
              {connection === 'connecting' ? 'Connecting to node' : 'Feed offline'}
            </span>
            <strong>
              {connection === 'connecting'
                ? 'Waiting for the first camera frame'
                : 'Start the backend and the edge preview will appear here'}
            </strong>
            <p>
              The preview and queue overlay will appear automatically as soon as
              the backend starts streaming live telemetry.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
