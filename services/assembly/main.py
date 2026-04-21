"""Transcript Assembly Service — merges ASR output with participant identities,
handles translation requests via RabbitMQ, triggers post-meeting summarization.

Exposes HTTP endpoints for the ingestion service to store segments and trigger summarization."""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

import aio_pika
import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_NOT_AVAILABLE = "Database not available"

DATABASE_URL = os.environ["DATABASE_URL"]
MQ_HOST = os.environ.get("MQ_HOST", "rabbitmq")
MQ_PORT = int(os.environ.get("MQ_PORT", "5672"))
MQ_USERNAME = os.environ.get("MQ_USERNAME", "user")
MQ_PASSWORD = os.environ.get("MQ_PASSWORD", "password")
MQ_EXCHANGE = os.environ.get("MQ_EXCHANGE", "translation")

SUMMARIZER_URL = os.environ.get("SUMMARIZER_URL", "http://summarizer:8001")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8080"))

# Languages that should be translated to Estonian
TRANSLATE_LANGUAGES = {"en", "eng", "ru", "rus", "de", "ger", "fi", "fin"}
LANG_2_TO_3 = {"en": "eng", "ru": "rus", "de": "ger", "fi": "fin"}

# SQL queries
SQL_MEETING_BY_CALL_ID = "SELECT id FROM meetings WHERE teams_call_id = $1"

# Global state
db_pool: asyncpg.Pool | None = None
mq_connection: aio_pika.abc.AbstractRobustConnection | None = None
mq_channel: aio_pika.abc.AbstractChannel | None = None
translation_callback_queue: str | None = None
_background_tasks: set[asyncio.Task] = set()

# Caches: external Teams IDs -> internal UUIDs
_meeting_id_cache: dict[str, uuid.UUID] = {}
_participant_id_cache: dict[tuple[str, str], uuid.UUID] = {}


# --- Pydantic models for HTTP endpoints ---


class MeetingCreateRequest(BaseModel):
    meeting_id: str
    owner_aad_id: str
    meeting_title: str = "Untitled Meeting"


class ParticipantRequest(BaseModel):
    meeting_id: str
    participant_id: str
    display_name: str
    email: str = ""


class SegmentRequest(BaseModel):
    meeting_id: str
    participant_id: str
    start_ms: int
    end_ms: int
    text: str
    language: str = "unknown"
    confidence: float = 0.0


class ChatMessageItem(BaseModel):
    sender_name: str
    text: str
    timestamp: str = ""


class EndMeetingRequest(BaseModel):
    meeting_id: str
    owner_aad_id: str
    chat_messages: list[ChatMessageItem] = []


# --- ID resolution helpers ---


async def _resolve_meeting_id(teams_call_id: str) -> uuid.UUID:
    """Resolve a Teams call ID string to the internal meeting UUID."""
    if teams_call_id in _meeting_id_cache:
        return _meeting_id_cache[teams_call_id]

    # If it's already a valid UUID, try direct lookup by PK
    try:
        as_uuid = uuid.UUID(teams_call_id)
        exists = await db_pool.fetchval(
            "SELECT id FROM meetings WHERE id = $1", as_uuid
        )
        if exists:
            _meeting_id_cache[teams_call_id] = as_uuid
            return as_uuid
    except ValueError:
        pass

    # Look up by teams_call_id
    internal_id = await db_pool.fetchval(
        SQL_MEETING_BY_CALL_ID, teams_call_id
    )
    if internal_id is None:
        raise HTTPException(status_code=404, detail=f"Meeting not found: {teams_call_id}")
    _meeting_id_cache[teams_call_id] = internal_id
    return internal_id


