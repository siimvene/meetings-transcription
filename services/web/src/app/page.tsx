"use client";

import { useEffect, useState } from "react";
import { useMsal, useAccount } from "@azure/msal-react";
import Header from "@/components/Header";
import AuthGuard from "@/components/AuthGuard";
import MeetingCard from "@/components/MeetingCard";
import { listTranscripts, TranscriptListItem } from "@/lib/api";
import { loginRequest } from "@/lib/msal";

function MeetingList() {
  const { instance, accounts } = useMsal();
  const account = useAccount(accounts[0] || {});
  const [meetings, setMeetings] = useState<TranscriptListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!account) return;

    async function fetchMeetings() {
      try {
        const tokenResponse = await instance.acquireTokenSilent({
          ...loginRequest,
          account: account!,
        });
        const all = await listTranscripts(tokenResponse.accessToken);
        // Filter to meetings owned by current user
        const owned = all.filter(
          (m) => !m.owner_aad_id || m.owner_aad_id === account!.localAccountId
        );
        setMeetings(owned);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load meetings");
      } finally {
        setLoading(false);
      }
    }

    fetchMeetings();
  }, [instance, account]);

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <div className="text-gray-400">Loading meetings...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex justify-center py-20">
        <div className="text-red-400">{error}</div>
      </div>
    );
  }

  if (meetings.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <p className="text-gray-400 text-lg">No meetings yet</p>
        <p className="text-gray-500 text-sm">
          Meetings will appear here after the transcription bot joins your call.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      {meetings.map((m) => (
        <MeetingCard key={m.id} meeting={m} />
      ))}
    </div>
  );
}

export default function HomePage() {
  return (
    <>
      <Header />
      <main className="max-w-3xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-bold text-gray-100 mb-6">My Meetings</h1>
        <AuthGuard>
          <MeetingList />
        </AuthGuard>
      </main>
    </>
  );
}
