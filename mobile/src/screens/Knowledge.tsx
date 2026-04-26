import { ScreenShell, AppHeader, Card, Pill, SectionLabel } from '../components/ui';
import { C } from '../theme';

const FACTS: Array<{ q: string; a: string; tag: 'Test' | 'ODI' | 'T20I' | 'IPL' }> = [
  {
    q: 'Highest individual Test score',
    a: '400* — Brian Lara, vs England, Antigua, 2004.',
    tag: 'Test',
  },
  {
    q: 'Most ODI runs',
    a: 'Sachin Tendulkar — 18,426 runs in 463 ODIs across 22 years.',
    tag: 'ODI',
  },
  {
    q: 'Fastest T20I century',
    a: 'Sahil Chauhan — 27 balls (Estonia vs Cyprus, 2024).',
    tag: 'T20I',
  },
  {
    q: 'Most IPL sixes',
    a: 'Chris Gayle — 357 sixes from 142 matches (2008–2021).',
    tag: 'IPL',
  },
  {
    q: 'Best bowling figures (Test)',
    a: 'Jim Laker — 10/53 vs Australia, Manchester, 1956.',
    tag: 'Test',
  },
];

export function ScreenKnowledge() {
  return (
    <ScreenShell>
      <AppHeader title="Facts" subtitle="Quick-fire cricket records" big />

      <div style={{ padding: '0 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <SectionLabel>Did you know?</SectionLabel>
        {FACTS.map((f, i) => (
          <Card key={i}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <Pill
                color={f.tag === 'Test' ? 'test' : f.tag === 'ODI' ? 'odi' : f.tag === 'T20I' ? 't20' : 'magenta'}
                size="sm"
              >
                {f.tag}
              </Pill>
              <span
                style={{
                  fontFamily: 'var(--f-mono)',
                  fontSize: 9.5,
                  color: C.ink3,
                  letterSpacing: '0.18em',
                  textTransform: 'uppercase',
                  fontWeight: 600,
                }}
              >
                Fact #{i + 1}
              </span>
            </div>
            <div
              style={{
                fontFamily: 'var(--f-display)',
                fontSize: 16,
                fontWeight: 700,
                color: C.ink0,
                marginBottom: 6,
                letterSpacing: '-0.01em',
              }}
            >
              {f.q}
            </div>
            <div style={{ fontSize: 13.5, color: C.ink2, lineHeight: 1.5 }}>{f.a}</div>
          </Card>
        ))}
      </div>
    </ScreenShell>
  );
}
