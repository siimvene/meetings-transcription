"use client";

import { useIsAuthenticated, useMsal } from "@azure/msal-react";
import { loginRequest } from "@/lib/msal";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useIsAuthenticated();
  const { instance } = useMsal();

  if (!isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
        <h2 className="text-2xl font-semibold text-gray-200">
          Sign in to view your meetings
        </h2>
        <p className="text-gray-400 max-w-md text-center">
          Authenticate with your organization account to access meeting
          transcripts and summaries.
        </p>
        <button
          onClick={() => instance.loginPopup(loginRequest).catch(console.error)}
          className="bg-accent hover:bg-accent-hover text-white px-6 py-3 rounded-lg text-base transition-colors"
        >
          Sign in with Microsoft
        </button>
      </div>
    );
  }

  return <>{children}</>;
}
