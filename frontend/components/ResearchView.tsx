import {ExternalLink,ShieldCheck,ShieldAlert,Clock3} from "lucide-react";
import type {AppRecord,ResearchRun,Claim} from "../lib/api";
import {formatDate} from "../lib/api";

const groups:Record<string,string>={auth:"Authentication",credentials:"Credential access",api:"API surface",mcp:"MCP & agent use"};
function ClaimRow({claim}:{claim:Claim}){
  return <div className={`claim-row ${claim.status!=="supported"?"muted-claim":""}`}>
    <div className="claim-text"><span className={`claim-dot ${claim.status}`}/>{claim.text}</div>
    <div className="claim-meta"><span>{claim.status}</span>{claim.source_url&&<a href={claim.source_url} target="_blank" rel="noreferrer">Source <ExternalLink size={12}/></a>}</div>
    {claim.status==="supported"&&<blockquote>{claim.evidence}</blockquote>}
    {claim.status!=="supported"&&<p className="claim-reason">{claim.reason}</p>}
  </div>;
}
export default function ResearchView({app,run,focus="overview"}:{app:AppRecord,run:ResearchRun|null|undefined,focus?:string}){
  if(!run) return <div className="empty-result"><Clock3 size={20}/><div><strong>No research run yet</strong><p>The app is in the assignment dataset. Authentication, access, API and MCP findings will appear after a live run.</p></div></div>;
  if(run.status==="queued"||run.status==="running") {const phase=run.stage.startsWith("Extracted")||run.stage.startsWith("Researched")?3:run.stage.startsWith("Verification")?4:2;return <div className="research-progress"><div className="progress-head"><span className="loader"/>Researching {app.name}</div><div className="progress-grid"><span className="step done">01 · Matched in dataset</span><span className={`step ${phase>2?"done":"current"}`}>02 · Search & extract</span><span className={`step ${phase>3?"done":phase===3?"current":""}`}>03 · Four research tracks</span><span className={`step ${phase===4?"current":""}`}>04 · Evidence verification</span></div><p>{run.stage}</p></div>}
  if(run.status==="failed") return <div className="notice error"><ShieldAlert size={18}/><div><strong>Research could not be completed</strong><p>{run.error}</p></div></div>;
  const supported=run.claims.filter(c=>c.status==="supported").length;
  const overview=run.claims.find(c=>c.dimension==="overview"&&c.status==="supported");
  const visibleGroups = focus in groups ? [[focus, groups[focus]]] : Object.entries(groups);
  return <article className="research-report">
    <div className="report-heading"><div><span className="eyebrow">RESEARCH DOSSIER · {app.category}</span><h2>{app.name}</h2>{overview&&<p className="overview-text">{overview.text} {overview.source_url&&<a href={overview.source_url} target="_blank" rel="noreferrer">Source ↗</a>}</p>}<p>Last researched {formatDate(run.completed_at)} · {supported} supported of {run.claims.length} extracted claims</p></div><span className="verified-badge"><ShieldCheck size={15}/> Evidence checked</span></div>
    <div className="assessment"><div><span className="eyebrow">BUILDABILITY ASSESSMENT</span><strong>{run.buildability}</strong></div><p>{run.blocker || "Verified API, authentication and credential access evidence support an integration path. Human review is still advised."}</p></div>
    <div className={`report-grid ${visibleGroups.length===1?"single":""}`}>{visibleGroups.map(([key,title])=><section className="report-section" key={key}><header><span className="section-number">{String(Object.keys(groups).indexOf(key)+1).padStart(2,"0")}</span><h3>{title}</h3></header>{run.claims.filter(c=>c.dimension===key&&c.status==="supported").length ? run.claims.filter(c=>c.dimension===key&&c.status==="supported").map(c=><ClaimRow key={c.id} claim={c}/>) : <p className="unknown">No supported finding from the retrieved documentation.</p>}</section>)}</div>
    {run.claims.some(c=>c.status!=="supported")&&<details className="excluded"><summary>{run.claims.filter(c=>c.status!=="supported").length} claims withheld after verification</summary>{run.claims.filter(c=>c.status!=="supported").map(c=><ClaimRow key={c.id} claim={c}/>)}</details>}
    <section className="source-list"><h3>Retrieved sources <span>{run.sources.length}</span></h3>{run.sources.map(s=><a key={s.id} href={s.url} target="_blank" rel="noreferrer"><span>{s.title||s.url}<small>{s.url}</small></span><ExternalLink size={14}/></a>)}</section>
    {run.error&&<p className="run-warning">Partial pipeline warning: {run.error}</p>}
  </article>;
}
