import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BDC Loan Marks — SEC portfolio explorer",
  description: "Search BDC loan holdings, compare fair-value marks across holders, and analyze pricing by issuer, industry, and maturity.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
