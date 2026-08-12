import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "MERIDIAN Control",
  description: "Durable control plane for MERIDIAN research jobs."
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
