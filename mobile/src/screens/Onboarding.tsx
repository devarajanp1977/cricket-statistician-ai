import { useNavigate } from 'react-router-dom';
import { Logo } from '../components/Logo';
import { C } from '../theme';
import { useAuth } from '../lib/AuthContext';
import { useState } from 'react';

export function ScreenOnboarding() {
  const navigate = useNavigate();
  const { signInWithGoogle, signInWithEmail, signUpWithEmail, user } = useAuth();
  const [showEmail, setShowEmail] = useState(false);
  const [mode, setMode] = useState<'in' | 'up'>('in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (user) {
    // Already signed in — bounce to chat.
    setTimeout(() => navigate('/', { replace: true }), 0);
  }

  async function google() {
    setErr(null);
    setBusy(true);
    try {
      await signInWithGoogle();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function emailGo() {
    setErr(null);
    setBusy(true);
    try {
      if (mode === 'in') await signInWithEmail(email, password);
      else await signUpWithEmail(email, password);
      navigate('/', { replace: true });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
        background: '#FFFFFF',
      }}
    >
      <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0, opacity: 0.5 }}>
        <defs>
          <pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <path d="M28 0H0V28" fill="none" stroke="#EEF1F5" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>
      <div
        style={{
          position: 'absolute',
          top: `calc(60px + var(--safe-top))`,
          left: 20,
          right: 20,
          height: 1,
          background: 'linear-gradient(to right, transparent, #E2E7EE, transparent)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          inset: 0,
          paddingTop: `calc(70px + var(--safe-top))`,
          paddingBottom: `calc(40px + var(--safe-bottom))`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'space-between',
          textAlign: 'center',
          padding: `calc(70px + var(--safe-top)) 32px calc(40px + var(--safe-bottom))`,
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 26 }}>
          <Logo size={130} />
          <div>
            <div
              style={{
                fontFamily: 'var(--f-mono)',
                fontSize: 10,
                letterSpacing: '0.32em',
                color: C.primary,
                textTransform: 'uppercase',
                marginBottom: 16,
                fontWeight: 700,
              }}
            >
              SINCE 1877 · 21,576 MATCHES
            </div>
            <h1
              style={{
                fontFamily: 'var(--f-display)',
                fontWeight: 800,
                fontSize: 50,
                lineHeight: 0.96,
                margin: 0,
                letterSpacing: '-0.03em',
                color: C.ink0,
              }}
            >
              Every ball.
              <br />
              Every record.
              <br />
              <span style={{ color: C.primary }}>One innings.</span>
            </h1>
            <p
              style={{
                fontSize: 15,
                color: C.ink2,
                marginTop: 18,
                lineHeight: 1.5,
                maxWidth: 280,
                marginInline: 'auto',
              }}
            >
              The cricket statistician in your pocket. Ask anything from Grace to Gambhir, answered
              from <strong style={{ color: C.ink0 }}>11.05M deliveries</strong>.
            </p>
          </div>
        </div>

        {!showEmail ? (
          <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <button
              onClick={google}
              disabled={busy}
              style={{
                height: 54,
                borderRadius: 16,
                border: 'none',
                cursor: 'pointer',
                background: C.ink0,
                color: '#fff',
                fontFamily: 'var(--f-display)',
                fontSize: 17,
                fontWeight: 800,
                letterSpacing: '-0.005em',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 10,
                opacity: busy ? 0.6 : 1,
              }}
            >
              <svg width="18" height="18" viewBox="0 0 48 48">
                <path
                  fill="#fff"
                  d="M44.5 20H24v8.5h11.8C34.7 33.9 30 37 24 37c-7.2 0-13-5.8-13-13s5.8-13 13-13c3.1 0 5.9 1.1 8.1 2.9l6.4-6.4C34.6 4.1 29.6 2 24 2 11.8 2 2 11.8 2 24s9.8 22 22 22c11 0 21-8 21-22 0-1.3-.2-2.7-.5-4z"
                />
              </svg>
              {busy ? 'Connecting…' : 'Continue with Google'}
            </button>
            <button
              onClick={() => setShowEmail(true)}
              style={{
                height: 48,
                borderRadius: 16,
                cursor: 'pointer',
                background: 'transparent',
                color: C.ink1,
                border: `1px solid ${C.line}`,
                fontFamily: 'var(--f-mono)',
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
              }}
            >
              Continue with email
            </button>
            <button
              onClick={() => navigate('/', { replace: true })}
              style={{
                height: 36,
                marginTop: 4,
                cursor: 'pointer',
                background: 'transparent',
                color: C.ink3,
                border: 'none',
                fontFamily: 'var(--f-mono)',
                fontSize: 10.5,
                letterSpacing: '0.16em',
                textTransform: 'uppercase',
              }}
            >
              Skip · explore as guest
            </button>
            {err && (
              <div style={{ fontSize: 12, color: C.magenta, marginTop: 8 }}>{err}</div>
            )}
          </div>
        ) : (
          <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <input
              type="email"
              autoComplete="email"
              placeholder="email@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={{
                height: 48,
                padding: '0 16px',
                borderRadius: 14,
                border: `1px solid ${C.line}`,
                background: '#fff',
                fontSize: 15,
                fontFamily: 'var(--f-body)',
                color: C.ink0,
              }}
            />
            <input
              type="password"
              autoComplete={mode === 'up' ? 'new-password' : 'current-password'}
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{
                height: 48,
                padding: '0 16px',
                borderRadius: 14,
                border: `1px solid ${C.line}`,
                background: '#fff',
                fontSize: 15,
                fontFamily: 'var(--f-body)',
                color: C.ink0,
              }}
            />
            <button
              onClick={emailGo}
              disabled={busy || !email || !password}
              style={{
                height: 50,
                borderRadius: 14,
                border: 'none',
                cursor: 'pointer',
                background: C.primary,
                color: '#fff',
                fontFamily: 'var(--f-display)',
                fontSize: 15,
                fontWeight: 800,
                opacity: busy || !email || !password ? 0.5 : 1,
              }}
            >
              {busy ? '…' : mode === 'in' ? 'Sign in' : 'Create account'}
            </button>
            <button
              onClick={() => setMode(mode === 'in' ? 'up' : 'in')}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: C.ink3,
                fontFamily: 'var(--f-mono)',
                fontSize: 10.5,
                letterSpacing: '0.16em',
                textTransform: 'uppercase',
                marginTop: 4,
              }}
            >
              {mode === 'in' ? 'New here? Create account' : 'Have account? Sign in'}
            </button>
            <button
              onClick={() => setShowEmail(false)}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: C.ink3,
                fontFamily: 'var(--f-mono)',
                fontSize: 10.5,
                letterSpacing: '0.16em',
                textTransform: 'uppercase',
              }}
            >
              ‹ Back
            </button>
            {err && <div style={{ fontSize: 12, color: C.magenta }}>{err}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
