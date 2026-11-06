export type Organization={id:string;name:string;slug:string;role:string};
export type Project={id:string;name:string;slug:string;platform:string|null;created_at:string};
export type Issue={id:string;title:string;status:string;first_seen:string;last_seen:string;event_count:number;user_count:number};
export type Page<T>={data:T[];next_cursor:string|null};
const API_URL=process.env.NEXT_PUBLIC_API_URL??"http://localhost:8000";
export async function api<T>(path:string,token:string,init?:RequestInit):Promise<T>{
 const response=await fetch(API_URL+path,{...init,headers:{"Content-Type":"application/json",Authorization:"Bearer "+token,...init?.headers},cache:"no-store"});
 if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail??("Request failed ("+response.status+")"));}
 return response.json() as Promise<T>;
}
