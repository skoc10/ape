"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

/** Follows the system preference until the user picks a theme; the choice is kept in localStorage. */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      {children}
    </NextThemesProvider>
  );
}
