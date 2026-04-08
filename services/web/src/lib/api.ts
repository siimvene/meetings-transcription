const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

export interface TranscriptListItem {
  id: string;
  title: string;
  timestamp: string;
  has_summary: boolean;
  owner_aad_id?: string;
  participant_count?: number;
}

export interface Segment {
  start: string;
  end: string;
  text: string;
  speaker: string | null;
  language?: string;
  translated_text?: string;
}

export interface Transcript {
  id: string;
  title: string;
  timestamp: string;
  language: string;
  segments: Segment[];
  transcript: string;
  summary: string;
  owner_aad_id?: string;
}

async function apiFetch<T>(
  path: string,
  token: string,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

export async function listTranscripts(
  token: string
): Promise<TranscriptListItem[]> {
  const data = await apiFetch<{ transcripts: TranscriptListItem[] }>(
    "/transcripts",
    token
  );
  return data.transcripts;
}

export async function getTranscript(
  id: string,
  token: string
): Promise<Transcript> {
  return apiFetch<Transcript>(`/transcripts/${id}`, token);
}

export function getWebSocketUrl(): string {
  const wsBase = API_URL.replace(/^http/, "ws");
  return `${wsBase}/ws/transcribe`;
}
