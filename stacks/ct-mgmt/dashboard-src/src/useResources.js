import { useState, useEffect } from 'preact/hooks';

const EMPTY = { nodes: [], storage: [] };

export function useResources(initial = EMPTY) {
  const [data, setData] = useState(initial);

  useEffect(() => {
    async function poll() {
      try {
        const res = await fetch('/api/status');
        const body = await res.json();
        setData(body.resources || EMPTY);
      } catch {}
    }
    if (!initial.nodes?.length) poll();
    const id = setInterval(poll, 30_000);
    return () => clearInterval(id);
  }, []);

  return data;
}
