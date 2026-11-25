/**
 * Workflow data model (PI-035, Phase 2 of the workflow-and-integrations plan).
 *
 * A workflow is a small, PlanInc-native node/edge graph stored as JSON:
 * `{ name, nodes[], edges[], version }`. The canvas reuses `PlanIncGraph`, the
 * server stores the document unchanged, and this module owns the only
 * validation rules — so "invalid on load" and "invalid on run" are the same
 * answer (plan Review Focus 3).
 *
 * Native node types are the PlanInc primitives. Arbitrary plugin node types were
 * deliberately left out (Open Decision 2): a node the runner cannot interpret
 * would otherwise validate and then hang.
 */

export const workflowNodeTypes = ['start', 'note', 'task', 'resource', 'ai'] as const

export type WorkflowNodeType = (typeof workflowNodeTypes)[number]

export type WorkflowNode = {
  id: string
  type: WorkflowNodeType
  label: string
  config: Record<string, unknown>
}

export type WorkflowEdge = {
  id: string
  from: string
  to: string
}

export type WorkflowValidation = {
  ok: boolean
  errors: string[]
}

export type WorkflowDocument = {
  name: string
  version: number
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export const WORKFLOW_SCHEMA_VERSION = 1

export function isWorkflowNodeType(value: unknown): value is WorkflowNodeType {
  return typeof value === 'string' && (workflowNodeTypes as readonly string[]).includes(value)
}

/** Create a node with defaults the palette can render immediately. */
export function createWorkflowNode(
  type: WorkflowNodeType,
  id: string,
  label?: string
): WorkflowNode {
  return {
    id,
    type,
    label: label ?? defaultNodeLabel(type),
    config: {},
  }
}

export function defaultNodeLabel(type: WorkflowNodeType): string {
  switch (type) {
    case 'start':
      return 'Start'
    case 'note':
      return 'Note'
    case 'task':
      return 'Task'
    case 'resource':
      return 'Resource'
    case 'ai':
      return 'AI'
  }
}

/**
 * Validate a workflow graph.
 *
 * Rejects: unknown node types, duplicate ids, edges with missing endpoints,
 * self-edges, and any cycle (DFS colouring). A diamond — two paths that merge —
 * is valid.
 */
export function validateWorkflow(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[]
): WorkflowValidation {
  const errors: string[] = []

  if (!Array.isArray(nodes)) return { ok: false, errors: ['nodes must be an array'] }
  if (!Array.isArray(edges)) return { ok: false, errors: ['edges must be an array'] }

  const byId = new Map<string, WorkflowNode>()
  for (const node of nodes) {
    if (!node || typeof node !== 'object') {
      errors.push('every node must be an object')
      continue
    }
    if (!node.id) {
      errors.push('every node needs an id')
      continue
    }
    if (byId.has(node.id)) {
      errors.push(`duplicate node id: ${node.id}`)
      continue
    }
    if (!isWorkflowNodeType(node.type)) {
      errors.push(`node ${node.id} has unknown type: ${String(node.type)}`)
    }
    byId.set(node.id, node)
  }

  const adjacency = new Map<string, string[]>()
  const seenEdges = new Set<string>()

  for (const edge of edges) {
    if (!edge || typeof edge !== 'object') {
      errors.push('every edge must be an object')
      continue
    }
    if (!edge.id) {
      errors.push('every edge needs an id')
      continue
    }
    if (seenEdges.has(edge.id)) {
      errors.push(`duplicate edge id: ${edge.id}`)
      continue
    }
    seenEdges.add(edge.id)

    if (!byId.has(edge.from)) {
      errors.push(`edge ${edge.id} starts at a missing node: ${edge.from}`)
      continue
    }
    if (!byId.has(edge.to)) {
      errors.push(`edge ${edge.id} points at a missing node: ${edge.to}`)
      continue
    }
    if (edge.from === edge.to) {
      errors.push(`edge ${edge.id} connects node ${edge.from} to itself`)
      continue
    }

    const list = adjacency.get(edge.from) ?? []
    list.push(edge.to)
    adjacency.set(edge.from, list)
  }

  const cycle = findCycle(byId.keys(), adjacency)
  if (cycle) errors.push(`workflow contains a cycle: ${cycle.join(' -> ')}`)

  return { ok: errors.length === 0, errors }
}

/**
 * Return one cycle path when the graph is cyclic, otherwise null.
 * Iterative DFS with a three-colour state map: 0 = unvisited, 1 = on stack,
 * 2 = done. The stack is kept separately so the reported path is readable.
 */
export function findCycle(
  nodeIds: Iterable<string>,
  adjacency: Map<string, string[]>
): string[] | null {
  const state = new Map<string, 0 | 1 | 2>()
  const stack: string[] = []

  const visit = (start: string): string[] | null => {
    // Frames hold the node and how many successors have been consumed.
    const frames: Array<{ id: string; index: number }> = [{ id: start, index: 0 }]
    state.set(start, 1)
    stack.push(start)

    while (frames.length > 0) {
      const frame = frames[frames.length - 1]
      const successors = adjacency.get(frame.id) ?? []

      if (frame.index >= successors.length) {
        state.set(frame.id, 2)
        frames.pop()
        stack.pop()
        continue
      }

      const next = successors[frame.index]
      frame.index += 1

      const seen = state.get(next) ?? 0
      if (seen === 1) {
        const from = stack.indexOf(next)
        return [...stack.slice(from === -1 ? 0 : from), next]
      }
      if (seen === 2) continue

      state.set(next, 1)
      stack.push(next)
      frames.push({ id: next, index: 0 })
    }

    return null
  }

  for (const id of nodeIds) {
    if ((state.get(id) ?? 0) !== 0) continue
    const cycle = visit(id)
    if (cycle) return cycle
  }

  return null
}

/**
 * Topological order for execution. Returns `null` when the graph is cyclic.
 * Ties keep document order, so a run is deterministic for the same document.
 */
export function topologicalOrder(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[]
): WorkflowNode[] | null {
  const indegree = new Map<string, number>()
  for (const node of nodes) indegree.set(node.id, 0)

  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    if (!indegree.has(edge.from) || !indegree.has(edge.to)) continue
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1)
    const list = adjacency.get(edge.from) ?? []
    list.push(edge.to)
    adjacency.set(edge.from, list)
  }

  const queue = nodes.filter((node) => (indegree.get(node.id) ?? 0) === 0).map((n) => n.id)
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const ordered: WorkflowNode[] = []

  while (queue.length > 0) {
    const id = queue.shift() as string
    const node = byId.get(id)
    if (node) ordered.push(node)
    for (const next of adjacency.get(id) ?? []) {
      const remaining = (indegree.get(next) ?? 0) - 1
      indegree.set(next, remaining)
      if (remaining === 0) queue.push(next)
    }
  }

  return ordered.length === nodes.length ? ordered : null
}

