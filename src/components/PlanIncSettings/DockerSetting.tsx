import { useCallback, useEffect, useState } from 'react';
import { observer } from 'mobx-react-lite';
import { Button } from '@/components/ui/button';
import { Icon } from '@/components/Common/Iconify/icons';
import { api } from '@/lib/trpc';

type DockerStatus = { available: boolean; version?: string; error?: string };

type DockerContainer = {
  id: string;
  name: string;
  image: string;
  state: string;
  status: string;
};

/**
 * Admin Docker panel (PI-034).
 *
 * Read-only by design (Open Decision 1): it shows whether the daemon answers and
 * the curated container list from `docker.list`. The browser never sees the
 * socket, environment variables or mounts — the server projects them away.
 */
export const DockerSetting = observer(() => {
  const [status, setStatus] = useState<DockerStatus | null>(null);
  const [containers, setContainers] = useState<DockerContainer[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextStatus = (await api.docker.status.query()) as DockerStatus;
      setStatus(nextStatus);
      if (nextStatus.available) {
        setContainers((await api.docker.list.query()) as DockerContainer[]);
      } else {
        setContainers([]);
      }
    } catch (caught) {
      setError((caught as Error).message);
      setStatus(null);
      setContainers([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold">Docker</h3>
          <p className="text-sm text-muted-foreground">
            Host daemon status and containers. Read-only: no lifecycle controls.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <Icon icon="tabler:refresh" width={16} height={16} className="mr-2" />
          Refresh
        </Button>
      </div>

      <div className="flex items-center gap-2 text-sm">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            status?.available ? 'bg-emerald-500' : 'bg-muted-foreground'
          }`}
        />
        {loading
          ? 'Checking the daemon…'
          : status?.available
            ? `Daemon reachable${status.version ? ` · Engine ${status.version}` : ''}`
            : `Daemon unavailable${status?.error ? ` · ${status.error}` : ''}`}
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Image</th>
              <th className="px-3 py-2 font-medium">State</th>
              <th className="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {containers.map((container) => (
              <tr key={container.id}>
                <td className="px-3 py-2">{container.name}</td>
                <td className="px-3 py-2 text-muted-foreground">{container.image}</td>
                <td className="px-3 py-2">{container.state}</td>
                <td className="px-3 py-2 text-muted-foreground">{container.status}</td>
              </tr>
            ))}
            {containers.length === 0 && !loading && (
              <tr>
                <td className="px-3 py-6 text-center text-muted-foreground" colSpan={4}>
                  No containers reported.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
});
