import type {Metadata} from "next";
import Shell from "../components/Shell";
import "./globals.css";
export const metadata:Metadata={title:"Fieldnote · Integration Research",description:"Evidence-backed research across a defined 100-app integration dataset."};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body><Shell>{children}</Shell></body></html>}
