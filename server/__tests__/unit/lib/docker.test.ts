import { describe, expect, test } from 'bun:test';
import {
  createDockerClient,
  projectContainer,
  type DockerEngine,
  type DockerEngineResponse,
} from '../../../lib/docker';

const rawContainer = {
  Id: '9d1f0c',
  Names: ['/planinc'],
  Image: 'planinc:latest',
  State: 'running',
  Status: 'Up 2 hours',
  // Everything below must never leave the server.
  Env: ['NEXTAUTH_SECRET=super-secret'],
  Mounts: [{ Destination: '/data', Source: '/srv/planinc/data' }],
  NetworkSettings: { Networks: { coolify: {} } },
  HostConfig: { Binds: ['/var/run/docker.sock:/var/run/docker.sock'] },
  Labels: { owner: 'internal' },
};

function fakeEngine(response: DockerEngineResponse | Error): DockerEngine {
  return {
    request: async () => {
      if (response instanceof Error) throw response;
      return response;
    },
  };
}

describe('projectContainer', () => {
  test('keeps exactly the curated fields', () => {
    const projected = projectContainer(rawContainer);

    expect(projected).toEqual({
      id: '9d1f0c',
      name: 'planinc',
      image: 'planinc:latest',
      state: 'running',
      status: 'Up 2 hours',
    });

    // Anything else — env secrets, mounts, socket binds — is dropped.
    const serialized = JSON.stringify(projected);
    expect(serialized).not.toContain('super-secret');
    expect(serialized).not.toContain('docker.sock');
    expect(serialized).not.toContain('/srv/planinc');
  });

  test('falls back to the short id when a container has no name', () => {
    const projected = projectContainer({ Id: 'abcdef1234567890', Names: [], Image: 'x', State: 'exited' });

    expect(projected?.name).toBe('abcdef123456');
  });

  test('ignores malformed entries', () => {
    expect(projectContainer(null)).toBeNull();
    expect(projectContainer('nope')).toBeNull();
    expect(projectContainer({ Names: ['/no-id'] })).toBeNull();
  });
});

describe('listContainers', () => {
  test('projects every engine entry', async () => {
    const client = createDockerClient(
      fakeEngine({ status: 200, body: JSON.stringify([rawContainer, { ...rawContainer, Id: 'b', Names: ['/db'] }]) })
    );

    const containers = await client.listContainers();

    expect(containers).toHaveLength(2);
    expect(containers[1]).toEqual({ id: 'b', name: 'db', image: 'planinc:latest', state: 'running', status: 'Up 2 hours' });
  });

  test('returns an empty list for a non-array body', async () => {
    const client = createDockerClient(fakeEngine({ status: 200, body: '{"message":"ok"}' }));

    expect(await client.listContainers()).toEqual([]);
  });

  test('throws on an engine error status', async () => {
    const client = createDockerClient(fakeEngine({ status: 500, body: 'boom' }));

    await expect(client.listContainers()).rejects.toThrow('docker responded 500');
  });
});

describe('getStatus', () => {
  test('reports the daemon version', async () => {
    const client = createDockerClient(
      fakeEngine({ status: 200, body: JSON.stringify({ Version: '27.1.0', ApiVersion: '1.46' }) })
    );

    expect(await client.getStatus()).toEqual({ available: true, version: '27.1.0' });
  });

  test('never throws when the socket is absent', async () => {
    const client = createDockerClient(fakeEngine(new Error('connect ENOENT /var/run/docker.sock')));

    expect(await client.getStatus()).toEqual({
      available: false,
      error: 'connect ENOENT /var/run/docker.sock',
    });
  });

  test('reports an error status without throwing', async () => {
    const client = createDockerClient(fakeEngine({ status: 403, body: 'permission denied' }));

    expect(await client.getStatus()).toEqual({ available: false, error: 'docker responded 403' });
  });
});
