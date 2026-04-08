# Meetings Transcription — Architecture Plan

## Context

On-premise transcription and summarization of sensitive Teams meetings. No audio or text leaves the organization's infrastructure. The GPU server runs an LLM via vLLM with spare VRAM for Whisper.

## User Flow

1. User creates a Teams meeting and **adds the bot as a participant** (like adding any colleague)
2. When the meeting starts, the bot auto-answers and begins capturing audio
3. Teams shows a recording indicator to all participants
4. During the meeting: live transcript available to the inviter via the web UI
5. After the meeting: the inviter gets the full transcript + AI summary
6. Only the inviter can access the transcript (owner-only access model)

## Architecture Overview

```
┌─── ON-PREMISE PUBLIC SERVER (Windows) ────────────┐
│                                                     │
│   Teams Media Bot (.NET 8 / C#, Windows Server)    │
│   - User adds bot as meeting participant            │
│   - Captures per-participant unmixed audio (16kHz)  │
│   - Maps Media Source ID → participant name          │
│   - Forwards raw PCM via gRPC to B200 server       │
│                                                     │
│   Requires: public IP, UDP ports 3478-3481 +       │
│   49152-53247 open for MS media relays             │
│   Cost: zero (existing infrastructure)             │
└──────────────────────┬──────────────────────────────┘
                       │ Internal network / WireGuard
                       │ gRPC streams (PCM 16kHz per participant)
┌──────────────────────┴──────────────────────────────┐
│   ON-PREMISE B200 SERVER (gpu-server-ip)           │
│                                                      │
│   ┌─────────────────────────────────────────────┐   │
│   │ Audio Ingestion (Python, asyncio)           │   │
│   │ Receives per-participant PCM streams        │   │
│   │ Feeds to WhisperLiveKit via WebSocket       │   │
│   └──────────────────┬──────────────────────────┘   │
│                      │                               │
│   ┌──────────────────┴──────────────────────────┐   │
│   │ WhisperLiveKit + faster-whisper             │   │
│   │ large-v3-turbo, ~4GB VRAM                   │   │
│   │ Per-participant ASR → text + language        │   │
│   └──────────────────┬──────────────────────────┘   │
│                      │                               │
│   ┌──────────────────┴──────────────────────────┐   │
│   │ Sortformer Diarization (~2GB VRAM)          │   │
│   │ For room/shared-mic participants only       │   │
│   │ Splits mixed room audio → individual spkrs  │   │
│   └──────────────────┬──────────────────────────┘   │
│                      │                               │
│   ┌──────────────────┴──────────────────────────┐   │
│   │ Transcript Assembly (Python)                │   │
│   │ Merges ASR output with participant names    │   │
│   │ Time-ordered, speaker-attributed segments   │   │
│   └───────┬──────────────────┬──────────────────┘   │
│           │                  │                       │
│   ┌───────┴────────┐ ┌──────┴──────────────────┐   │
│   │ TartuNLP NMT   │ │ Gemma 4 31B (vLLM)     │   │
│   │ septilang      │ │ Post-meeting summary    │   │
│   │ EN→ET via      │ │ Already running         │   │
│   │ RabbitMQ       │ │                          │   │
│   └───────┬────────┘ └──────┬──────────────────┘   │
│           │                  │                       │
│   ┌───────┴──────────────────┴──────────────────┐   │
│   │ PostgreSQL                                   │   │
│   │ meetings, participants, segments, summaries  │   │
│   └──────────────────┬──────────────────────────┘   │
│                      │                               │
│   ┌──────────────────┴──────────────────────────┐   │
│   │ Web App                                      │   │
│   │ Backend: FastAPI + WebSocket (live view)     │   │
│   │ Frontend: React/Next.js                      │   │
│   │ Auth: Azure Entra ID SSO (OIDC)             │   │
│   └─────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. Why an Azure VM is required
The Microsoft Graph Communications Media SDK (for raw audio access) is .NET-only, Windows-only, and requires a public IP reachable by Microsoft's media relays. A small Azure VM (~70 EUR/month) captures audio and forwards it on-premise. No AI runs on this VM.

### 2. Speaker identification: two paths
- **Remote participants** (individual devices): Teams Bot provides unmixed per-participant audio streams tagged with MSI → deterministic speaker attribution from the call roster. No diarization needed.
- **Room participants** (shared conference room mic / Teams Room device): Multiple people share one audio channel. The bot gets a single mixed stream for the room device. **Sortformer is required** here to separate speakers within the room's audio. Sortformer labels them as "Room Speaker 1", "Room Speaker 2" etc. Users can optionally map these to names post-meeting via the web UI.

### 3. Translation approach
TartuNLP septilang NMT via RabbitMQ for segment-level EN→ET translation. Higher quality than LLM translation for short segments. RabbitMQ pattern already proven in the 112 system.

### 4. Summarization
Post-meeting: full transcript sent to Gemma 4 31B via the existing vLLM endpoint. Structured output: participants, decisions, action items, summary.

## Components

| Component | Language | Runs On | GPU |
|---|---|---|---|
| Teams Media Bot | C# / .NET 8 | On-premise Windows Server (public IP) | No |
| Audio Ingestion | Python | B200 server (Docker) | No |
| WhisperLiveKit | Python | B200 server (Docker) | ~4 GB |
| Sortformer | Python (NeMo) | B200 server (Docker) | ~2 GB (for room audio) |
| Transcript Assembly | Python | B200 server (Docker) | No |
| NMT Worker | Python | B200 server (Docker) | CPU |
| RabbitMQ | - | B200 server (Docker) | No |
| Summarizer | Python | B200 server (Docker) | No (API call) |
| PostgreSQL | - | B200 server (Docker) | No |
| Web Backend (FastAPI) | Python | B200 server (Docker) | No |
| Web Frontend (Next.js) | TypeScript | B200 server (Docker) | No |

## Azure Setup Required

### App Registration — Bot
- Register "MeetingTranscriptionBot" in Entra ID
- Permissions (Application, admin consent): `Calls.JoinGroupCall.All`, `Calls.AccessMedia.All`
- Register as Azure Bot, enable Teams channel with calling webhook
- No compliance recording policy needed — bot is invited as a participant

### App Registration — Web UI
- Register "MeetingTranscriptViewer" for web SSO
- Permissions (Delegated): `User.Read`
- Redirect URI: SPA → `https://transcripts.yourdomain.ee/auth/callback`

