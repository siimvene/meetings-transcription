"""Audio Ingestion Service — receives per-participant PCM streams from the Teams bot via gRPC
and forwards them to WhisperLiveKit via WebSocket."""

import asyncio
import json
import logging
import os
import signal
import grpc
import httpx
import websockets

import audio_ingestion_pb2
import audio_ingestion_pb2_grpc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TRANSCRIPTION_WS_URL = os.environ.get("TRANSCRIPTION_URL", "ws://transcription:8000/asr")
ASSEMBLY_URL = os.environ.get("ASSEMBLY_URL", "http://assembly:8080")
GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "10"))
WS_CONNECT_TIMEOUT = int(os.environ.get("WS_CONNECT_TIMEOUT", "10"))
WS_RECONNECT_DELAY = int(os.environ.get("WS_RECONNECT_DELAY", "2"))
WS_MAX_RECONNECTS = int(os.environ.get("WS_MAX_RECONNECTS", "5"))


class AudioSession:
    """Manages a single participant's audio stream and WhisperLiveKit WebSocket."""

    def __init__(self, meeting_id: str, participant_id: str, display_name: str):
        self.meeting_id = meeting_id
        self.participant_id = participant_id
        self.display_name = display_name
        self.ws = None
        self.chunks_received = 0
        self._receive_task = None
        self._reconnect_count = 0

    async def connect(self):
        """Establish WebSocket connection to WhisperLiveKit."""
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(TRANSCRIPTION_WS_URL),
                timeout=WS_CONNECT_TIMEOUT,
            )
            # Send initial configuration for WhisperLiveKit
            config = {
                "uid": f"{self.meeting_id}_{self.participant_id}",
                "language": None,  # Auto-detect
                "task": "transcribe",
                "use_vad": True,
            }
            await self.ws.send(json.dumps(config))
            logger.info(
                "WebSocket connected for participant %s (%s) in meeting %s",
                self.participant_id,
                self.display_name,
                self.meeting_id,
            )
            # Start background task to receive transcription results
            self._receive_task = asyncio.create_task(self._receive_transcriptions())
        except Exception:
            logger.exception(
                "Failed to connect WebSocket for participant %s in meeting %s",
                self.participant_id,
                self.meeting_id,
            )
            raise

    async def _receive_transcriptions(self):
        """Receive transcription segments from WhisperLiveKit and forward to assembly."""
        try:
            async for message in self.ws:
                try:
                    segments = json.loads(message)
                    if not isinstance(segments, list):
                        segments = [segments]

                    for segment in segments:
                        if not segment.get("text", "").strip():
                            continue

                        payload = {
                            "meeting_id": self.meeting_id,
                            "participant_id": self.participant_id,
                            "start_ms": int(segment.get("start", 0) * 1000),
                            "end_ms": int(segment.get("end", 0) * 1000),
                            "text": segment["text"].strip(),
                            "language": segment.get("language", "unknown"),
                            "confidence": segment.get("confidence", 0.0),
                        }

                        try:
                            async with httpx.AsyncClient(timeout=10) as client:
                                resp = await client.post(
                                    f"{ASSEMBLY_URL}/segments",
                                    json=payload,
                                )
                                if resp.status_code != 200:
                                    logger.error(
                                        "Failed to forward segment to assembly: %s %s",
                                        resp.status_code,
                                        resp.text[:200],
                                    )
                        except httpx.HTTPError:
                            logger.exception("Error forwarding segment to assembly service")

                except json.JSONDecodeError:
                    logger.warning("Non-JSON message from WhisperLiveKit: %s", message[:100])
        except websockets.exceptions.ConnectionClosed:
            logger.info(
                "WebSocket closed for participant %s in meeting %s",
                self.participant_id,
                self.meeting_id,
            )
        except Exception:
            logger.exception(
                "Error in transcription receiver for participant %s",
                self.participant_id,
            )

    async def send_audio(self, pcm_data: bytes):
        """Forward PCM audio data to WhisperLiveKit."""
        if not self.ws or self.ws.closed:
            if self._reconnect_count < WS_MAX_RECONNECTS:
                self._reconnect_count += 1
                logger.warning(
                    "WebSocket closed, attempting reconnect %d/%d for participant %s",
                    self._reconnect_count,
                    WS_MAX_RECONNECTS,
                    self.participant_id,
                )
                await asyncio.sleep(WS_RECONNECT_DELAY)
                await self.connect()
            else:
                logger.error(
                    "Max reconnects reached for participant %s, dropping audio",
                    self.participant_id,
                )
                return

        try:
            await self.ws.send(pcm_data)
            self.chunks_received += 1
        except websockets.exceptions.ConnectionClosed:
            logger.warning(
                "WebSocket send failed (connection closed) for participant %s",
                self.participant_id,
            )

    async def close(self):
        """Close the WebSocket connection and cancel the receive task."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self.ws and not self.ws.closed:
            try:
                # Send empty bytes to signal end of stream
                await self.ws.send(b"")
                await self.ws.close()
            except Exception:
                logger.exception("Error closing WebSocket for participant %s", self.participant_id)

        logger.info(
            "Session closed for participant %s (%s) — %d chunks processed",
            self.participant_id,
            self.display_name,
            self.chunks_received,
        )


class MeetingState:
    """Tracks the state of an active meeting."""

    def __init__(self, meeting_id: str, owner_aad_id: str, meeting_title: str = ""):
        self.meeting_id = meeting_id
        self.owner_aad_id = owner_aad_id
        self.meeting_title = meeting_title
        self.participants: dict[str, str] = {}  # participant_id -> display_name
        self.sessions: dict[str, AudioSession] = {}  # participant_id -> AudioSession
        self.created = False


# Active meetings: meeting_id -> MeetingState
meetings: dict[str, MeetingState] = {}
# Lock for meeting state mutations
_meetings_lock = asyncio.Lock()


async def _ensure_meeting_created(meeting: MeetingState, chunk: audio_ingestion_pb2.AudioChunk):
    """Create meeting record on first chunk via assembly service."""
    if meeting.created:
        return

    payload = {
        "meeting_id": chunk.meeting_id,
        "owner_aad_id": chunk.owner_aad_id,
        "meeting_title": chunk.meeting_title or "Untitled Meeting",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ASSEMBLY_URL}/meetings", json=payload)
            if resp.status_code == 200:
                meeting.created = True
                logger.info("Meeting record created: %s", chunk.meeting_id)
            else:
                logger.error(
                    "Failed to create meeting record: %s %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except httpx.HTTPError:
        logger.exception("Error creating meeting record for %s", chunk.meeting_id)


async def _update_participant_roster(meeting: MeetingState, chunk: audio_ingestion_pb2.AudioChunk):
    """Register or update participant in the roster."""
    pid = chunk.participant_id
    if pid not in meeting.participants or meeting.participants[pid] != chunk.display_name:
        meeting.participants[pid] = chunk.display_name

        payload = {
            "meeting_id": chunk.meeting_id,
            "participant_id": pid,
            "display_name": chunk.display_name,
            "email": chunk.email or "",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{ASSEMBLY_URL}/participants", json=payload)
                logger.info(
                    "Participant registered: %s (%s) in meeting %s",
                    pid,
                    chunk.display_name,
                    chunk.meeting_id,
                )
        except httpx.HTTPError:
            logger.exception("Error registering participant %s", pid)


class AudioIngestionServicer(audio_ingestion_pb2_grpc.AudioIngestionServicer):
    """gRPC service implementation for AudioIngestion."""

    async def StreamAudio(self, request_iterator, context):
        """Receive per-participant audio chunks and forward to WhisperLiveKit."""
        meeting_id = None
        participant_id = None
        chunks_received = 0
        session = None

        try:
            async for chunk in request_iterator:
                meeting_id = chunk.meeting_id
                participant_id = chunk.participant_id

                async with _meetings_lock:
                    # Get or create meeting state
                    if meeting_id not in meetings:
                        meetings[meeting_id] = MeetingState(
                            meeting_id=meeting_id,
                            owner_aad_id=chunk.owner_aad_id,
                            meeting_title=chunk.meeting_title,
                        )
                    meeting = meetings[meeting_id]

                # Create meeting record on first chunk
                await _ensure_meeting_created(meeting, chunk)

                # Update participant roster
                await _update_participant_roster(meeting, chunk)

                # Log room device for Sortformer diarization
                if chunk.is_room_device:
                    logger.info(
                        "Room device detected: participant %s (%s) in meeting %s — "
                        "Sortformer diarization needed for shared microphone",
                        participant_id,
                        chunk.display_name,
                        meeting_id,
                    )

                # Get or create audio session
                if participant_id not in meeting.sessions:
                    session = AudioSession(meeting_id, participant_id, chunk.display_name)
                    try:
                        await session.connect()
                    except Exception:
                        logger.exception(
                            "Cannot establish WebSocket for participant %s, aborting stream",
                            participant_id,
                        )
                        return audio_ingestion_pb2.StreamResult(
                            ok=False,
                            message=f"WebSocket connection failed for participant {participant_id}",
                            chunks_received=chunks_received,
                        )
                    meeting.sessions[participant_id] = session
                else:
                    session = meeting.sessions[participant_id]

                # Forward PCM audio to WhisperLiveKit
                if chunk.pcm_data:
                    await session.send_audio(chunk.pcm_data)
                    chunks_received += 1

            # Stream ended normally — close the participant's session
            if session:
                await session.close()
                async with _meetings_lock:
                    if meeting_id in meetings and participant_id in meetings[meeting_id].sessions:
                        del meetings[meeting_id].sessions[participant_id]

            logger.info(
                "StreamAudio completed for participant %s in meeting %s — %d chunks",
                participant_id,
                meeting_id,
                chunks_received,
            )

            return audio_ingestion_pb2.StreamResult(
                ok=True,
                message=f"Stream completed for participant {participant_id}",
                chunks_received=chunks_received,
            )

        except Exception:
            logger.exception(
                "Error in StreamAudio for participant %s in meeting %s",
                participant_id,
                meeting_id,
            )
            # Clean up session on error
            if session:
                await session.close()
                async with _meetings_lock:
                    if meeting_id in meetings and participant_id in meetings[meeting_id].sessions:
                        del meetings[meeting_id].sessions[participant_id]

            return audio_ingestion_pb2.StreamResult(
                ok=False,
                message=f"Stream error for participant {participant_id}",
                chunks_received=chunks_received,
            )

    async def EndMeeting(self, request, context):
        """Signal that a meeting has ended. Close all sessions and trigger summarization."""
        meeting_id = request.meeting_id
        owner_aad_id = request.owner_aad_id

        logger.info("EndMeeting received for meeting %s (owner: %s)", meeting_id, owner_aad_id)

        async with _meetings_lock:
            meeting = meetings.pop(meeting_id, None)

        if not meeting:
            logger.warning("EndMeeting called for unknown meeting %s", meeting_id)
            return audio_ingestion_pb2.EndMeetingResult(
                ok=True,
                message=f"Meeting {meeting_id} not found (may have already ended)",
            )

        # Close all active participant sessions
        close_errors = []
        for pid, session in meeting.sessions.items():
            try:
                await session.close()
                logger.info("Closed session for participant %s in meeting %s", pid, meeting_id)
            except Exception:
                logger.exception("Error closing session for participant %s", pid)
                close_errors.append(pid)

        # Notify assembly service to trigger summarization
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{ASSEMBLY_URL}/end-meeting",
                    json={
                        "meeting_id": meeting_id,
                        "owner_aad_id": owner_aad_id,
                    },
                )
                if resp.status_code == 200:
                    logger.info("Summarization triggered for meeting %s", meeting_id)
                else:
                    logger.error(
                        "Failed to trigger summarization for meeting %s: %s %s",
                        meeting_id,
                        resp.status_code,
                        resp.text[:200],
                    )
        except httpx.HTTPError:
            logger.exception("Error notifying assembly service for meeting %s", meeting_id)

        participant_count = len(meeting.participants)
        msg = f"Meeting {meeting_id} ended — {participant_count} participants"
        if close_errors:
            msg += f" ({len(close_errors)} session close errors)"

        return audio_ingestion_pb2.EndMeetingResult(ok=True, message=msg)


async def serve():
    """Start the async gRPC server."""
    server = grpc.aio.server(
        options=[
            ("grpc.max_receive_message_length", 64 * 1024 * 1024),  # 64 MB
            ("grpc.keepalive_time_ms", 30000),
            ("grpc.keepalive_timeout_ms", 10000),
            ("grpc.keepalive_permit_without_calls", True),
        ],
    )
    audio_ingestion_pb2_grpc.add_AudioIngestionServicer_to_server(
        AudioIngestionServicer(), server
    )
    listen_addr = f"[::]:{GRPC_PORT}"
    server.add_insecure_port(listen_addr)

    logger.info("Audio Ingestion Service starting on %s", listen_addr)
    logger.info("WhisperLiveKit target: %s", TRANSCRIPTION_WS_URL)
    logger.info("Assembly service target: %s", ASSEMBLY_URL)

    await server.start()

    # Handle graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await shutdown_event.wait()

    logger.info("Shutting down gRPC server...")

    # Close all active sessions
    async with _meetings_lock:
        for meeting_id, meeting in meetings.items():
            for pid, session in meeting.sessions.items():
                try:
                    await session.close()
                except Exception:
                    logger.exception(
                        "Error closing session during shutdown: %s/%s", meeting_id, pid
                    )
        meetings.clear()

    await server.stop(grace=5)
    logger.info("Server stopped.")


if __name__ == "__main__":
    asyncio.run(serve())
