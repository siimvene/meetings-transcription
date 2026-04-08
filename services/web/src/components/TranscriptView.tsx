"use client";

import { useState } from "react";
import type { Segment } from "@/lib/api";

const SPEAKER_COLORS = [
  "text-blue-400",
  "text-emerald-400",
  "text-amber-400",
  "text-rose-400",
  "text-violet-400",
  "text-cyan-400",
  "text-orange-400",
  "text-pink-400",
];

function getSpeakerColor(speaker: string, speakerMap: Map<string, number>): string {
  if (!speakerMap.has(speaker)) {
    speakerMap.set(speaker, speakerMap.size);
  }
  return SPEAKER_COLORS[speakerMap.get(speaker)! % SPEAKER_COLORS.length];
}

export default function TranscriptView({ segments }: { segments: Segment[] }) {
  const [showTranslation, setShowTranslation] = useState(false);
  const speakerMap = new Map<string, number>();

  const hasTranslations = segments.some((s) => s.translated_text);

  return (
    <div className="bg-surface-light border border-gray-700 rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-100">Transcript</h2>
        {hasTranslations && (
          <button
            onClick={() => setShowTranslation(!showTranslation)}
            className={`text-sm px-3 py-1 rounded transition-colors ${
              showTranslation
                ? "bg-accent text-white"
                : "bg-surface text-gray-400 hover:text-gray-200"
            }`}
          >
            {showTranslation ? "Translation ON" : "Translation OFF"}
          </button>
        )}
      </div>

      <div className="space-y-3">
        {segments.map((seg, i) => {
          const speaker = seg.speaker || "Unknown";
          const showSpeaker =
            i === 0 || segments[i - 1]?.speaker !== seg.speaker;
          const colorClass = getSpeakerColor(speaker, speakerMap);

          return (
            <div key={i}>
              {showSpeaker && (
                <div className="flex items-baseline gap-2 mt-4 first:mt-0">
                  <span className={`font-medium text-sm ${colorClass}`}>
                    {speaker}
                  </span>
                  <span className="text-xs text-gray-500">{seg.start}</span>
                </div>
              )}
              <p className="text-gray-300 text-sm leading-relaxed pl-2">
                {showTranslation && seg.translated_text
                  ? seg.translated_text
                  : seg.text}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
