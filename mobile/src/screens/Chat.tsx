import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Logo } from '../components/Logo';
import { ScreenShell, AssistantBubble, UserBubble, Card, Pill, Dot, Em } from '../components/ui';
import { C } from '../theme';
import { api, type AskResponse, type AskHistoryTurn } from '../lib/api';
import { useAuth } from '../lib/AuthContext';

interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  data?: AskResponse | null;
  pending?: boolean;
  error?: string;
}

const QUICK_PROMPTS = ['Last over', 'Compare', 'Career stat', 'Head to head'];

export function ScreenChat() {
  const navigate = useNavigate();
  const { sessionId: routeSession } = useParams();
  const { user } = useAuth();
  const [sessionId] = useState<string>(() => routeSession || crypto.randomUUID());
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new turns
  useEffect(() => {
    const el = scrollRef.current?.parentElement;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, thinking]);

  async function send(q?: string) {
    const text = (q ?? input).trim();
    if (!text || thinking) return;
    setInput('');

    const userTurn: ChatTurn = { id: crypto.randomUUID(), role: 'user', content: text };
    setTurns((t) => [...t, userTurn]);
    setThinking(true);

    const history: AskHistoryTurn[] = turns
      .filter((t) => t.role === 'user' && t.data)
      .slice(-5)
      .map((t) => ({
        question: t.content,
        sql: t.data?.sql ?? '',
        context_summary: t.data?.context_summary ?? '',
      }));

    try {
      const res = await api.ask({ question: text, history });
      setTurns((t) => [
        ...t,
        { id: crypto.randomUUID(), role: 'assistant', content: res.answer, data: res },
      ]);

      if (user) {
        api
          .saveChatTurn({ session_id: sessionId, role: 'user', content: text })
          .catch(() => undefined);
        api
          .saveChatTurn({
            session_id: sessionId,
            role: 'assistant',
            content: res.answer,
            metadata: { sql: res.sql, model: res.model_used, columns: res.columns, rows: res.rows },
          })
          .catch(() => undefined);
      }
    } catch (e) {
      setTurns((t) => [
        ...t,
        {
          id: crypto.randomUUID(),
          role: 'assistant',
          content: '',
          error: (e as Error).message,
        },
      ]);
    } finally {
      setThinking(false);
    }
  }

  return (
    <ScreenShell padBottom={150}>
      {/* sticky chat header */}
      <div
        style={{
          padding: '4px 16px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          borderBottom: `1px solid ${C.lineSoft}`,
        }}
      >
        <button
          onClick={() => navigate('/history')}
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background: C.bg2,
            border: `1px solid ${C.line}`,
            color: C.ink1,
            padding: 0,
            cursor: 'pointer',
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" style={{ display: 'block', margin: 'auto' }}>
            <path d="M15 5l-7 7 7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: 'var(--f-mono)',
              fontSize: 9.5,
              color: C.ink3,
              letterSpacing: '0.18em',
              textTransform: 'uppercase',
              marginBottom: 2,
            }}
          >
            <span style={{ color: C.primary }}>● Live</span> · GPT-4.1 + DuckDB
          </div>
          <div
            style={{
              fontFamily: 'var(--f-display)',
              fontSize: 15,
              fontWeight: 700,
              color: C.ink0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              letterSpacing: '-0.01em',
            }}
          >
            {turns.length === 0 ? 'New conversation' : turns[0].content.slice(0, 60)}
          </div>
        </div>
        <Pill color="primary" size="sm">
          {turns.length} {turns.length === 1 ? 'turn' : 'turns'}
        </Pill>
      </div>

      <div
        ref={scrollRef}
        style={{ padding: '14px 16px 0', display: 'flex', flexDirection: 'column', gap: 14 }}
      >
        {turns.length === 0 && <EmptyChatHint onSelect={(q) => send(q)} />}

        {turns.map((t) =>
          t.role === 'user' ? (
            <UserBubble key={t.id}>{t.content}</UserBubble>
          ) : t.error ? (
            <AssistantBubble key={t.id}>
              <span style={{ color: C.magenta }}>⚠ {t.error}</span>
            </AssistantBubble>
          ) : (
            <AnswerBlock key={t.id} data={t.data!} />
          )
        )}

        {thinking && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              background: '#FFFFFF',
              border: `1px solid ${C.line}`,
              borderRadius: '18px 18px 18px 4px',
              padding: '12px 14px',
              alignSelf: 'flex-start',
              maxWidth: '92%',
              boxShadow: 'var(--shadow-card)',
            }}
          >
            <span style={{ display: 'inline-flex', gap: 4 }}>
              <Dot />
              <Dot delay={0.15} />
              <Dot delay={0.3} />
            </span>
            <span
              style={{
                fontFamily: 'var(--f-mono)',
                fontSize: 11,
                color: C.ink2,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
              }}
            >
              Querying deliveries · 11M rows
            </span>
          </div>
        )}

        <div style={{ height: 8 }} />
      </div>

      <div
        style={{
          position: 'absolute',
          bottom: `calc(78px + var(--safe-bottom))`,
          left: 0,
          right: 0,
          padding: '10px 16px 6px',
          background: 'linear-gradient(to top, #FFFFFF 70%, rgba(255,255,255,0))',
        }}
      >
        <div
          style={{
            display: 'flex',
            gap: 8,
            marginBottom: 10,
            overflowX: 'auto',
          }}
          className="no-scrollbar"
        >
          {QUICK_PROMPTS.map((s) => (
            <button
              key={s}
              onClick={() => setInput((v) => (v ? v + ' ' + s : s))}
              style={{
                padding: '6px 11px',
                borderRadius: 999,
                background: C.bg2,
                border: `1px solid ${C.line}`,
                fontFamily: 'var(--f-mono)',
                fontSize: 10.5,
                color: C.ink1,
                letterSpacing: '0.08em',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                cursor: 'pointer',
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: '#FFF',
            border: `1px solid ${C.line}`,
            borderRadius: 24,
            padding: '4px 4px 4px 16px',
            height: 48,
            boxShadow: 'var(--shadow-card)',
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a cricket question…"
            style={{
              flex: 1,
              border: 'none',
              outline: 'none',
              background: 'transparent',
              fontSize: 14,
              color: C.ink0,
              fontFamily: 'var(--f-body)',
            }}
            enterKeyHint="send"
          />
          <button
            type="submit"
            disabled={!input.trim() || thinking}
            style={{
              width: 40,
              height: 40,
              borderRadius: 999,
              border: 'none',
              cursor: 'pointer',
              background: C.primary,
              color: '#fff',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              opacity: !input.trim() || thinking ? 0.4 : 1,
            }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path
                d="M5 12h14M13 6l6 6-6 6"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </form>
      </div>
    </ScreenShell>
  );
}

function EmptyChatHint({ onSelect }: { onSelect: (q: string) => void }) {
  const samples = [
    'How many centuries has KL Rahul hit in the IPL?',
    "What has been Nicholas Pooran's average in T20s since 2024?",
    'Who has the most centuries in Test cricket?',
    'Compare Virat Kohli and Joe Root in Test averages.',
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 18, padding: '28px 8px' }}>
      <Logo size={84} label />
      <div
        style={{
          fontFamily: 'var(--f-mono)',
          fontSize: 10,
          letterSpacing: '0.22em',
          color: C.ink3,
          textTransform: 'uppercase',
        }}
      >
        Ask anything · since 1877
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
        {samples.map((s) => (
          <button
            key={s}
            onClick={() => onSelect(s)}
            style={{
              textAlign: 'left',
              padding: '12px 14px',
              borderRadius: 14,
              background: '#fff',
              border: `1px solid ${C.line}`,
              cursor: 'pointer',
              fontSize: 13.5,
              color: C.ink0,
              fontFamily: 'var(--f-body)',
              boxShadow: 'var(--shadow-card)',
            }}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function AnswerBlock({ data }: { data: AskResponse }) {
  const { answer, sql, columns, rows } = data;
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        alignSelf: 'flex-start',
        width: '100%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Logo size={22} />
        <span
          style={{
            fontFamily: 'var(--f-mono)',
            fontSize: 9.5,
            color: C.primary,
            letterSpacing: '0.2em',
            textTransform: 'uppercase',
            fontWeight: 700,
          }}
        >
          Cricket Statistician
        </span>
        {data.cached && (
          <span style={{ marginLeft: 'auto' }}>
            <Pill color="cyan" size="sm">
              cached
            </Pill>
          </span>
        )}
      </div>

      <div
        className="selectable"
        style={{
          background: C.bg2,
          border: `1px solid ${C.line}`,
          borderRadius: 14,
          padding: '14px 16px',
          fontSize: 14,
          lineHeight: 1.55,
          color: C.ink1,
          whiteSpace: 'pre-wrap',
        }}
      >
        {renderInlineEmphasis(answer)}
      </div>

      {rows.length > 0 && columns.length > 0 && (
        <Card padding={0} style={{ overflow: 'hidden' }}>
          <div
            style={{
              padding: '12px 14px',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              borderBottom: `1px solid ${C.line}`,
              background: C.bg2,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="3" width="18" height="18" rx="2" stroke={C.primary} strokeWidth="1.7" />
              <path d="M3 9h18M9 3v18" stroke={C.primary} strokeWidth="1.7" />
            </svg>
            <span
              style={{
                fontFamily: 'var(--f-mono)',
                fontSize: 10,
                color: C.ink0,
                letterSpacing: '0.18em',
                textTransform: 'uppercase',
                fontWeight: 700,
              }}
            >
              {rows.length} {rows.length === 1 ? 'row' : 'rows'}
            </span>
            {sql && (
              <span style={{ marginLeft: 'auto' }}>
                <Pill color="cyan" size="sm">
                  SQL
                </Pill>
              </span>
            )}
          </div>
          <div style={{ overflowX: 'auto' }} className="no-scrollbar">
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                fontVariantNumeric: 'tabular-nums',
                fontSize: 12,
              }}
            >
              <thead>
                <tr style={{ background: '#FFF' }}>
                  {columns.map((h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: 'left',
                        padding: '8px 10px',
                        color: C.ink3,
                        fontFamily: 'var(--f-mono)',
                        fontSize: 9.5,
                        fontWeight: 600,
                        letterSpacing: '0.14em',
                        textTransform: 'uppercase',
                        borderBottom: `1px solid ${C.line}`,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 25).map((r, i) => (
                  <tr key={i} style={{ borderBottom: i < rows.length - 1 ? `1px solid ${C.lineSoft}` : 'none' }}>
                    {r.map((cell, j) => (
                      <td
                        key={j}
                        style={{
                          padding: '10px 10px',
                          color: j === 0 ? C.ink0 : C.ink1,
                          fontFamily: j === 0 ? 'var(--f-body)' : 'var(--f-mono)',
                          whiteSpace: 'nowrap',
                          fontWeight: j === 0 ? 500 : 400,
                        }}
                      >
                        {cell === null || cell === undefined ? '—' : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > 25 && (
              <div
                style={{
                  padding: '10px 14px',
                  fontSize: 11,
                  color: C.ink3,
                  fontFamily: 'var(--f-mono)',
                  letterSpacing: '0.1em',
                  textTransform: 'uppercase',
                  borderTop: `1px solid ${C.lineSoft}`,
                }}
              >
                + {rows.length - 25} more rows
              </div>
            )}
          </div>
        </Card>
      )}

      {sql && <SqlBlock sql={sql} />}
    </div>
  );
}

function SqlBlock({ sql }: { sql: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      style={{
        background: '#FFF',
        border: `1px solid ${C.line}`,
        borderRadius: 12,
        overflow: 'hidden',
      }}
    >
      <button
        onClick={() => setOpen(!open)}
        style={{
          width: '100%',
          padding: '10px 14px',
          background: 'transparent',
          border: 'none',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          cursor: 'pointer',
          color: C.ink2,
        }}
      >
        <span
          style={{
            fontFamily: 'var(--f-mono)',
            fontSize: 10,
            letterSpacing: '0.18em',
            textTransform: 'uppercase',
            fontWeight: 700,
            color: C.cyan,
          }}
        >
          {open ? '▾ SQL' : '▸ SQL'}
        </span>
      </button>
      {open && (
        <pre
          className="selectable no-scrollbar"
          style={{
            margin: 0,
            padding: '0 14px 14px',
            fontFamily: 'var(--f-mono)',
            fontSize: 11,
            color: C.ink1,
            overflowX: 'auto',
            whiteSpace: 'pre',
          }}
        >
          {sql}
        </pre>
      )}
    </div>
  );
}

function renderInlineEmphasis(text: string) {
  // Highlight numeric tokens and **bold** spans.
  const parts: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*)|(\b\d[\d.,/]*\*?\b)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    if (match[1]) {
      parts.push(<Em key={key++}>{match[1].slice(2, -2)}</Em>);
    } else {
      parts.push(<Em key={key++}>{match[2]}</Em>);
    }
    last = re.lastIndex;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}
