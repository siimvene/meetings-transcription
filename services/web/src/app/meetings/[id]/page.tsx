"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useMsal, useAccount } from "@azure/msal-react";
import Link from "next/link";
import Header from "@/components/Header";
import AuthGuard from "@/components/AuthGuard";
import SummaryView from "@/components/SummaryView";
import TranscriptView from "@/components/TranscriptView";
import { getTranscript, Transcript } from "@/lib/api";
import { loginRequest } from "@/lib/msal";

function MeetingDetail() {
  const params = useParams();
  const id = params.id as string;
  const { instance, accounts } = useMsal();
  const account = useAccount(accounts[0] || {});
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!account || !id) return;

    async function fetchTranscript() {
      try {
        const tokenResponse = await instance.acquireTokenSilent({
          ...loginRequest,
          account: account!,
        });
        const data = await getTranscript(id, tokenResponse.accessToken);
        setTranscript(data);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load transcript"
        );
      } finally {
        setLoading(false);
      }
    }

    fetchTranscript();
  }, [instance, account, id]);

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <div className="text-gray-400">Loading transcript...</div>
      </div>
    );
  }

  if (error || !transcript) {
    return (
      <div className="flex justify-center py-20">
        <div className="text-red-400">{error || "Transcript not found"}</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-100">
            {transcript.title}
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            {new Date(transcript.timestamp).toLocaleString("et-EE")}
            {transcript.language && (
              <span className="ml-3 text-gray-500">
                Language: {transcript.language}
              </span>
            )}
          </p>
        </div>
        <Link
          href={`/meetings/${id}/live`}
          className="bg-accent hover:bg-accent-hover text-white px-4 py-2 rounded text-sm transition-colors"
        >
          Live View
        </Link>
      </div>

      {transcript.summary && <SummaryView summary={transcript.summary} />}

      {transcript.segments && transcript.segments.length > 0 && (
        <TranscriptView segments={transcript.segments} />
      )}
    </div>
  );
}

export default function MeetingPage() {
  return (
    <>
      <Header />
      <main className="max-w-4xl mx-auto px-4 py-8">
        <Link
          href="/"
          className="text-sm text-gray-400 hover:text-gray-200 transition-colors mb-4 inline-block"
        >
          &larr; Back to meetings
        </Link>
        <AuthGuard>
          <MeetingDetail />
        </AuthGuard>
      </main>
    </>
  );
}
