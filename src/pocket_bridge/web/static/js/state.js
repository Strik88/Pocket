// App state from /api/state, with polling while a sync or a Claude job runs.
import { api } from "./core.js";

export const S = { state: null };

export async function refresh() {
  S.state = await api("/api/state");
  window.dispatchEvent(new CustomEvent("pb:state", { detail: S.state }));
  return S.state;
}

let timer = null;
/** Poll every second while something runs in the background; stops by itself. */
export function watch() {
  if (timer) return;
  const tick = async () => {
    try {
      const st = await refresh();
      if (st.sync_running || st.job_running || st.discover?.running) {
        timer = setTimeout(tick, 1000);
        return;
      }
    } catch { /* app stopped: try again later */ }
    timer = null;
    window.dispatchEvent(new CustomEvent("pb:idle"));
  };
  timer = setTimeout(tick, 600);
}

export const busy = () => !!(S.state?.sync_running || S.state?.job_running || S.state?.discover?.running);
