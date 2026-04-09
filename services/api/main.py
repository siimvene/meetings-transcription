"""API gateway — file upload, WebSocket streaming, transcript retrieval."""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import uuid as uuid_mod
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import asyncpg
import httpx
import websockets
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import jwt
from jwt import PyJWKClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PCM_WAV_SUFFIX = ".pcm.wav"
TRANSCRIPTION_URL = os.environ.get("TRANSCRIPTION_URL", "ws://transcription:8000/asr")
SUMMARIZER_URL = os.environ.get("SUMMARIZER_URL", "http://summarizer:8001")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data/transcripts"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://meetings:changeme@postgres:5432/meetings"
)
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")

OPENID_CONFIG_URL = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/v2.0/.well-known/openid-configuration"
JWKS_URL = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/discovery/v2.0/keys"
ISSUER = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/v2.0"

# Global state
db_pool: asyncpg.Pool | None = None
jwk_client: PyJWKClient | None = None
security = HTTPBearer()


# --- Auth ---


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Validate Azure AD JWT and return user claims."""
    token = credentials.credentials

    if not AZURE_TENANT_ID or not AZURE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Azure AD not configured")

    try:
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AZURE_CLIENT_ID,
            issuer=ISSUER,
        )
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning("JWT validation failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")


def get_user_oid(claims: dict) -> str:
    """Extract user object ID from JWT claims."""
    return claims.get("oid") or claims.get("sub", "")


# --- Lifecycle ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, jwk_client

    # Connect to PostgreSQL
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        logger.info("Connected to PostgreSQL")
    except Exception:
        logger.exception("Failed to connect to PostgreSQL")
        raise

    # Initialize JWKS client for token validation
    if AZURE_TENANT_ID:
        jwk_client = PyJWKClient(JWKS_URL, cache_keys=True)
        logger.info("JWKS client initialized for tenant %s", AZURE_TENANT_ID)

    yield

    if db_pool:
        await db_pool.close()
        logger.info("PostgreSQL connection closed")


app = FastAPI(title="Meetings Transcription API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "database": db_pool is not None, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/transcribe")
async def transcribe_file(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    summarize: bool = Form(True),
    title: str = Form(""),
):
    """Upload an audio file for transcription and optional summarization."""
    transcript_id = str(uuid_mod.uuid4())[:8]
    timestamp = datetime.now(timezone.utc).isoformat()

    # Save uploaded file temporarily
    suffix = Path(file.filename or "audio.wav").suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # Convert to PCM 16kHz mono WAV using ffmpeg
        pcm_path = tmp_path + PCM_WAV_SUFFIX
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", tmp_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", pcm_path,
            ],
            capture_output=True,
            check=True,
        )

        # Read converted audio
        async with aiofiles.open(pcm_path, "rb") as f:
            pcm_data = await f.read()

        # Stream to WhisperLiveKit and collect results
        segments = await _transcribe_audio(pcm_data)
    finally:
        os.unlink(tmp_path)
        if os.path.exists(tmp_path + PCM_WAV_SUFFIX):
            os.unlink(tmp_path + PCM_WAV_SUFFIX)

    # Build diarized transcript text
    transcript_text = _segments_to_text(segments)

    # Summarize if requested
    summary = ""
    if summarize and transcript_text.strip():
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{SUMMARIZER_URL}/summarize",
                    json={"transcript": transcript_text, "language": language},
                )
                if resp.status_code == 200:
                    summary = resp.json()["summary"]
        except Exception as e:
            summary = f"Summarization failed: {e}"

    # Save result
    result = {
        "id": transcript_id,
        "title": title or file.filename or "Untitled",
        "timestamp": timestamp,
        "language": language,
        "segments": segments,
        "transcript": transcript_text,
        "summary": summary,
    }

    result_path = DATA_DIR / f"{transcript_id}.json"
    async with aiofiles.open(result_path, "w") as f:
        await f.write(json.dumps(result, ensure_ascii=False, indent=2))

    return result


@app.get("/transcripts")
async def list_transcripts(claims: dict = Depends(get_current_user)):
    """List meetings owned by the authenticated user."""
    user_oid = get_user_oid(claims)
    if not user_oid:
        raise HTTPException(status_code=401, detail="User identity not found in token")

    rows = await db_pool.fetch(
        """SELECT m.id, m.title, m.started_at, m.ended_at, m.status, m.owner_aad_id,
                  (SELECT count(*) FROM participants p WHERE p.meeting_id = m.id) AS participant_count,
                  (SELECT count(*) > 0 FROM summaries s WHERE s.meeting_id = m.id) AS has_summary
           FROM meetings m
           WHERE m.owner_aad_id = $1
           ORDER BY m.started_at DESC""",
        user_oid,
    )

    transcripts = [
        {
            "id": str(row["id"]),
            "title": row["title"] or "Untitled Meeting",
            "timestamp": row["started_at"].isoformat() if row["started_at"] else "",
            "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
            "status": row["status"],
            "owner_aad_id": row["owner_aad_id"],
            "participant_count": row["participant_count"],
            "has_summary": row["has_summary"],
        }
        for row in rows
    ]

    return {"transcripts": transcripts}


@app.get("/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str, claims: dict = Depends(get_current_user)):
    """Retrieve a specific transcript. Only the owner can access it."""
    user_oid = get_user_oid(claims)
    if not user_oid:
        raise HTTPException(status_code=401, detail="User identity not found in token")

    # Validate UUID format
    try:
        meeting_uuid = uuid_mod.UUID(transcript_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid transcript ID format")

    # Verify ownership
    meeting = await db_pool.fetchrow(
        "SELECT id, title, started_at, ended_at, status, owner_aad_id FROM meetings WHERE id = $1",
        meeting_uuid,
    )
    if not meeting:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if meeting["owner_aad_id"] != user_oid:
        raise HTTPException(status_code=403, detail="Access denied")

    # Fetch segments with speaker names
    segments = await db_pool.fetch(
        """SELECT ts.start_ms, ts.end_ms, ts.original_text, ts.translated_text,
                  ts.source_language, ts.confidence, p.display_name
           FROM transcript_segments ts
           LEFT JOIN participants p ON ts.participant_id = p.id
           WHERE ts.meeting_id = $1
           ORDER BY ts.start_ms""",
        meeting_uuid,
    )

    # Fetch summary
    summary_row = await db_pool.fetchrow(
        "SELECT summary_text FROM summaries WHERE meeting_id = $1 ORDER BY generated_at DESC LIMIT 1",
        meeting_uuid,
    )

    # Build transcript text
    formatted_segments = [
        {
            "start": _ms_to_timestamp(row["start_ms"]),
            "end": _ms_to_timestamp(row["end_ms"]),
            "text": row["original_text"],
            "translated_text": row["translated_text"],
            "speaker": row["display_name"],
            "language": row["source_language"] or "",
        }
        for row in segments
    ]

    return {
        "id": str(meeting["id"]),
        "title": meeting["title"] or "Untitled Meeting",
        "timestamp": meeting["started_at"].isoformat() if meeting["started_at"] else "",
        "language": "",
        "segments": formatted_segments,
        "transcript": _segments_to_text(formatted_segments),
        "summary": summary_row["summary_text"] if summary_row else "",
        "owner_aad_id": meeting["owner_aad_id"],
    }


def _ms_to_timestamp(ms: int) -> str:
    """Convert milliseconds to H:MM:SS format."""
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}"


@app.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket):
    """Real-time streaming transcription via WebSocket.

    Client sends raw PCM 16kHz audio chunks.
    Server sends back JSON segments as they are produced.
    """
    await websocket.accept()

    try:
        async with websockets.connect(TRANSCRIPTION_URL) as wlk_ws:

            async def forward_audio():
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await wlk_ws.send(data)
                except Exception:
                    await wlk_ws.send(bytes())

            async def forward_transcripts():
                try:
                    async for message in wlk_ws:
                        await websocket.send_text(
                            message if isinstance(message, str) else message.decode()
                        )
                except websockets.ConnectionClosed:
                    pass

            await asyncio.gather(forward_audio(), forward_transcripts())

    except Exception as e:
        await websocket.close(code=1011, reason=str(e)[:120])


async def _transcribe_audio(pcm_data: bytes) -> list[dict]:
    """Send audio to WhisperLiveKit and collect all segments."""
    segments = []
    chunk_size = 16000 * 2  # 1 second of 16kHz 16-bit PCM

    try:
        async with websockets.connect(TRANSCRIPTION_URL) as ws:
            await _send_audio_chunks(ws, pcm_data, chunk_size)
            await _collect_segments(ws, segments)
    except Exception as e:
        segments.append({"error": str(e), "start": "0:00:00", "end": "0:00:00"})

    return segments


async def _send_audio_chunks(ws, pcm_data: bytes, chunk_size: int):
    """Send audio data in chunks and signal end of stream."""
    for i in range(0, len(pcm_data), chunk_size):
        chunk = pcm_data[i : i + chunk_size]
        await ws.send(chunk)
        await asyncio.sleep(0.05)
    await ws.send(bytes())


async def _collect_segments(ws, segments: list[dict]):
    """Collect transcription segments from WebSocket responses."""
    async for message in ws:
        data = _parse_ws_message(message)
        if data is None or data.get("type") == "config":
            continue

        if data.get("type") == "ready_to_stop":
            for line in data.get("lines", []):
                segments.append(_parse_segment(line))
            break

        for line in data.get("lines", []):
            _upsert_segment(segments, _parse_segment(line))


def _parse_ws_message(message) -> dict | None:
    """Parse a WebSocket message as JSON, returning None on failure."""
    try:
        return json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return None


def _upsert_segment(segments: list[dict], parsed: dict):
    """Update an existing segment or append a new one."""
    existing = next((s for s in segments if s["start"] == parsed["start"]), None)
    if existing:
        existing.update(parsed)
    else:
        segments.append(parsed)


def _parse_segment(line: dict) -> dict:
    return {
        "start": str(line.get("start", "0:00:00")),
        "end": str(line.get("end", "0:00:00")),
        "text": line.get("text", "").strip(),
        "speaker": line.get("speaker", None),
        "language": line.get("detected_language", ""),
    }


def _segments_to_text(segments: list[dict]) -> str:
    """Format segments into a readable diarized transcript."""
    lines = []
    current_speaker = None

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue

        speaker = seg.get("speaker") or "Unknown"
        start = seg.get("start", "")

        if speaker != current_speaker:
            lines.append(f"\n[{speaker}] ({start})")
            current_speaker = speaker

        lines.append(text)

    return "\n".join(lines).strip()
