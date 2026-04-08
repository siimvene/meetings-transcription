"use client";

import { MsalProvider } from "@azure/msal-react";
import {
  PublicClientApplication,
  EventType,
  EventMessage,
  AuthenticationResult,
} from "@azure/msal-browser";
import { msalConfig } from "@/lib/msal";
import { useEffect, useState } from "react";
import "./globals.css";

let msalInstance: PublicClientApplication | null = null;

function getMsalInstance(): PublicClientApplication {
  if (!msalInstance) {
    msalInstance = new PublicClientApplication(msalConfig);
  }
  return msalInstance;
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [ready, setReady] = useState(false);
  const [instance] = useState(getMsalInstance);

  useEffect(() => {
    instance.initialize().then(() => {
      // Set active account from cache if available
      const accounts = instance.getAllAccounts();
      if (accounts.length > 0) {
        instance.setActiveAccount(accounts[0]);
      }

      instance.addEventCallback((event: EventMessage) => {
        if (
          event.eventType === EventType.LOGIN_SUCCESS &&
          event.payload
        ) {
          const result = event.payload as AuthenticationResult;
          instance.setActiveAccount(result.account);
        }
      });

      setReady(true);
    });
  }, [instance]);

  return (
    <html lang="en" className="dark">
      <body className="min-h-screen">
        {ready ? (
          <MsalProvider instance={instance}>{children}</MsalProvider>
        ) : (
          <div className="flex items-center justify-center min-h-screen">
            <div className="text-gray-400">Loading...</div>
          </div>
        )}
      </body>
    </html>
  );
}
