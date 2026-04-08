"use client";

import ReactMarkdown from "react-markdown";

export default function SummaryView({ summary }: { summary: string }) {
  if (!summary) return null;

  return (
    <div className="bg-surface-light border border-gray-700 rounded-lg p-6">
      <h2 className="text-lg font-semibold text-gray-100 mb-4">Summary</h2>
      <div className="prose prose-invert prose-sm max-w-none text-gray-300">
        <ReactMarkdown>{summary}</ReactMarkdown>
      </div>
    </div>
  );
}
