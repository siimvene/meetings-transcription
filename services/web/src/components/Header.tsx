"use client";

import { useMsal, useAccount } from "@azure/msal-react";
import { loginRequest } from "@/lib/msal";
import Link from "next/link";

export default function Header() {
  const { instance, accounts } = useMsal();
  const account = useAccount(accounts[0] || {});

  const handleLogin = () => {
    instance.loginPopup(loginRequest).catch(console.error);
  };

  const handleLogout = () => {
    instance.logoutPopup().catch(console.error);
  };

  return (
    <header className="bg-surface-light border-b border-gray-700 px-6 py-3 flex items-center justify-between">
      <Link href="/" className="text-lg font-semibold text-gray-100 hover:text-accent transition-colors">
        Meetings Transcription
      </Link>

      <div className="flex items-center gap-4">
        {account ? (
          <>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-accent flex items-center justify-center text-sm font-bold text-white">
                {account.name?.charAt(0).toUpperCase() || "?"}
              </div>
              <span className="text-sm text-gray-300">{account.name}</span>
            </div>
            <button
              onClick={handleLogout}
              className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
            >
              Sign out
            </button>
          </>
        ) : (
          <button
            onClick={handleLogin}
            className="bg-accent hover:bg-accent-hover text-white px-4 py-2 rounded text-sm transition-colors"
          >
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}
