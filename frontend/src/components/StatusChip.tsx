import type {Status} from '../api/types';
const colors:Record<Status,string>={include:'bg-emerald-100 text-emerald-800',exclude:'bg-red-100 text-red-800',maybe:'bg-amber-100 text-amber-800',conflict:'bg-purple-100 text-purple-800'};
export function StatusChip({status}:{status:Status}){return <span className={`chip capitalize ${colors[status]}`}>{status}</span>}
