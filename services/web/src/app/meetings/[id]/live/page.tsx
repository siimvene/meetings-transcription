"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import AuthGuard from "@/components/AuthGuard";
import LiveTranscript from "@/components/LiveTranscript";

function LiveView() {
  const params = useParams();
  const id = params.id as string;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-100">Live Meeting</h1>
        <Link
          href={`/meetings/${id}`}
          className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          View full transcript &rarr;
        </Link>
      </div>
      <LiveTranscript meetingId={id} />
    </div>
  );
}

export default function LivePage() {
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
          <LiveView />
        </AuthGuard>
      </main>
    </>
  );
}
