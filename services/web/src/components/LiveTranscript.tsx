"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getWebSocketUrl } from "@/lib/api";

interface LiveSegment {
  text: string;
  speaker?: string;
  start?: string;
  is_final?: boolean;
}

export default function LiveTranscript() {
  const [segments, setSegments] = useState<LiveSegment[]>([]);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const connect = useCallback(() => {
    const url = getWebSocketUrl();
    setStatus("connecting");

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setStatus("connected");

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "config") return;

        const lines = data.lines || [];
        for (const line of lines) {
          const seg: LiveSegment = {
            text: line.text?.trim() || "",
            speaker: line.speaker || undefined,
            start: line.start ? String(line.start) : undefined,
            is_final: line.is_final ?? false,
          };
          if (!seg.text) continue;

          setSegments((prev) => {
            // Replace last non-final segment from same speaker, or append
            if (
              !seg.is_final &&
              prev.length > 0 &&
              !prev[prev.length - 1].is_final &&
              prev[prev.length - 1].speaker === seg.speaker
            ) {
              return [...prev.slice(0, -1), seg];
            }
            return [...prev, seg];
          });
        }
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => setStatus("disconnected");
    ws.onerror = () => setStatus("disconnected");
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [segments]);

  const statusColor =
    status === "connected"
      ? "bg-emerald-500"
      : status === "connecting"
      ? "bg-amber-500"
      : "bg-red-500";

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
        {segments.map((seg, i) => {
          const showSpeaker =
            i === 0 || segments[i - 1]?.speaker !== seg.speaker;
          return (
            <div key={i}>
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
              <p
                className={`text-sm leading-relaxed pl-2 ${
                  seg.is_final ? "text-gray-300" : "text-gray-500 italic"
                }`}
              >
                {seg.text}
              </p>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
