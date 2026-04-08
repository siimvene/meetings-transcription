# On-Premise Teams Meeting Transcription & Summarization

Fully on-premise system for transcribing Microsoft Teams meetings with speaker identification, real-time translation, and AI-generated summaries. No audio or text leaves your infrastructure.

## What It Does

1. User adds **Transcription Bot** as a participant in a Teams meeting
2. Bot joins automatically when the meeting starts, shows a recording indicator
3. Posts an intro message to the meeting chat
4. Transcribes each participant's audio in real-time using Whisper
5. Identifies speakers by name (remote participants) or Sortformer diarization (room devices)
6. At meeting halftime, posts a summary to the meeting chat
7. At meeting end, posts the final summary to the chat
8. Full transcript + summary stored in PostgreSQL, accessible via web UI with Azure Entra SSO

## Architecture

```
┌─── WINDOWS SERVER (public IP) ─────────────────────┐
│                                                      │
│   Teams Media Bot (.NET 8 / C#)                     │
│   - Captures per-participant unmixed audio (16kHz)   │
│   - Maps speakers to display names from Teams roster │
│   - Reads + writes to meeting chat (Graph API)       │
│   - Forwards PCM audio via gRPC                      │
│                                                      │
└──────────────────────┬───────────────────────────────┘
                       │ gRPC (PCM audio streams)
┌──────────────────────┴───────────────────────────────┐
│   GPU SERVER (Docker Compose, 9 services)            │
│                                                      │
│   Ingestion ──→ WhisperLiveKit ──→ Assembly          │
│   (gRPC)        (ASR, GPU)         (PostgreSQL)      │
│                      │                  │            │
│               Sortformer          ┌─────┴─────┐     │
│               (room audio,    Translation  Summarizer│
│                GPU)           (NMT/RabbitMQ) (LLM)  │
│                                                      │
│   Web App (Next.js + FastAPI, Entra SSO)            │
└──────────────────────────────────────────────────────┘
```

## Components

| Service | Purpose | GPU |
|---|---|---|
| **Teams Media Bot** | Joins meetings, captures audio, posts to chat | None |
| **Ingestion** | Receives gRPC audio, feeds to Whisper | None |
| **Transcription** | WhisperLiveKit + Whisper large-v3-turbo | ~4 GB |
| **Sortformer** | Speaker diarization for shared room mics | ~2 GB |
| **Assembly** | Merges transcripts, triggers translation + summary | None |
| **Translation Worker** | TartuNLP septilang NMT (EN/RU/FI/DE/LV/LT to ET) | CPU |
| **RabbitMQ** | Message broker for translation requests | None |
| **Summarizer** | Sends transcripts to LLM for structured summaries | None (API) |
| **PostgreSQL** | Stores meetings, segments, participants, summaries | None |
| **Web App** | Next.js frontend + FastAPI backend, Entra SSO | None |

## Bot Behavior in Meeting Chat

| When | Message |
|---|---|
| **Join** | Intro: explains transcription is active |
| **Halftime** | Mid-meeting summary of discussion so far |
| **End** | Final structured summary with decisions + action items |

Summaries are generated in the detected meeting language.

## Repository Structure

```
meetings-transcription/          # GPU server services (this repo)
├── docker-compose.yml           # 9 services
├── proto/                       # gRPC contract (shared with bot)
├── schema/                      # PostgreSQL migrations
├── config/                      # Environment configuration
├── scripts/                     # Model download scripts
├── deploy/                      # Nginx, setup scripts
└── services/
    ├── transcription/           # WhisperLiveKit + Sortformer
    ├── ingestion/               # gRPC audio receiver
    ├── assembly/                # Transcript merging + orchestration
    ├── summarizer/              # LLM summary proxy
    ├── translation-worker/      # TartuNLP NMT
    ├── api/                     # FastAPI backend
    └── web/                     # Next.js frontend

meetings-transcription-bot/      # Windows server (separate repo)
├── src/                         # C# .NET 8 bot
│   ├── BotService.cs            # Call handling, chat interaction
│   ├── AudioHandler.cs          # Per-participant audio capture
│   ├── GrpcForwarder.cs         # Audio streaming to GPU server
│   └── Models/                  # Configuration types
├── teams-app/                   # Teams app manifest + icons
└── TranscriptionBot.zip         # Ready-to-upload Teams app package
```