### On-Premise Bot Host (Windows Server)
- Existing on-premise server with public IP
- Windows Server 2022, .NET 8 runtime
- Firewall: open UDP 3478-3481 + 49152-53247 for Microsoft media relays
- Network connectivity to B200 server (internal network or WireGuard)
- No Azure VM needed — zero cloud compute cost

### Bot Identity in Teams
- The bot appears as a named user (e.g. "Transcription Bot") that can be added to any meeting
- When the meeting starts, bot auto-answers the call and captures audio
- Bot calls `updateRecordingStatus` → Teams shows recording indicator to all participants

## Data Flow

1. User adds "Transcription Bot" as a participant in a Teams meeting invite
2. Meeting starts → Teams calls the bot → bot auto-answers, requests unmixed audio
3. Bot identifies the **inviter** from the call notification (organizer/inviter AAD identity) → stored as transcript owner
4. Bot receives 16kHz 16-bit PCM per participant (50 frames/sec, 640 bytes/frame)
5. Bot streams PCM over gRPC through WireGuard to B200
5. Audio Ingestion feeds per-participant streams to WhisperLiveKit
6. Whisper produces text segments with timestamps + detected language
7. For room/shared-device streams: Sortformer splits mixed audio into individual speakers ("Room Speaker 1/2/...")
8. Transcript Assembly merges with participant names (remote) or speaker labels (room), streams to web UI via WebSocket
8. Non-Estonian segments → RabbitMQ → septilang NMT → Estonian translation
9. Meeting ends → full transcript sent to Gemma 4 → structured summary
10. Everything persisted to PostgreSQL

## Database Schema

```sql
meetings (id, teams_call_id, title, started_at, ended_at, status, owner_aad_id)
participants (id, meeting_id FK, aad_user_id, display_name, email, msi_id)
transcript_segments (id, meeting_id FK, participant_id FK, start_ms, end_ms, text, translated_text, language, confidence)
summaries (id, meeting_id FK, summary_text, language, model, generated_at)
```

## Implementation Phases

| Phase | Scope | Duration |
|---|---|---|
| 1 | Bot: Entra app registration, deploy C# media bot on on-premise Windows server, network to B200 | 2 weeks |
| 2 | Audio pipeline: gRPC bridge, WhisperLiveKit on B200 | 2 weeks |
| 3 | Transcript assembly + PostgreSQL storage | 1-2 weeks |
| 4 | Translation (septilang + RabbitMQ) + Summarization (Gemma) | 1-2 weeks |
| 5 | Web UI: Entra SSO, transcript viewer, live view | 2 weeks |
| 6 | Hardening: monitoring, retention, audit logging | 1-2 weeks |

## VRAM Budget

| Component | VRAM |
|---|---|
| Gemma 4 31B (vLLM, 0.86) | ~155 GB |
| Whisper large-v3-turbo | ~4 GB |
| Sortformer (room diarization) | ~2 GB |
| Headroom | ~22 GB |
| **Total B200** | **183 GB** |

## Risks

| Risk | Mitigation |
|---|---|
| Media SDK requires public IP | On-premise Windows server with public IP + open UDP ports for MS media relays |
| GPU memory pressure (Whisper + Gemma concurrent) | Queue summarization; Whisper INT8 reduces to ~3GB |
| WireGuard latency | Buffer 2-3s on ingestion; WireGuard adds <5ms typically |
| Compliance certification | Not required for internal-only use within own tenant |

## Files to Create/Modify

### New repo: `meetings-transcription/`
- `docker-compose.yml` — all on-premise services
- `services/transcription/` — WhisperLiveKit container (already created)
- `services/ingestion/` — gRPC server receiving audio from Azure bot
- `services/assembly/` — transcript merging + WebSocket broadcasting
- `services/summarizer/` — vLLM API caller (already created)
- `services/api/` — FastAPI backend (already created, needs expansion)
- `services/web/` — Next.js frontend
- `config/` — env files, nginx config
- `deploy/` — setup scripts (already created)
- `schema/` — PostgreSQL migrations

### New repo: `meetings-transcription-bot/`
- C# .NET 8 project based on Microsoft's PolicyRecordingBot sample
- gRPC client for streaming audio to on-premise
- WireGuard integration docs

### Copy from `hk-kone-transkriptsioon/`
- `translation-worker/` — TartuNLP septilang NMT worker (as-is)
- `translation-worker/config/config.yaml` — model configuration

## Verification

1. **Bot joins meeting**: User adds bot to meeting invite, bot auto-answers when meeting starts, Teams shows recording indicator
2. **Audio captured**: gRPC server logs PCM frames with participant IDs
3. **Transcription works**: Whisper produces text from live audio
4. **Speaker attribution**: Segments correctly tagged with participant display names
5. **Translation**: English segments translated to Estonian
6. **Summarization**: Post-meeting summary generated with decisions + action items
7. **Web UI**: User logs in via Entra SSO, sees only meetings they invited the bot to (owner-only access)
8. **Live view**: Opening active meeting shows transcript updating in real-time
