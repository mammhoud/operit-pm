/**
 * Docker Engine client (PI-034, Phase 1 of the workflow-and-integrations plan).
 *
 * This is the **only** module that knows the Docker daemon exists. Everything
 * that leaves it is projected to a handful of curated fields — never `inspect`
 * payloads, environment variables, mounts or network settings (plan Review
 * Focus 4). The router on top is `superAdminAuthMiddleware`-gated and the socket
 * is never proxied to a client.
 *
 * The engine is injectable so the projection can be tested without a daemon.
 */
import http from 'node:http';

export type DockerStatus = {
  available: boolean;
  version?: string;
  error?: string;
};

export type DockerContainer = {
  id: string;
  name: string;
  image: string;
  state: string;
  status: string;
};

export type DockerEngineResponse = {
  status: number;
  body: string;
};

export interface DockerEngine {
  request(path: string): Promise<DockerEngineResponse>;
}

export const DEFAULT_DOCKER_SOCKET = process.env.DOCKER_SOCKET || '/var/run/docker.sock';

/** Docker Engine API over the unix socket. GET only — this client is read-only. */
export function createSocketEngine(socketPath: string = DEFAULT_DOCKER_SOCKET): DockerEngine {
  return {
    request(path: string): Promise<DockerEngineResponse> {
      return new Promise((resolve, reject) => {
        const request = http.request(
          { socketPath, path, method: 'GET', timeout: 2_000 },
          (response) => {
            // Collect decoded text: the Docker API always answers with JSON, and
            // this avoids `Buffer` typing differences between bun-types and
            // @types/node.
            const chunks: string[] = [];
            response.setEncoding('utf8');
            response.on('data', (chunk: string) => chunks.push(chunk));
            response.on('end', () =>
              resolve({
                status: response.statusCode ?? 0,
                body: chunks.join(''),
              })
            );
            response.on('error', reject);
          }
        );
        request.on('timeout', () => {
          request.destroy(new Error('docker socket timed out'));
        });
        request.on('error', reject);
        request.end();
      });
    },
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Project one `/containers/json` entry. Anything not listed here is dropped on
 * purpose, so a new Docker field can never leak through by accident.
 */
export function projectContainer(raw: unknown): DockerContainer | null {
  const record = asRecord(raw);
  if (!record) return null;

  const names = Array.isArray(record.Names) ? record.Names : [];
  const firstName = typeof names[0] === 'string' ? (names[0] as string).replace(/^\//, '') : '';
  const id = typeof record.Id === 'string' ? record.Id : '';
  if (!id) return null;

  return {
    id,
    name: firstName || id.slice(0, 12),
    image: typeof record.Image === 'string' ? record.Image : '',
    state: typeof record.State === 'string' ? record.State : '',
    status: typeof record.Status === 'string' ? record.Status : '',
  };
}

export function parseJsonBody(body: string): unknown {
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

export function createDockerClient(engine: DockerEngine) {
  /** Daemon reachability. A missing socket is a normal state, not an error. */
  async function getStatus(): Promise<DockerStatus> {
    try {
      const response = await engine.request('/version');
      if (response.status < 200 || response.status >= 300) {
        return { available: false, error: `docker responded ${response.status}` };
      }
      const payload = asRecord(parseJsonBody(response.body));
      const version = typeof payload?.Version === 'string' ? (payload.Version as string) : undefined;
      return { available: true, ...(version ? { version } : {}) };
    } catch (error) {
      return { available: false, error: (error as Error)?.message || 'docker is not reachable' };
    }
  }

  async function listContainers(): Promise<DockerContainer[]> {
    const response = await engine.request('/containers/json?all=1');
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`docker responded ${response.status}`);
    }
    const payload = parseJsonBody(response.body);
    if (!Array.isArray(payload)) return [];
    return payload
      .map(projectContainer)
      .filter((container): container is DockerContainer => container !== null);
  }

  return { getStatus, listContainers };
}

const defaultClient = createDockerClient(createSocketEngine());

export const getDockerStatus = defaultClient.getStatus;
export const listContainers = defaultClient.listContainers;
