export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type AppRecord = {id:number;name:string;category:string;hint:string;research_status?:string;buildability?:string|null;findings?:Record<string,{label:string;source_url:string|null}|null>;run?:ResearchRun|null};
export type Claim = {id:number;dimension:string;tag:string;text:string;evidence:string;status:string;reason:string;source_url:string|null};
export type ResearchRun = {id:number;app_id:number;status:string;stage:string;started_at:string;completed_at:string|null;error:string|null;buildability:string;blocker:string|null;sources:{id:number;url:string;title:string;retrieved_at:string}[];claims:Claim[]};
export type ChatResponse = {session_id:string;type:string;message:string;focus?:string;app?:AppRecord;run?:ResearchRun|null};
export async function get<T>(path:string):Promise<T> { const r=await fetch(`${API}${path}`,{cache:"no-store"}); if(!r.ok) throw new Error(await r.text()); return r.json(); }
export async function post<T>(path:string,data:unknown):Promise<T> { const r=await fetch(`${API}${path}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)}); if(!r.ok) throw new Error(await r.text()); return r.json(); }
export const formatDate = (value:string|null|undefined) => value ? new Date(value).toLocaleString() : "—";
