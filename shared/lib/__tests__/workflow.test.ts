import { describe, expect, test } from 'bun:test';
import {
  WORKFLOW_SCHEMA_VERSION,
  createWorkflowNode,
  parseWorkflowDocument,
  serializeWorkflow,
  topologicalOrder,
  validateWorkflow,
  type WorkflowEdge,
  type WorkflowNode,
} from '../workflow';

const node = (id: string, type: WorkflowNode['type'] = 'note'): WorkflowNode => ({
  id,
  type,
  label: id,
  config: {},
});

const edge = (id: string, from: string, to: string): WorkflowEdge => ({ id, from, to });

describe('validateWorkflow', () => {
  test('accepts a valid diamond', () => {
    const nodes = [node('start', 'start'), node('a'), node('b'), node('end')];
    const edges = [
      edge('e1', 'start', 'a'),
      edge('e2', 'start', 'b'),
      edge('e3', 'a', 'end'),
      edge('e4', 'b', 'end'),
    ];

    expect(validateWorkflow(nodes, edges)).toEqual({ ok: true, errors: [] });
  });

  test('accepts an empty workflow', () => {
    expect(validateWorkflow([], []).ok).toBe(true);
  });

  test('rejects a three-node cycle and names it', () => {
    const nodes = [node('a'), node('b'), node('c')];
    const edges = [edge('e1', 'a', 'b'), edge('e2', 'b', 'c'), edge('e3', 'c', 'a')];

    const result = validateWorkflow(nodes, edges);

    expect(result.ok).toBe(false);
    expect(result.errors.some((error) => error.includes('cycle'))).toBe(true);
  });

  test('rejects an edge pointing at a missing node', () => {
    const result = validateWorkflow([node('a')], [edge('e1', 'a', 'ghost')]);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain('edge e1 points at a missing node: ghost');
  });

  test('rejects an edge starting at a missing node', () => {
    const result = validateWorkflow([node('a')], [edge('e1', 'ghost', 'a')]);

    expect(result.ok).toBe(false);
    expect(result.errors).toContain('edge e1 starts at a missing node: ghost');
  });

  test('rejects self edges, duplicate ids and unknown types', () => {
    const nodes = [node('a'), node('a'), { ...node('b'), type: 'plugin' as any }];
    const result = validateWorkflow(nodes, [edge('e1', 'a', 'a')]);

    expect(result.ok).toBe(false);
    expect(result.errors.some((error) => error.includes('duplicate node id'))).toBe(true);
    expect(result.errors.some((error) => error.includes('unknown type'))).toBe(true);
    expect(result.errors.some((error) => error.includes('to itself'))).toBe(true);
  });
});

describe('topologicalOrder', () => {
  test('orders dependencies before dependants', () => {
    const nodes = [node('end'), node('a'), node('start', 'start')];
    const edges = [edge('e1', 'start', 'a'), edge('e2', 'a', 'end')];

    const ordered = topologicalOrder(nodes, edges)?.map((item) => item.id);

    expect(ordered).toEqual(['start', 'a', 'end']);
  });

  test('returns null for a cycle', () => {
    const nodes = [node('a'), node('b')];
    const edges = [edge('e1', 'a', 'b'), edge('e2', 'b', 'a')];

    expect(topologicalOrder(nodes, edges)).toBeNull();
  });
});

describe('createWorkflowNode', () => {
  test('fills a default label per native type', () => {
    expect(createWorkflowNode('task', 'n1').label).toBe('Task');
    expect(createWorkflowNode('ai', 'n2', 'Summarise').label).toBe('Summarise');
    expect(createWorkflowNode('note', 'n3').config).toEqual({});
  });
});

describe('workflow documents', () => {
  test('round-trips nodes and edges unchanged', () => {
    const document = {
      name: 'Morning triage',
      version: WORKFLOW_SCHEMA_VERSION,
      nodes: [node('start', 'start'), node('a')],
      edges: [edge('e1', 'start', 'a')],
    };

    const parsed = parseWorkflowDocument(serializeWorkflow(document));

    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.workflow.nodes).toEqual(document.nodes);
    expect(parsed.workflow.edges).toEqual(document.edges);
  });

  test('rejects a script with a cycle', () => {
    const raw = JSON.stringify({
      name: 'looping',
      version: 1,
      nodes: [node('a'), node('b')],
      edges: [edge('e1', 'a', 'b'), edge('e2', 'b', 'a')],
    });

    const parsed = parseWorkflowDocument(raw);

    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.errors.some((error) => error.includes('cycle'))).toBe(true);
  });

  test('rejects malformed JSON and missing sections', () => {
    expect(parseWorkflowDocument('{nope').ok).toBe(false);
    expect(parseWorkflowDocument('[]').ok).toBe(false);
    expect(parseWorkflowDocument('{}').ok).toBe(false);
  });
});
