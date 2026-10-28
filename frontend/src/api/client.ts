const BASE=import.meta.env.VITE_API_URL ?? '/api';
export class ApiError extends Error{constructor(public status:number,message:string){super(message)}}
export async function api<T>(path:string,init?:RequestInit):Promise<T>{const r=await fetch(`${BASE}${path}`,{...init,headers:{'Content-Type':'application/json',...init?.headers}});if(!r.ok){let message=`Request failed (${r.status})`;try{const b=await r.json();message=b.detail??message}catch{}throw new ApiError(r.status,message)}if(r.status===204)return undefined as T;return r.json()}
export const qs=(values:Record<string,unknown>)=>{const p=new URLSearchParams();Object.entries(values).forEach(([k,v])=>{if(v!==''&&v!==undefined&&v!==null)p.set(k,String(v))});return `?${p}`};
export const reviewer=()=>{let name=localStorage.getItem('mini-rayyan-reviewer');if(!name){name=window.prompt('Your reviewer name')?.trim()||'reviewer';localStorage.setItem('mini-rayyan-reviewer',name)}return name};
