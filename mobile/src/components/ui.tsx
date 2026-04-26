/* UI primitives — direct ports from the Claude Design `ui.jsx`. */

import type { CSSProperties, ReactNode, MouseEventHandler } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { C } from '../theme';
import { Haptics, ImpactStyle } from '@capacitor/haptics';
import { Capacitor } from '@capacitor/core';

const isNative = Capacitor.isNativePlatform();
function tap() {
  if (isNative) Haptics.impact({ style: ImpactStyle.Light }).catch(() => undefined);
}

export function HUDStatusBar({ time = '9:41' }: { time?: string }) {
  return (
    <div
      style={{
        height: 54,
        padding: '14px 20px 0',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        color: '#0B1B2B',
        fontSize: 17,
        fontWeight: 600,
        fontVariantNumeric: 'tabular-nums',
        position: 'relative',
        zIndex: 5,
      }}
    >
      <span>{time}</span>
      <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
        <svg width="18" height="11" viewBox="0 0 18 11">
          <g fill="#0B1B2B">
            <rect x="0" y="7" width="3" height="4" rx="1" />
            <rect x="5" y="5" width="3" height="6" rx="1" />
            <rect x="10" y="2.5" width="3" height="8.5" rx="1" />
            <rect x="15" y="0" width="3" height="11" rx="1" />
          </g>
        </svg>
        <svg width="16" height="11" viewBox="0 0 16 11" fill="none">
          <path d="M8 10.5l1.5-1.5a2 2 0 00-3 0L8 10.5z" fill="#0B1B2B" />
          <path d="M3.5 6c2.4-2.5 6.6-2.5 9 0" stroke="#0B1B2B" strokeWidth="1.6" strokeLinecap="round" />
          <path d="M0.5 3c4-4 11-4 15 0" stroke="#0B1B2B" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <svg width="26" height="12" viewBox="0 0 26 12">
          <rect x="0.5" y="0.5" width="22" height="11" rx="3" fill="none" stroke="rgba(11,27,43,0.5)" />
          <rect x="2" y="2" width="19" height="8" rx="1.5" fill="#0B1B2B" />
          <rect x="23.5" y="3.5" width="2" height="5" rx="1" fill="rgba(11,27,43,0.5)" />
        </svg>
      </span>
    </div>
  );
}

interface AppHeaderProps {
  title: string;
  subtitle?: string;
  leading?: ReactNode;
  trailing?: ReactNode;
  big?: boolean;
}

export function AppHeader({ title, subtitle, leading, trailing, big = false }: AppHeaderProps) {
  return (
    <div
      style={{
        padding: big ? '8px 20px 18px' : '8px 16px 12px',
        display: 'flex',
        alignItems: big ? 'flex-end' : 'center',
        justifyContent: 'space-between',
        gap: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: big ? 'flex-end' : 'center', gap: 12, minWidth: 0 }}>
        {leading}
        <div style={{ minWidth: 0 }}>
          {subtitle && (
            <div
              style={{
                fontFamily: 'var(--f-mono)',
                fontSize: 9.5,
                letterSpacing: '0.22em',
                color: C.primary,
                textTransform: 'uppercase',
                marginBottom: 6,
                fontWeight: 600,
              }}
            >
              {subtitle}
            </div>
          )}
          <div
            style={{
              fontFamily: 'var(--f-display)',
              fontWeight: 800,
              fontSize: big ? 34 : 22,
              lineHeight: 1,
              letterSpacing: '-0.025em',
              color: C.ink0,
            }}
          >
            {title}
          </div>
        </div>
      </div>
      {trailing && <div style={{ display: 'flex', gap: 8 }}>{trailing}</div>}
    </div>
  );
}

interface IconBtnProps {
  children: ReactNode;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  active?: boolean;
  ariaLabel?: string;
}

export function IconBtn({ children, onClick, active = false, ariaLabel }: IconBtnProps) {
  return (
    <button
      onClick={(e) => {
        tap();
        onClick?.(e);
      }}
      aria-label={ariaLabel}
      style={{
        width: 38,
        height: 38,
        borderRadius: 12,
        background: active ? C.primarySoft : C.bg0,
        border: `1px solid ${active ? C.primary : C.line}`,
        color: active ? C.primary : C.ink1,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'pointer',
        padding: 0,
      }}
    >
      {children}
    </button>
  );
}

