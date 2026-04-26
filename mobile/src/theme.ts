/* Centralised palette so screens & ui share the exact same tokens. */

export const C = {
  bg0: 'var(--bg-0)', bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  line: 'var(--line)', lineSoft: 'var(--line-soft)',
  ink0: 'var(--ink-0)', ink1: 'var(--ink-1)', ink2: 'var(--ink-2)',
  ink3: 'var(--ink-3)', ink4: 'var(--ink-4)',
  primary: 'var(--primary)', primaryDeep: 'var(--primary-deep)',
  primarySoft: 'var(--primary-soft)',
  teal: 'var(--teal)',
  cyan: 'var(--cyan)', magenta: 'var(--magenta)', gold: 'var(--gold)', warn: 'var(--warn)',
  test: 'var(--fmt-test)', odi: 'var(--fmt-odi)', t20: 'var(--fmt-t20)',
} as const;

export type ColorKey = keyof typeof C;
