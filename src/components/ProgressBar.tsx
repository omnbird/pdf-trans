interface Props {
  progress: {
    stage: string;
    current: number;
    total: number;
    message: string;
  };
}

export default function ProgressBar({ progress }: Props) {
  const pct = Math.round((progress.current / progress.total) * 100);

  return (
    <div
      style={{
        background: "#1a1a1a",
        border: "1px solid #333",
        borderRadius: 8,
        padding: 20,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginBottom: 8,
          fontSize: 13,
          color: "#aaa",
        }}
      >
        <span>{progress.stage}</span>
        <span>{pct}%</span>
      </div>
      <div
        style={{
          width: "100%",
          height: 6,
          background: "#222",
          borderRadius: 3,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: "#3b82f6",
            borderRadius: 3,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: "#666" }}>
        {progress.message}
      </div>
    </div>
  );
}
