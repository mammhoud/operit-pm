import { describe, expect, test } from 'bun:test';
import { workflowRouter } from '../../../routerTrpc/workflow';
import { dockerRouter } from '../../../routerTrpc/docker';

/**
 * Router surface tests: these pin the procedures the canvas/preset UI calls.
 * Behavioural rules live in `shared/lib/__tests__/workflow.test.ts` and
 * `server/__tests__/unit/lib/{docker,mcpPresets}.test.ts`, where the logic is
 * pure and does not need a live SurrealDB.
 */

describe('workflow router surface', () => {
  const procedures = Object.keys(workflowRouter._def.procedures);

  test('exposes CRUD, validate and run', () => {
    for (const name of ['list', 'get', 'validate', 'upsert', 'delete', 'run']) {
      expect(procedures).toContain(name);
    }
  });
});

describe('docker router surface', () => {
  const procedures = Object.keys(dockerRouter._def.procedures);

  test('exposes read-only status and list', () => {
    expect(procedures.sort()).toEqual(['list', 'status']);
  });

  test('has no lifecycle procedures', () => {
    for (const name of ['start', 'stop', 'restart', 'remove', 'kill', 'exec']) {
      expect(procedures).not.toContain(name);
    }
  });
});