type PillColor = 'primary' | 'teal' | 'cyan' | 'magenta' | 'gold' | 'warn' | 'test' | 'odi' | 't20' | 'neutral';
const pillColors: Record<PillColor, string> = {
  primary: C.primary,
  teal: C.teal,
  cyan: C.cyan,
  magenta: C.magenta,
  gold: C.gold,
  warn: C.warn,
  test: C.test,
  odi: C.odi,
  t20: C.t20,
  neutral: C.ink2,
};

interface PillProps {
  children: ReactNode;
  color?: PillColor;
  size?: 'sm' | 'md';
  filled?: boolean;
}

export function Pill({ children, color = 'primary', size = 'md', filled = false }: PillProps) {
  const c = pillColors[color];
  const pad = size === 'sm' ? '3px 8px' : '5px 10px';
  const fs = size === 'sm' ? 9.5 : 10.5;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: pad,
        borderRadius: 999,
        fontFamily: 'var(--f-mono)',
        fontSize: fs,
        fontWeight: 600,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        background: filled ? c : 'transparent',
        color: filled ? '#fff' : c,
        border: filled ? 'none' : `1px solid ${c}55`,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

interface CardProps {
  children: ReactNode;
  style?: CSSProperties;
  glow?: boolean;
  padding?: number;
}

export function Card({ children, style = {}, glow = false, padding = 16 }: CardProps) {
  return (
    <div
      style={{
        background: C.bg1,
        border: `1px solid ${C.line}`,
        borderRadius: 'var(--r-md)',
        boxShadow: glow ? 'var(--shadow-glow)' : 'var(--shadow-card)',
        padding,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function SectionLabel({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 4px 8px',
        fontFamily: 'var(--f-mono)',
        fontSize: 9.5,
        letterSpacing: '0.24em',
        textTransform: 'uppercase',
        color: C.ink3,
        fontWeight: 600,
      }}
    >
      <span>{children}</span>
      {right}
    </div>
  );
}

interface StatProps {
  value: ReactNode;
  label: string;
  color?: string;
  sub?: string;
  align?: 'left' | 'center' | 'right';
}

export function Stat({ value, label, color, sub, align = 'left' }: StatProps) {
  return (
    <div style={{ textAlign: align }}>
      <div
        style={{
          fontFamily: 'var(--f-display)',
          fontWeight: 800,
          color: color || C.ink0,
          fontSize: 34,
          lineHeight: 1,
          fontVariantNumeric: 'tabular-nums',
          letterSpacing: '-0.03em',
        }}
      >
        {value}
      </div>
      <div
        style={{
          fontFamily: 'var(--f-mono)',
          fontSize: 9.5,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: C.ink3,
          marginTop: 6,
          fontWeight: 600,
        }}
      >
        {label}
      </div>
      {sub && <div style={{ fontSize: 11, color: C.ink2, marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

interface TabBarItem<T extends string = string> {
  id: T;
  label: string;
}

interface TabBarProps<T extends string = string> {
  items: TabBarItem<T>[];
  active: T;
  onChange?: (id: T) => void;
}

export function TabBar<T extends string = string>({ items, active, onChange }: TabBarProps<T>) {
  return (
    <div
      style={{
        display: 'inline-flex',
        padding: 4,
        gap: 2,
        borderRadius: 999,
        background: C.bg2,
        border: `1px solid ${C.line}`,
      }}
    >
      {items.map((i) => (
        <button
          key={i.id}
          onClick={() => {
            tap();
            onChange?.(i.id);
          }}
          style={{
            padding: '7px 14px',
            borderRadius: 999,
            border: 'none',
            cursor: 'pointer',
            background: i.id === active ? C.ink0 : 'transparent',
            color: i.id === active ? '#fff' : C.ink2,
            fontFamily: 'var(--f-mono)',
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
          }}
        >
          {i.label}
        </button>
      ))}
    </div>
  );
}

const navItems = [
  {
    id: 'chat',
    path: '/',
    label: 'Ask',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
        <path d="M4 5h16v12H8l-4 4V5z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    id: 'history',
    path: '/history',
    label: 'History',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.7" />
        <path d="M12 7v5l3 2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    id: 'data',
    path: '/data',
    label: 'Data',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
        <rect x="3" y="13" width="4" height="7" stroke="currentColor" strokeWidth="1.7" />
        <rect x="10" y="8" width="4" height="12" stroke="currentColor" strokeWidth="1.7" />
        <rect x="17" y="4" width="4" height="16" stroke="currentColor" strokeWidth="1.7" />
      </svg>
    ),
  },
  {
    id: 'know',
    path: '/knowledge',
    label: 'Facts',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
        <path d="M5 4h11l3 3v13H5V4z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M8 11h8M8 15h5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    ),
  },
] as const;

export function BottomNav() {
  const navigate = useNavigate();
  const location = useLocation();
  const path = location.pathname;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        paddingBottom: `calc(20px + var(--safe-bottom))`,
        paddingTop: 10,
        background: 'linear-gradient(to top, #FFFFFF 60%, rgba(255,255,255,0))',
        borderTop: `1px solid ${C.line}`,
        display: 'flex',
        justifyContent: 'space-around',
        zIndex: 40,
      }}
    >
      {navItems.map((i) => {
        const on =
          i.path === '/'
            ? path === '/' || path.startsWith('/chat')
            : path.startsWith(i.path);
        return (
          <button
            key={i.id}
            onClick={() => {
              tap();
              navigate(i.path);
            }}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 4,
              color: on ? C.primary : C.ink3,
              padding: '4px 12px',
            }}
          >
            {i.icon}
            <span
              style={{
                fontFamily: 'var(--f-mono)',
                fontSize: 9,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                fontWeight: 700,
              }}
            >
              {i.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function ScreenShell({
  children,
  showNav = true,
  scroll = true,
  padBottom = 110,
}: {
  children: ReactNode;
  showNav?: boolean;
  scroll?: boolean;
  padBottom?: number;
}) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        background: '#FFFFFF',
        position: 'relative',
        color: C.ink0,
        fontFamily: 'var(--f-body)',
      }}
    >
      <div
        className={scroll ? 'scroll-area no-scrollbar' : ''}
        style={{
          position: 'absolute',
          inset: 0,
          paddingTop: `calc(8px + var(--safe-top))`,
          paddingBottom: showNav ? padBottom : 'calc(20px + var(--safe-bottom))',
          overflow: scroll ? 'auto' : 'hidden',
        }}
      >
        {children}
      </div>
      {showNav && <BottomNav />}
    </div>
  );
}

export function Dot({ delay = 0 }: { delay?: number }) {
  return (
    <span
      style={{
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: C.primary,
        display: 'inline-block',
        animation: `dot 1.2s ${delay}s infinite ease-in-out`,
      }}
    />
  );
}

export function Em({ children }: { children: ReactNode }) {
  return <span style={{ color: C.primary, fontWeight: 600 }}>{children}</span>;
}

export function UserBubble({ children }: { children: ReactNode }) {
  return (
    <div
      className="selectable"
      style={{
        alignSelf: 'flex-end',
        maxWidth: '85%',
        background: '#DCEBFF',
        border: '1px solid #B7D4F5',
        padding: '10px 14px',
        borderRadius: '18px 18px 4px 18px',
        fontSize: 14.5,
        lineHeight: 1.4,
        color: '#0B1B2B',
      }}
    >
      {children}
    </div>
  );
}

export function AssistantBubble({ children }: { children: ReactNode }) {
  return (
    <div
      className="selectable"
      style={{
        alignSelf: 'flex-start',
        maxWidth: '92%',
        background: '#FFFFFF',
        border: '1px solid #E2E7EE',
        boxShadow: 'var(--shadow-card)',
        padding: '10px 14px',
        borderRadius: '18px 18px 18px 4px',
        fontSize: 14.5,
        lineHeight: 1.4,
        color: '#0B1B2B',
      }}
    >
      {children}
    </div>
  );
}