/** Deep, stable copy of the parts that must round-trip unchanged. */
export function serializeWorkflow(document: WorkflowDocument): string {
  return JSON.stringify(
    {
      name: document.name,
      version: document.version,
      nodes: document.nodes,
      edges: document.edges,
    },
    null,
    2
  )
}

/**
 * Parse a workflow document (a "loading script") and validate it before it can
 * reach the canvas or the database. Never throws: the caller renders `errors`.
 */
export function parseWorkflowDocument(
  raw: string
): { ok: true; workflow: WorkflowDocument } | { ok: false; errors: string[] } {
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch (error) {
    return { ok: false, errors: [`invalid JSON: ${(error as Error).message}`] }
  }

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, errors: ['workflow document must be a JSON object'] }
  }

  const candidate = value as Partial<WorkflowDocument>
  const errors: string[] = []

  if (typeof candidate.name !== 'string' || candidate.name.trim() === '') {
    errors.push('workflow needs a name')
  }
  if (candidate.version !== undefined && typeof candidate.version !== 'number') {
    errors.push('workflow version must be a number')
  }
  if (!Array.isArray(candidate.nodes)) errors.push('workflow needs a nodes array')
  if (!Array.isArray(candidate.edges)) errors.push('workflow needs an edges array')
  if (errors.length > 0) return { ok: false, errors }

  const validation = validateWorkflow(candidate.nodes as WorkflowNode[], candidate.edges as WorkflowEdge[])
  if (!validation.ok) return { ok: false, errors: validation.errors }

  return {
    ok: true,
    workflow: {
      name: (candidate.name as string).trim(),
      version: candidate.version ?? WORKFLOW_SCHEMA_VERSION,
      nodes: candidate.nodes as WorkflowNode[],
      edges: candidate.edges as WorkflowEdge[],
    },
  }
}
