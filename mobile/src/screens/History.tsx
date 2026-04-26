import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ScreenShell, AppHeader, IconBtn, Card, Pill, SectionLabel } from '../components/ui';
import { C } from '../theme';
import { api, type ChatHistoryItem, type BookmarkItem } from '../lib/api';
import { useAuth } from '../lib/AuthContext';

export function ScreenHistory() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [tab, setTab] = useState<'recent' | 'saved'>('recent');
  const [history, setHistory] = useState<ChatHistoryItem[]>([]);
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!user) return;
    setLoading(true);
    Promise.all([api.chatHistory().catch(() => ({ items: [] })), api.listBookmarks().catch(() => ({ items: [] }))])
      .then(([h, b]) => {
        setHistory(h.items);
        setBookmarks(b.items);
      })
      .finally(() => setLoading(false));
  }, [user]);

  const sessions = groupBySession(history);

  return (
    <ScreenShell>
      <AppHeader
        title="History"
        subtitle="Your conversations"
        big
        trailing={
          <IconBtn ariaLabel="Search">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.7" />
              <path d="M16 16l4 4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
          </IconBtn>
        }
      />

      <div style={{ padding: '0 16px 16px' }}>
        <div style={{ display: 'flex', gap: 8 }}>
          {(['recent', 'saved'] as const).map((id) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              style={{
                flex: 1,
                height: 38,
                borderRadius: 12,
                border: `1px solid ${tab === id ? C.primary : C.line}`,
                background: tab === id ? C.primarySoft : '#fff',
                color: tab === id ? C.primary : C.ink2,
                fontFamily: 'var(--f-mono)',
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                cursor: 'pointer',
              }}
            >
              {id === 'recent' ? `Recent (${sessions.length})` : `Saved (${bookmarks.length})`}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: '0 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {!user && (
          <Card>
            <div style={{ fontSize: 13, color: C.ink2, lineHeight: 1.5 }}>
              Sign in to sync your conversations and bookmarks across devices.
            </div>
            <button
              onClick={() => navigate('/onboarding')}
              style={{
                marginTop: 12,
                height: 40,
                borderRadius: 12,
                border: 'none',
                background: C.primary,
                color: '#fff',
                fontFamily: 'var(--f-mono)',
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                cursor: 'pointer',
                paddingInline: 18,
              }}
            >
              Sign in
            </button>
          </Card>
        )}

        {loading && (
          <>
            <Card>
              <div className="skeleton" style={{ height: 14, borderRadius: 4, marginBottom: 8 }} />
              <div className="skeleton" style={{ height: 12, width: '60%', borderRadius: 4 }} />
            </Card>
            <Card>
              <div className="skeleton" style={{ height: 14, borderRadius: 4, marginBottom: 8 }} />
              <div className="skeleton" style={{ height: 12, width: '60%', borderRadius: 4 }} />
            </Card>
          </>
        )}

        {tab === 'recent' && !loading && (
          <>
            <SectionLabel>Sessions</SectionLabel>
            {sessions.length === 0 && !loading && user && (
              <Card>
                <div style={{ fontSize: 13, color: C.ink3 }}>No conversations yet. Start by asking a question.</div>
              </Card>
            )}
            {sessions.map((s) => (
              <button
                key={s.session_id}
                onClick={() => navigate(`/chat/${s.session_id}`)}
                style={{
                  textAlign: 'left',
                  padding: 0,
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                <Card>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <Pill color="primary" size="sm">
                      {s.count} {s.count === 1 ? 'turn' : 'turns'}
                    </Pill>
                    <span
                      style={{
                        fontFamily: 'var(--f-mono)',
                        fontSize: 10,
                        color: C.ink3,
                        letterSpacing: '0.1em',
                      }}
                    >
                      {fmtDate(s.last_at)}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 14,
                      color: C.ink0,
                      fontWeight: 500,
                      overflow: 'hidden',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                  >
                    {s.first_question}
                  </div>
                </Card>
              </button>
            ))}
          </>
        )}

        {tab === 'saved' && !loading && (
          <>
            <SectionLabel>Bookmarks</SectionLabel>
            {bookmarks.length === 0 && user && (
              <Card>
                <div style={{ fontSize: 13, color: C.ink3 }}>No bookmarks yet.</div>
              </Card>
            )}
            {bookmarks.map((b) => (
              <Card key={b.id}>
                <div
                  style={{
                    fontFamily: 'var(--f-display)',
                    fontWeight: 700,
                    fontSize: 15,
                    color: C.ink0,
                    marginBottom: 4,
                  }}
                >
                  {b.title}
                </div>
                <div style={{ fontSize: 13, color: C.ink2, marginBottom: 10 }}>{b.query}</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {b.tags.map((t) => (
                    <Pill key={t} color="cyan" size="sm">
                      {t}
                    </Pill>
                  ))}
                </div>
              </Card>
            ))}
          </>
        )}
      </div>
    </ScreenShell>
  );
}

interface SessionGroup {
  session_id: string;
  first_question: string;
  count: number;
  last_at: string;
}

function groupBySession(items: ChatHistoryItem[]): SessionGroup[] {
  const m = new Map<string, ChatHistoryItem[]>();
  for (const it of items) {
    const a = m.get(it.session_id) ?? [];
    a.push(it);
    m.set(it.session_id, a);
  }
  const out: SessionGroup[] = [];
  for (const [sid, arr] of m) {
    arr.sort((a, b) => a.created_at.localeCompare(b.created_at));
    const firstUser = arr.find((x) => x.role === 'user');
    out.push({
      session_id: sid,
      first_question: firstUser?.content ?? '(no question)',
      count: arr.length,
      last_at: arr[arr.length - 1].created_at,
    });
  }
  out.sort((a, b) => b.last_at.localeCompare(a.last_at));
  return out;
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    const now = Date.now();
    const diff = now - d.getTime();
    const min = Math.floor(diff / 60000);
    if (min < 1) return 'just now';
    if (min < 60) return `${min}m ago`;
    const h = Math.floor(min / 60);
    if (h < 24) return `${h}h ago`;
    const days = Math.floor(h / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString();
  } catch {
    return '';
  }
}
