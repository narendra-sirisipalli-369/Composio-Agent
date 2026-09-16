"use client";
import Link from "next/link";
import {usePathname} from "next/navigation";
import {MessageSquareText,Table2,ChartNoAxesColumn,ShieldCheck,Workflow,ArrowUpRight,DatabaseZap} from "lucide-react";
import type {ReactNode} from "react";

const nav = [
  {href:"/",label:"Research chat",icon:MessageSquareText},
  {href:"/apps",label:"100 applications",icon:Table2},
  {href:"/insights",label:"Insights",icon:ChartNoAxesColumn},
  {href:"/verification",label:"Verification",icon:ShieldCheck},
  {href:"/methodology",label:"Methodology",icon:Workflow},
];
export default function Shell({children}:{children:ReactNode}){
  const path=usePathname();
  return <div className="app-shell">
    <aside className="sidebar">
      <Link href="/" className="brand"><span className="brand-mark"><DatabaseZap size={18}/></span><span>Fieldnote<small>INTEGRATION INTELLIGENCE</small></span></Link>
      <div className="sidebar-label">WORKSPACE</div>
      <nav>{nav.map(({href,label,icon:Icon})=><Link key={href} href={href} className={`nav-link ${path===href?"active":""}`}><Icon size={17}/>{label}</Link>)}</nav>
      <div className="sidebar-bottom"><div className="sidebar-bottom-title">Evidence first</div><p>Every finding is tied to retrieved documentation and a verification result.</p><a href="/case-study.html" target="_blank">View case study <ArrowUpRight size={13}/></a></div>
    </aside>
    <main className="main">{children}</main>
  </div>;
}
