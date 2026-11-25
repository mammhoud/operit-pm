import { useMemo } from 'react';
import { Icon } from '@/components/Common/Iconify/icons';
import type { WorkflowEdge, WorkflowNode } from '@shared/lib/workflow';
import { NODE_ICON, NODE_LABEL } from './NodePalette';

/**
 * Deterministic DAG view of a workflow.
 *
 * The knowledge graph's `PlanIncGraph` is force-directed over numeric ids — a
 * good explorer, a bad editor: node positions drift between renders and a
 * workflow is read top-to-bottom. This canvas keeps the same node/edge language
 * (tokens, rounded cards, dashed relation strokes) on a layered layout computed
 * from the graph itself, so the same document always draws the same picture.
 * It uses no graph library.
 */
const COLUMN_WIDTH = 210;
const ROW_HEIGHT = 104;
const NODE_WIDTH = 168;
const NODE_HEIGHT = 64;
const PADDING = 32;

export type WorkflowCanvasProps = {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  selectedNodeId?: string | null;
  onSelectNode?: (id: string) => void;
  onDeleteNode?: (id: string) => void;
};

function layers(nodes: WorkflowNode[], edges: WorkflowEdge[]): Map<string, number> {
  const incoming = new Map<string, string[]>();
  for (const node of nodes) incoming.set(node.id, []);
  for (const edge of edges) {
    if (!incoming.has(edge.to)) continue;
    incoming.get(edge.to)!.push(edge.from);
  }

  const depth = new Map<string, number>();
  const visiting = new Set<string>();

  const resolve = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // cycles are rejected before rendering
    visiting.add(id);
    const parents = incoming.get(id) ?? [];
    const value = parents.length === 0 ? 0 : Math.max(...parents.map(resolve)) + 1;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };

  for (const node of nodes) resolve(node.id);
  return depth;
}

export function WorkflowCanvas({
  nodes,
  edges,
  selectedNodeId,
  onSelectNode,
  onDeleteNode,
}: WorkflowCanvasProps) {
  const layout = useMemo(() => {
    const depth = layers(nodes, edges);
    const byColumn = new Map<number, WorkflowNode[]>();
    for (const node of nodes) {
      const column = depth.get(node.id) ?? 0;
      const list = byColumn.get(column) ?? [];
      list.push(node);
      byColumn.set(column, list);
    }

    const positions = new Map<string, { x: number; y: number }>();
    let maxRows = 1;
    for (const [column, list] of byColumn) {
      maxRows = Math.max(maxRows, list.length);
      list.forEach((node, row) => {
        positions.set(node.id, {
          x: PADDING + column * COLUMN_WIDTH,
          y: PADDING + row * ROW_HEIGHT,
        });
      });
    }

    const columns = byColumn.size || 1;
    const width = PADDING * 2 + Math.max(1, columns) * COLUMN_WIDTH;
    const height = PADDING * 2 + maxRows * ROW_HEIGHT;

    return { positions, width, height };
  }, [nodes, edges]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-muted-foreground">
        <Icon icon="tabler:git-branch" width={32} height={32} />
        <p className="text-sm">Add a node to start the workflow.</p>
      </div>
    );
  }

  return (
    <div className="h-full w-full overflow-auto rounded-lg border border-border bg-card">
      <svg
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="img"
        aria-label="Workflow graph"
      >
        <defs>
          <marker id="workflow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>

        <g className="text-muted-foreground">
          {edges.map((edge) => {
            const from = layout.positions.get(edge.from);
            const to = layout.positions.get(edge.to);
            if (!from || !to) return null;
            const x1 = from.x + NODE_WIDTH;
            const y1 = from.y + NODE_HEIGHT / 2;
            const x2 = to.x;
            const y2 = to.y + NODE_HEIGHT / 2;
            const midX = (x1 + x2) / 2;
            return (
              <path
                key={edge.id}
                d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                fill="none"
                stroke="currentColor"
                strokeDasharray="4 4"
                markerEnd="url(#workflow-arrow)"
              />
            );
          })}
        </g>

        {nodes.map((node) => {
          const position = layout.positions.get(node.id);
          if (!position) return null;
          const selected = node.id === selectedNodeId;
          return (
            <g
              key={node.id}
              transform={`translate(${position.x} ${position.y})`}
              onClick={() => onSelectNode?.(node.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') onSelectNode?.(node.id);
                if (event.key === 'Delete' || event.key === 'Backspace') onDeleteNode?.(node.id);
              }}
              tabIndex={0}
              role="button"
              aria-label={`${NODE_LABEL[node.type]} node: ${node.label}`}
              className="cursor-pointer"
            >
              <rect
                width={NODE_WIDTH}
                height={NODE_HEIGHT}
                rx={8}
                style={{ fill: 'var(--card)' }}
                stroke={selected ? 'var(--primary)' : 'var(--border)'}
                strokeWidth={selected ? 2 : 1}
              />
              <foreignObject width={NODE_WIDTH} height={NODE_HEIGHT}>
                <div className="flex h-full w-full items-center gap-2 px-3">
                  <Icon icon={NODE_ICON[node.type]} width={18} height={18} />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{node.label}</p>
                    <p className="truncate text-xs text-muted-foreground">{NODE_LABEL[node.type]}</p>
                  </div>
                </div>
              </foreignObject>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
