/**
 * Backend API client. Reaches the FastAPI server (DuckDB engine) over HTTPS.
 *
 * Base URL is supplied via VITE_API_BASE_URL. In dev we proxy /api → backend.
 * Auth: every request attaches the current Supabase JWT (if signed in) so
 * the backend can scope chat_history / bookmarks per user.
 */
import { supabase } from './supabase';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') || '';

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string> | undefined),
    ...(await authHeaders()),
  };
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

// ── Public API surface ──────────────────────────────────────────────────────

export interface AskHistoryTurn {
  question: string;
  context_summary?: string;
  sql?: string;
}

export interface AskRequest {
  question: string;
  history?: AskHistoryTurn[];
}

export interface AskResponse {
  question: string;
  sql: string | null;
  columns: string[];
  rows: Array<Array<string | number | null>>;
  answer: string;
  error: string | null;
  chart_config?: Record<string, unknown> | null;
  context_summary?: string | null;
  display_hint?: Record<string, unknown> | null;
  sections?: Record<string, unknown> | null;
  model_used?: string | null;
  cached?: boolean;
  candidates?: Array<Record<string, unknown>> | null;
  original_question?: string | null;
  profile?: Record<string, unknown> | null;
}

export interface ChatHistoryItem {
  id: string;
  user_id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface BookmarkItem {
  id: string;
  user_id: string;
  title: string;
  query: string;
  answer: string | null;
  tags: string[];
  created_at: string;
}

export const api = {
  ask: (body: AskRequest) =>
    request<AskResponse>('/api/ask', { method: 'POST', body: JSON.stringify(body) }),

  health: () => request<{ status: string }>('/health'),

  stats: () => request<Record<string, unknown>>('/api/stats'),

  // User-scoped (require auth)
  me: () =>
    request<{ user: { id: string; email: string | null; role: string } | null; auth_enabled: boolean }>(
      '/api/auth/me'
    ),

  chatHistory: (sessionId?: string, limit = 100) => {
    const qs = new URLSearchParams();
    if (sessionId) qs.set('session_id', sessionId);
    qs.set('limit', String(limit));
    return request<{ items: ChatHistoryItem[] }>(`/api/chat/history?${qs.toString()}`);
  },

  saveChatTurn: (turn: { session_id: string; role: string; content: string; metadata?: Record<string, unknown> }) =>
    request<{ item: ChatHistoryItem }>('/api/chat/history', {
      method: 'POST',
      body: JSON.stringify(turn),
    }),

  listBookmarks: () => request<{ items: BookmarkItem[] }>('/api/bookmarks'),

  addBookmark: (b: { title: string; query: string; answer?: string; tags?: string[] }) =>
    request<{ item: BookmarkItem }>('/api/bookmarks', {
      method: 'POST',
      body: JSON.stringify(b),
    }),

  deleteBookmark: (id: string) =>
    request<{ ok: boolean }>(`/api/bookmarks/${id}`, { method: 'DELETE' }),
};

export const apiBaseUrl = API_BASE;
