import { useEffect, useState } from 'react';
import { ScreenShell, AppHeader, Card, Pill, SectionLabel, Stat } from '../components/ui';
import { C } from '../theme';
import { api } from '../lib/api';
import { useAuth } from '../lib/AuthContext';

type StatsShape = {
  matches?: number;
  deliveries?: number;
  span?: { from: number; to: number };
  formats?: Record<string, number>;
};

export function ScreenData() {
  const { signOut, user } = useAuth();
  const [stats, setStats] = useState<StatsShape | null>(null);

  useEffect(() => {
    api
      .stats()
      .then((s) => setStats(s as StatsShape))
      .catch(() => undefined);
  }, []);

  return (
    <ScreenShell>
      <AppHeader title="Data" subtitle="Cricket since 1877" big />

      <div style={{ padding: '0 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Card glow>
          <SectionLabel right={<Pill color="teal" size="sm">live</Pill>}>Engine status</SectionLabel>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Stat
              value={stats?.matches ? fmt(stats.matches) : '—'}
              label="Matches"
              color={C.primary}
            />
            <Stat
              value={stats?.deliveries ? fmt(stats.deliveries) : '—'}
              label="Deliveries"
              color={C.teal}
            />
          </div>
          <div style={{ marginTop: 14, fontSize: 12.5, color: C.ink2, lineHeight: 1.5 }}>
            Powered by <strong style={{ color: C.ink0 }}>DuckDB</strong> on Oracle Cloud ARM.
            Refreshed weekly from Cricsheet.
          </div>
        </Card>

        <SectionLabel>Coverage</SectionLabel>
        <Card>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              { label: 'Test', color: 'test' as const, since: 1877 },
              { label: 'ODI', color: 'odi' as const, since: 1971 },
              { label: 'T20I', color: 't20' as const, since: 2005 },
              { label: 'IPL', color: 'magenta' as const, since: 2008 },
              { label: 'County / Domestic', color: 'gold' as const, since: 1890 },
            ].map((row) => (
              <div
                key={row.label}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  paddingBottom: 10,
                  borderBottom: `1px solid ${C.lineSoft}`,
                }}
              >
                <Pill color={row.color} size="sm">
                  {row.label}
                </Pill>
                <span
                  style={{
                    fontFamily: 'var(--f-mono)',
                    fontSize: 11,
                    color: C.ink2,
                    letterSpacing: '0.1em',
                  }}
                >
                  Since {row.since}
                </span>
                <span style={{ marginLeft: 'auto' }}>
                  <Pill color="teal" size="sm" filled>
                    ✓
                  </Pill>
                </span>
              </div>
            ))}
          </div>
        </Card>

        <SectionLabel>Account</SectionLabel>
        <Card>
          {user ? (
            <>
              <div style={{ fontSize: 13.5, color: C.ink0, marginBottom: 4 }}>{user.email}</div>
              <div
                style={{
                  fontSize: 11,
                  color: C.ink3,
                  fontFamily: 'var(--f-mono)',
                  letterSpacing: '0.1em',
                }}
              >
                ID · {user.id.slice(0, 8)}…
              </div>
              <button
                onClick={signOut}
                style={{
                  marginTop: 14,
                  height: 38,
                  borderRadius: 12,
                  border: `1px solid ${C.line}`,
                  background: '#fff',
                  color: C.magenta,
                  fontFamily: 'var(--f-mono)',
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: '0.14em',
                  textTransform: 'uppercase',
                  cursor: 'pointer',
                  paddingInline: 14,
                }}
              >
                Sign out
              </button>
            </>
          ) : (
            <div style={{ fontSize: 13, color: C.ink2 }}>Not signed in. Guest mode.</div>
          )}
        </Card>
      </div>
    </ScreenShell>
  );
}

function fmt(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}
