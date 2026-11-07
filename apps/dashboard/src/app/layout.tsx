import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { QueryProvider } from "@/components/query-provider";
import "./globals.css";
export const metadata:Metadata={title:"Oberyn — Error monitoring",description:"Find, understand, and fix production errors."};
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>){
 return <ClerkProvider><html lang="en"><body className="min-h-screen bg-[#090b10] text-slate-100 antialiased"><QueryProvider>{children}</QueryProvider></body></html></ClerkProvider>;
}
