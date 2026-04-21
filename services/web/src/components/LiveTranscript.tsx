"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useMsal, useAccount } from "@azure/msal-react";
import { getLiveWebSocketUrl } from "@/lib/api";
import { loginRequest } from "@/lib/msal";

interface LiveSegment {
  text: string;
  translated_text?: string;
  speaker?: string;
  start?: string;
}

interface LiveTranscriptProps {
  meetingId: string;
}

export default function LiveTranscript({ meetingId }: LiveTranscriptProps) {
  const [segments, setSegments] = useState<LiveSegment[]>([]);
  const [status, setStatus] = useState<
    "connecting" | "connected" | "disconnected" | "ended"
  >("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const { instance, accounts } = useMsal();
  const account = useAccount(accounts[0] || {});

  const connect = useCallback(async () => {
    if (!account) return;

    setStatus("connecting");

    try {
      const tokenResponse = await instance.acquireTokenSilent({
        ...loginRequest,
        account,
      });

      const url = getLiveWebSocketUrl(meetingId, tokenResponse.accessToken);
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setStatus("connected");

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === "meeting_ended") {
            setStatus("ended");
            return;
          }

          if (data.type === "segment") {
            const seg: LiveSegment = {
              text: data.text || "",
              translated_text: data.translated_text || undefined,
              speaker: data.speaker || undefined,
              start: data.start || undefined,
            };
            if (seg.text) {
              setSegments((prev) => [...prev, seg]);
            }
          }
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onclose = () => {
        setStatus((prev) => (prev === "ended" ? "ended" : "disconnected"));
      };
      ws.onerror = () => setStatus("disconnected");
    } catch {
      setStatus("disconnected");
    }
  }, [account, instance, meetingId]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [segments]);

  const statusColors: Record<string, string> = {
    connected: "bg-emerald-500",
    connecting: "bg-amber-500",
    ended: "bg-blue-500",
    disconnected: "bg-red-500",
  };
  const statusColor = statusColors[status];

  return (
    <div className="bg-surface-light border border-gray-700 rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-100">Live Transcript</h2>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${statusColor}`} />
          <span className="text-xs text-gray-400 capitalize">{status}</span>
          {status === "disconnected" && (
            <button
              onClick={connect}
              className="text-xs text-accent hover:text-accent-hover ml-2"
            >
              Reconnect
            </button>
          )}
        </div>
      </div>

      <div className="space-y-2 max-h-[70vh] overflow-y-auto">
        {segments.length === 0 && status === "connected" && (
          <p className="text-gray-500 text-sm italic">
            Waiting for speech...
          </p>
        )}
        {status === "ended" && segments.length === 0 && (
          <p className="text-gray-500 text-sm italic">
            Meeting has ended. No transcript segments were recorded.
          </p>
        )}
        {segments.map((seg, i) => {
          const showSpeaker =
            i === 0 || segments[i - 1]?.speaker !== seg.speaker;
          return (
            <div key={`${seg.start}-${seg.speaker}-${i}`}>
              {showSpeaker && seg.speaker && (
                <div className="flex items-baseline gap-2 mt-3 first:mt-0">
                  <span className="font-medium text-sm text-accent">
                    {seg.speaker}
                  </span>
                  {seg.start && (
                    <span className="text-xs text-gray-500">{seg.start}</span>
                  )}
                </div>
              )}
              <p className="text-sm leading-relaxed pl-2 text-gray-300">
                {seg.text}
              </p>
              {seg.translated_text && (
                <p className="text-sm leading-relaxed pl-2 text-gray-500 italic">
                  {seg.translated_text}
                </p>
              )}
            </div>
          );
        })}
        {status === "ended" && segments.length > 0 && (
          <p className="text-gray-500 text-sm italic mt-4 pt-4 border-t border-gray-700">
            Meeting ended
          </p>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
