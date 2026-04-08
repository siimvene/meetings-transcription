"use client";

import Link from "next/link";
import type { TranscriptListItem } from "@/lib/api";

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("et-EE", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function MeetingCard({ meeting }: { meeting: TranscriptListItem }) {
  return (
    <Link href={`/meetings/${meeting.id}`}>
      <div className="bg-surface-light hover:bg-surface-lighter border border-gray-700 rounded-lg p-5 transition-colors cursor-pointer">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-base font-medium text-gray-100 truncate">
              {meeting.title}
            </h3>
            <p className="text-sm text-gray-400 mt-1">
              {formatDate(meeting.timestamp)}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {meeting.participant_count != null && (
              <span className="text-xs text-gray-400 bg-surface px-2 py-1 rounded">
                {meeting.participant_count} participants
              </span>
            )}
            {meeting.has_summary && (
              <span className="text-xs text-accent bg-accent/10 px-2 py-1 rounded font-medium">
                Summary
              </span>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}
