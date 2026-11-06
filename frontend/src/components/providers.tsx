"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // End-of-day data: no need for aggressive refetching.
            staleTime: 60_000,
            refetchOnWindowFocus: false,
            retry: (count, err) => count < 2 && !(err && typeof err === "object" && "status" in err && [401, 403, 404, 422].includes((err as { status: number }).status)),
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
