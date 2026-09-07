import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { requestBasePath } from "@/lib/server/base-path";

export const metadata: Metadata = {
  title: "PegasusAI Studio",
  description: "AI-powered scientific workflow development platform",
};

// Set the theme class before first paint to avoid a flash of the wrong theme.
const noFlashTheme = `(function(){try{var t=JSON.parse(localStorage.getItem('studio-theme')||'{}').state?.theme||'system';var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const basePath = await requestBasePath();
  const baseHref = `${basePath || ""}/`;

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <base href={baseHref} />
        <meta name="studio-base-path" content={basePath} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: noFlashTheme }} />
      </head>
      <body className="antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
