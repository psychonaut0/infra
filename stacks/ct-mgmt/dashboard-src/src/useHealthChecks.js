import { useState, useEffect } from 'preact/hooks';

export function useHealthChecks(initial = {}) {
  const [status, setStatus] = useState(initial);

  useEffect(() => {
    async function check() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        setStatus(data.health || {});
      } catch {}
    }
    // Only fetch on client after hydration if no initial data was provided
    if (!Object.keys(initial).length) check();
    const id = setInterval(check, 60_000);
    return () => clearInterval(id);
  }, []);

  return status;
}