async def _resolve_participant_id(teams_call_id: str, ext_participant_id: str) -> uuid.UUID:
    """Resolve an external participant ID to the internal UUID."""
    cache_key = (teams_call_id, ext_participant_id)
    if cache_key in _participant_id_cache:
        return _participant_id_cache[cache_key]

    internal_meeting_id = await _resolve_meeting_id(teams_call_id)
    internal_id = await db_pool.fetchval(
        "SELECT id FROM participants WHERE meeting_id = $1 AND aad_user_id = $2",
        internal_meeting_id,
        ext_participant_id,
    )
    if internal_id is None:
        # Auto-register with placeholder name
        internal_id = await db_pool.fetchval(
            """INSERT INTO participants (meeting_id, aad_user_id, display_name, joined_at)
               VALUES ($1, $2, $3, NOW())
               ON CONFLICT (meeting_id, aad_user_id)
               DO UPDATE SET display_name = EXCLUDED.display_name
               RETURNING id""",
            internal_meeting_id,
            ext_participant_id,
            "Unknown",
        )
    _participant_id_cache[cache_key] = internal_id
    return internal_id


# --- Database operations ---


async def store_segment(
    pool: asyncpg.Pool, meeting_id: uuid.UUID, participant_id: uuid.UUID, segment: dict
) -> uuid.UUID:
    """Store a transcript segment in PostgreSQL. Returns the generated segment UUID."""
    return await pool.fetchval(
        """INSERT INTO transcript_segments
           (meeting_id, participant_id, start_ms, end_ms, original_text, source_language, confidence)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING id""",
        meeting_id,
        participant_id,
        segment["start_ms"],
        segment["end_ms"],
        segment["text"],
        segment["language"],
        segment.get("confidence", 0.0),
    )


async def request_translation(
    channel: aio_pika.abc.AbstractChannel,
    text: str,
    source_lang: str,
    segment_uuid: uuid.UUID,
    callback_queue_name: str,
):
    """Publish a translation request to RabbitMQ with RPC reply_to."""
    exchange = await channel.declare_exchange(MQ_EXCHANGE, aio_pika.ExchangeType.DIRECT)

    lang_3 = LANG_2_TO_3.get(source_lang, source_lang)
    routing_key = f"{MQ_EXCHANGE}.{lang_3}.est.general"

    body = json.dumps({"text": text, "src": lang_3, "tgt": "est"})

    await exchange.publish(
        aio_pika.Message(
            body=body.encode(),
            content_type="application/json",
            reply_to=callback_queue_name,
            correlation_id=str(segment_uuid),
        ),
        routing_key=routing_key,
    )
    logger.info("Translation requested: %s->est for segment %s", lang_3, segment_uuid)


async def _on_translation_response(message: aio_pika.IncomingMessage):
    """Handle translation response from the NMT worker."""
    async with message.process():
        try:
            data = json.loads(message.body)
            segment_id_str = message.correlation_id
            if not segment_id_str:
                logger.warning("Translation response missing correlation_id, ignoring")
                return

            segment_uuid = uuid.UUID(segment_id_str)

            if data.get("status_code") != 200:
                logger.error(
                    "Translation failed for segment %s: %s",
                    segment_id_str,
                    data.get("status"),
                )
                return

            translated_text = data.get("result", "")
            if not translated_text:
                logger.warning("Empty translation result for segment %s", segment_id_str)
                return

            if db_pool:
                await db_pool.execute(
                    "UPDATE transcript_segments SET translated_text = $1 WHERE id = $2",
                    translated_text,
                    segment_uuid,
                )
                logger.info("Translation stored for segment %s", segment_id_str)
        except Exception:
            logger.exception("Error processing translation response")


