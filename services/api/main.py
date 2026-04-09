"""API gateway — file upload, WebSocket streaming, transcript retrieval."""

import asyncio
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import httpx
import websockets
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import JSONResponse

app = FastAPI(title="Meetings Transcription API")

PCM_WAV_SUFFIX = ".pcm.wav"
TRANSCRIPTION_URL = os.environ.get("TRANSCRIPTION_URL", "ws://transcription:8000/asr")
SUMMARIZER_URL = os.environ.get("SUMMARIZER_URL", "http://summarizer:8001")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data/transcripts"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/transcribe")
async def transcribe_file(
    file: UploadFile = File(...),
    language: str = Form("auto"),
    summarize: bool = Form(True),
    title: str = Form(""),
):
    """Upload an audio file for transcription and optional summarization."""
    transcript_id = str(uuid.uuid4())[:8]
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
async def list_transcripts():
    """List all stored transcripts."""
    transcripts = []
    for path in sorted(DATA_DIR.glob("*.json"), reverse=True):
        async with aiofiles.open(path) as f:
            data = json.loads(await f.read())
            transcripts.append({
                "id": data["id"],
                "title": data["title"],
                "timestamp": data["timestamp"],
                "has_summary": bool(data.get("summary")),
            })
    return {"transcripts": transcripts}


@app.get("/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str):
    """Retrieve a specific transcript."""
    path = DATA_DIR / f"{transcript_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    async with aiofiles.open(path) as f:
        return json.loads(await f.read())


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
