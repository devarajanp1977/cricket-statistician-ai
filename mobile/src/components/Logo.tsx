/* Logo — Light theme. Hex shield, white fill, ink stroke. */

interface LogoProps {
  size?: number;
  label?: boolean;
}

export function Logo({ size = 96, label = false }: LogoProps) {
  const ink = '#0B1B2B';
  const blue = '#1A4FA0';
  const blueDeep = '#103573';
  const paper = '#FFFFFF';
  const stroke = '#0B1B2B';

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: size * 0.18 }}>
      <svg width={size} height={size} viewBox="0 0 120 120" style={{ display: 'block' }}>
        <path
          d="M60 6 L108 32 L108 88 L60 114 L12 88 L12 32 Z"
          fill={paper}
          stroke={stroke}
          strokeWidth="2.4"
        />
        <path
          d="M60 12 L102 35 L102 85 L60 108 L18 85 L18 35 Z"
          fill="none"
          stroke={stroke}
          strokeWidth="0.8"
          opacity="0.35"
        />

        <g transform="translate(38 70)">
          <circle cx="0" cy="0" r="11" fill={ink} />
          <path d="M-9 -2 Q0 -5 9 -2" stroke={blue} strokeWidth="1.4" fill="none" />
          <path d="M-9 2 Q0 5 9 2" stroke={blue} strokeWidth="1.1" fill="none" opacity="0.65" />
          {Array.from({ length: 6 }).map((_, i) => {
            const x = -7 + i * 2.8;
            return (
              <line
                key={i}
                x1={x}
                y1={-3.5}
                x2={x}
                y2={-0.5}
                stroke={paper}
                strokeWidth="0.7"
                strokeLinecap="round"
              />
            );
          })}
        </g>

        <g transform="rotate(35 60 60)">
          <rect x="55" y="20" width="14" height="42" rx="2" fill={ink} />
          <line x1="62" y1="22" x2="62" y2="60" stroke="#243447" strokeWidth="0.8" />
          <path d="M55 24 Q58 18 62 18 Q66 18 69 24" fill={ink} />
          <rect x="59" y="6" width="6" height="16" rx="2" fill={blueDeep} />
          {[8, 11, 14, 17].map((y, i) => (
            <line
              key={i}
              x1="59"
              y1={y}
              x2="65"
              y2={y}
              stroke={paper}
              strokeWidth="0.7"
              opacity="0.7"
            />
          ))}
        </g>

        <polyline
          points="76,80 84,76 92,79 100,72"
          fill="none"
          stroke={blue}
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <circle cx="100" cy="72" r="2" fill={blue} />
      </svg>

      {label && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            lineHeight: 1,
            fontFamily: 'var(--f-display)',
          }}
        >
          <span
            style={{
              fontSize: size * 0.3,
              fontWeight: 700,
              letterSpacing: '-0.01em',
              color: 'var(--ink-0)',
            }}
          >
            Cricket
          </span>
          <span
            style={{
              fontSize: size * 0.3,
              fontWeight: 800,
              letterSpacing: '-0.01em',
              color: 'var(--primary)',
              marginTop: size * 0.04,
            }}
          >
            Statistician
          </span>
        </div>
      )}
    </div>
  );
}