async def trigger_summarization(
    pool: asyncpg.Pool, meeting_id: uuid.UUID, chat_messages: list[ChatMessageItem] | None = None
):
    """After meeting ends, fetch full transcript + chat messages and request summary from Gemma."""
    rows = await pool.fetch(
        """SELECT ts.start_ms, ts.original_text, ts.source_language,
                  p.display_name
           FROM transcript_segments ts
           JOIN participants p ON ts.participant_id = p.id
           WHERE ts.meeting_id = $1
           ORDER BY ts.start_ms""",
        meeting_id,
    )

    if not rows:
        logger.warning("No segments for meeting %s, skipping summarization", meeting_id)
        return

    # Build diarized transcript
    lines = []
    current_speaker = None
    for row in rows:
        if row["display_name"] != current_speaker:
            lines.append(f"\n[{row['display_name']}]")
            current_speaker = row["display_name"]
        lines.append(row["original_text"])

    transcript_text = "\n".join(lines).strip()

    # Append chat messages if available
    if chat_messages:
        chat_lines = ["\n\n--- Meeting Chat ---"]
        for msg in chat_messages:
            ts = msg.timestamp[:19] if msg.timestamp else ""
            chat_lines.append(f"[{msg.sender_name}] ({ts}): {msg.text}")
        transcript_text += "\n".join(chat_lines)

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{SUMMARIZER_URL}/summarize",
            json={"transcript": transcript_text},
        )

    if resp.status_code == 200:
        data = resp.json()
        await pool.execute(
            """INSERT INTO summaries (meeting_id, summary_text, model_used, prompt_tokens, completion_tokens)
               VALUES ($1, $2, $3, $4, $5)""",
            meeting_id,
            data["summary"],
            data["model"],
            data["prompt_tokens"],
            data["completion_tokens"],
        )
        logger.info("Summary stored for meeting %s", meeting_id)
    else:
        logger.error("Summarization failed: %s %s", resp.status_code, resp.text[:200])


# --- Application lifecycle ---


async def _connect_postgres() -> asyncpg.Pool:
    """Connect to PostgreSQL with retry."""
    for attempt in range(1, 11):
        try:
            pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
            logger.info("Connected to PostgreSQL")
            return pool
        except Exception:
            logger.warning("PostgreSQL connection attempt %d/10 failed, retrying in 3s...", attempt)
            if attempt == 10:
                logger.exception("Failed to connect to PostgreSQL after 10 attempts")
                raise
            await asyncio.sleep(3)


async def _connect_rabbitmq() -> tuple[aio_pika.abc.AbstractRobustConnection | None, aio_pika.abc.AbstractChannel | None]:
    """Connect to RabbitMQ with retry."""
    for attempt in range(1, 11):
        try:
            connection = await aio_pika.connect_robust(
                host=MQ_HOST, port=MQ_PORT, login=MQ_USERNAME, password=MQ_PASSWORD,
            )
            channel = await connection.channel()
            logger.info("Connected to RabbitMQ")
            return connection, channel
        except Exception:
            logger.warning("RabbitMQ connection attempt %d/10 failed, retrying in 3s...", attempt)
            if attempt == 10:
                logger.warning("Failed to connect to RabbitMQ — translation will be unavailable")
            else:
                await asyncio.sleep(3)
    return None, None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of database and message queue connections."""
    global db_pool, mq_connection, mq_channel, translation_callback_queue

    logger.info("Transcript Assembly Service starting...")
    logger.info("Database: %s", DATABASE_URL.split("@")[1] if "@" in DATABASE_URL else DATABASE_URL)
    logger.info("RabbitMQ: %s:%s", MQ_HOST, MQ_PORT)
    logger.info("Summarizer: %s", SUMMARIZER_URL)

    db_pool = await _connect_postgres()
    mq_connection, mq_channel = await _connect_rabbitmq()

    # Set up translation response callback queue
    if mq_channel:
        callback_queue = await mq_channel.declare_queue("", exclusive=True)
        translation_callback_queue = callback_queue.name
        await callback_queue.consume(_on_translation_response)
        logger.info("Translation callback queue: %s", translation_callback_queue)

    yield

    logger.info("Shutting down...")
    if mq_channel:
        await mq_channel.close()
    if mq_connection:
        await mq_connection.close()
    if db_pool:
        await db_pool.close()
    logger.info("Shutdown complete.")


app = FastAPI(title="Transcript Assembly Service", lifespan=lifespan)


# --- HTTP endpoints ---


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "database": db_pool is not None}


@app.post("/meetings")
async def create_meeting(req: MeetingCreateRequest):
    """Create a meeting record. Called by ingestion on first audio chunk.

    The ingestion service sends a Teams call ID string as meeting_id.
    We store it in teams_call_id and let PostgreSQL generate the UUID PK."""
    if not db_pool:
        raise HTTPException(status_code=503, detail=DB_NOT_AVAILABLE)

    try:
        # Check if meeting already exists
        row = await db_pool.fetchrow(
            SQL_MEETING_BY_CALL_ID, req.meeting_id
        )
        if row:
            internal_id = row["id"]
        else:
            internal_id = await db_pool.fetchval(
                """INSERT INTO meetings (teams_call_id, owner_aad_id, title, started_at)
                   VALUES ($1, $2, $3, NOW())
                   ON CONFLICT (teams_call_id) DO NOTHING
                   RETURNING id""",
                req.meeting_id,
                req.owner_aad_id,
                req.meeting_title,
            )
            if internal_id is None:
                # Race condition: concurrent insert won
                internal_id = await db_pool.fetchval(
                    SQL_MEETING_BY_CALL_ID, req.meeting_id
                )

        _meeting_id_cache[req.meeting_id] = internal_id
        logger.info(
            "Meeting created: teams_call_id=%s -> uuid=%s (%s)",
            req.meeting_id,
            internal_id,
            req.meeting_title,
        )
        return {"ok": True, "meeting_id": str(internal_id)}
    except Exception:
        logger.exception("Error creating meeting %s", req.meeting_id)
        raise HTTPException(status_code=500, detail="Failed to create meeting")


@app.post("/participants")
async def register_participant(req: ParticipantRequest):
    """Register or update a participant in a meeting.

    The ingestion service sends an external participant ID (AAD object ID or MSI).
    We store it in aad_user_id and let PostgreSQL generate the UUID PK."""
    if not db_pool:
        raise HTTPException(status_code=503, detail=DB_NOT_AVAILABLE)

    try:
        internal_meeting_id = await _resolve_meeting_id(req.meeting_id)

        row = await db_pool.fetchrow(
            """INSERT INTO participants (meeting_id, aad_user_id, display_name, email, joined_at)
               VALUES ($1, $2, $3, $4, NOW())
               ON CONFLICT (meeting_id, aad_user_id)
               DO UPDATE SET display_name = EXCLUDED.display_name, email = EXCLUDED.email
               RETURNING id""",
            internal_meeting_id,
            req.participant_id,
            req.display_name,
            req.email,
        )
        internal_participant_id = row["id"]
        _participant_id_cache[(req.meeting_id, req.participant_id)] = internal_participant_id

        logger.info(
            "Participant registered: ext_id=%s -> uuid=%s (%s) in meeting %s",
            req.participant_id,
            internal_participant_id,
            req.display_name,
            req.meeting_id,
        )
        return {"ok": True, "participant_id": str(internal_participant_id)}
    except Exception:
        logger.exception("Error registering participant %s", req.participant_id)
        raise HTTPException(status_code=500, detail="Failed to register participant")


@app.post("/segments")
async def receive_segment(req: SegmentRequest):
    """Store a transcript segment. Called by ingestion when WhisperLiveKit produces output."""
    if not db_pool:
        raise HTTPException(status_code=503, detail=DB_NOT_AVAILABLE)

    try:
        internal_meeting_id = await _resolve_meeting_id(req.meeting_id)
        internal_participant_id = await _resolve_participant_id(req.meeting_id, req.participant_id)

        segment = {
            "start_ms": req.start_ms,
            "end_ms": req.end_ms,
            "text": req.text,
            "language": req.language,
            "confidence": req.confidence,
        }
        segment_uuid = await store_segment(db_pool, internal_meeting_id, internal_participant_id, segment)
        logger.debug(
            "Segment stored: meeting=%s participant=%s [%d-%d] %s",
            req.meeting_id,
            req.participant_id,
            req.start_ms,
            req.end_ms,
            req.text[:80],
        )

        # Request translation if the language qualifies
        if req.language in TRANSLATE_LANGUAGES and mq_channel and translation_callback_queue:
            try:
                await request_translation(
                    mq_channel, req.text, req.language, segment_uuid, translation_callback_queue
                )
            except Exception:
                logger.exception("Error requesting translation for segment")

        return {"ok": True}
    except Exception:
        logger.exception("Error storing segment")
        raise HTTPException(status_code=500, detail="Failed to store segment")


@app.post("/end-meeting")
async def end_meeting(req: EndMeetingRequest):
    """Signal meeting end. Triggers summarization pipeline."""
    if not db_pool:
        raise HTTPException(status_code=503, detail=DB_NOT_AVAILABLE)

    logger.info("End-meeting signal received for %s", req.meeting_id)

    try:
        internal_meeting_id = await _resolve_meeting_id(req.meeting_id)

        # Update meeting record
        await db_pool.execute(
            "UPDATE meetings SET ended_at = NOW(), status = 'ended' WHERE id = $1",
            internal_meeting_id,
        )

        # Trigger summarization in background to avoid blocking the response
        task = asyncio.create_task(_run_summarization(internal_meeting_id, req.chat_messages))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return {"ok": True, "message": f"Summarization triggered for meeting {req.meeting_id}"}
    except Exception:
        logger.exception("Error processing end-meeting for %s", req.meeting_id)
        raise HTTPException(status_code=500, detail="Failed to process end-meeting")


async def _run_summarization(meeting_id: uuid.UUID, chat_messages: list[ChatMessageItem] | None = None):
    """Run summarization in the background."""
    try:
        await trigger_summarization(db_pool, meeting_id, chat_messages)
    except Exception:
        logger.exception("Summarization failed for meeting %s", meeting_id)


class SummarizeNowRequest(BaseModel):
    meeting_id: str
    type: str = "mid"  # "mid" or "final"


@app.post("/summarize-now")
async def summarize_now(req: SummarizeNowRequest):
    """On-demand summary for the bot to post mid-meeting or at meeting end.
    Returns the summary text synchronously (blocks until LLM responds).

    meeting_id can be either a Teams call ID string or internal UUID."""
    if not db_pool:
        raise HTTPException(status_code=503, detail=DB_NOT_AVAILABLE)

    internal_meeting_id = await _resolve_meeting_id(req.meeting_id)

    rows = await db_pool.fetch(
        """SELECT ts.start_ms, ts.original_text, ts.source_language,
                  p.display_name
           FROM transcript_segments ts
           JOIN participants p ON ts.participant_id = p.id
           WHERE ts.meeting_id = $1
           ORDER BY ts.start_ms""",
        internal_meeting_id,
    )

    if not rows:
        return {"summary": ""}

    # Build transcript
    lines = []
    current_speaker = None
    for row in rows:
        if row["display_name"] != current_speaker:
            lines.append(f"\n[{row['display_name']}]")
            current_speaker = row["display_name"]
        lines.append(row["original_text"])
    transcript_text = "\n".join(lines).strip()

    # Adjust prompt based on summary type
    if req.type == "mid":
        prompt_suffix = (
            "\n\nSee on vahearuanne koosoleku keskel. "
            "Koosta lühike kokkuvõte senistest aruteludest (3-5 lauset). "
            "Kasuta sama keelt, milles koosolek toimub."
        )
    else:
        prompt_suffix = ""

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{SUMMARIZER_URL}/summarize",
                json={"transcript": transcript_text + prompt_suffix},
            )
        if resp.status_code == 200:
            return {"summary": resp.json()["summary"]}
        else:
            logger.error("Summarize-now failed: %s", resp.text[:200])
            return {"summary": ""}
    except Exception:
        logger.exception("Summarize-now error for meeting %s", req.meeting_id)
        return {"summary": ""}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
