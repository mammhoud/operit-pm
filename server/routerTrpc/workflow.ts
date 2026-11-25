import { TRPCError } from '@trpc/server';
import { z } from 'zod';
import { router, authProcedure } from '../middleware';
import { db } from '../db';
import {
  WORKFLOW_SCHEMA_VERSION,
  topologicalOrder,
  validateWorkflow,
  workflowNodeTypes,
  type WorkflowEdge,
  type WorkflowNode,
} from '@shared/lib/workflow';

/**
 * Workflow canvas server surface (PI-035).
 *
 * A workflow is a PlanInc-native node/edge document stored as JSON (`workflow`
 * table). The canvas, the loader and the runner all validate through
 * `shared/lib/workflow.ts`, so a document that loads is exactly a document that
 * may run (plan Review Focus 3).
 *
 * `run` executes the graph — it validates, computes a deterministic topological
 * order and records a `workflowRuns` row with the per-node steps. Node *effects*
 * (writing a note, calling an AI provider) belong to per-type handlers in the
 * next slice; the canvas slice owns graph execution and its audit trail.
 */

const workflowNodeSchema = z.object({
  id: z.string().trim().min(1).max(64),
  type: z.enum(workflowNodeTypes),
  label: z.string().trim().min(1).max(120),
  config: z.record(z.string(), z.unknown()).default({}),
});

const workflowEdgeSchema = z.object({
  id: z.string().trim().min(1).max(64),
  from: z.string().trim().min(1).max(64),
  to: z.string().trim().min(1).max(64),
});

const workflowSchema = z.object({
  id: z.number().int(),
  accountId: z.number().int(),
  name: z.string(),
  version: z.number().int(),
  nodes: z.array(workflowNodeSchema),
  edges: z.array(workflowEdgeSchema),
  createdAt: z.coerce.date(),
  updatedAt: z.coerce.date(),
});

const workflowInput = z.object({
  id: z.number().int().optional(),
  name: z.string().trim().min(1).max(120),
  version: z.number().int().min(1).default(WORKFLOW_SCHEMA_VERSION),
  nodes: z.array(workflowNodeSchema).max(200).default([]),
  edges: z.array(workflowEdgeSchema).max(400).default([]),
});

const workflowRunSchema = z.object({
  started: z.boolean(),
  runId: z.string(),
  status: z.enum(['queued', 'completed', 'rejected']),
  steps: z.array(z.object({ nodeId: z.string(), type: z.string(), status: z.string() })),
  errors: z.array(z.string()),
});

function normalize(value: { nodes?: unknown; edges?: unknown } | null | undefined): {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
} {
  return {
    nodes: (Array.isArray(value?.nodes) ? value.nodes : []) as WorkflowNode[],
    edges: (Array.isArray(value?.edges) ? value.edges : []) as WorkflowEdge[],
  };
}

export const workflowRouter = router({
  list: authProcedure.input(z.void()).output(z.array(workflowSchema)).query(async ({ ctx }) => {
    return db.workflow.findMany({
      where: { accountId: Number(ctx.id) },
      orderBy: { updatedAt: 'desc' },
    });
  }),

  get: authProcedure
    .input(z.object({ id: z.number().int() }))
    .output(z.union([workflowSchema, z.null()]))
    .query(async ({ ctx, input }) => {
      const found = await db.workflow.findFirst({
        where: { id: input.id, accountId: Number(ctx.id) },
      });
      return found ?? null;
    }),

  /** Validate without saving — the canvas calls this on every edit batch. */
  validate: authProcedure
    .input(z.object({ nodes: z.array(workflowNodeSchema), edges: z.array(workflowEdgeSchema) }))
    .output(z.object({ ok: z.boolean(), errors: z.array(z.string()) }))
    .mutation(async ({ input }) => validateWorkflow(input.nodes as WorkflowNode[], input.edges as WorkflowEdge[])),

  upsert: authProcedure.input(workflowInput).output(workflowSchema).mutation(async ({ ctx, input }) => {
    const accountId = Number(ctx.id);
    const validation = validateWorkflow(input.nodes as WorkflowNode[], input.edges as WorkflowEdge[]);
    if (!validation.ok) {
      throw new TRPCError({ code: 'BAD_REQUEST', message: validation.errors.join('; ') });
    }

    const data = {
      name: input.name,
      version: input.version,
      // Stored exactly as supplied: the JSON round-trip is part of the contract.
      nodes: input.nodes,
      edges: input.edges,
      accountId,
    };

    if (input.id === undefined) {
      return db.workflow.create({ data });
    }

    const current = await db.workflow.findFirst({ where: { id: input.id, accountId } });
    if (!current) throw new TRPCError({ code: 'NOT_FOUND', message: 'Workflow not found' });

    return db.workflow.update({ where: { id: input.id }, data });
  }),

  delete: authProcedure
    .input(z.object({ id: z.number().int() }))
    .output(z.object({ success: z.boolean() }))
    .mutation(async ({ ctx, input }) => {
      const accountId = Number(ctx.id);
      const current = await db.workflow.findFirst({ where: { id: input.id, accountId } });
      if (!current) throw new TRPCError({ code: 'NOT_FOUND', message: 'Workflow not found' });

      await db.workflowRuns.deleteMany({ where: { workflowId: input.id, accountId } });
      await db.workflow.delete({ where: { id: input.id } });
      return { success: true };
    }),

  run: authProcedure
    .input(z.object({ id: z.number().int() }))
    .output(workflowRunSchema)
    .mutation(async ({ ctx, input }) => {
      const accountId = Number(ctx.id);
      const workflow = await db.workflow.findFirst({ where: { id: input.id, accountId } });
      if (!workflow) throw new TRPCError({ code: 'NOT_FOUND', message: 'Workflow not found' });

      const { nodes, edges } = normalize(workflow);
      const validation = validateWorkflow(nodes, edges);
      if (!validation.ok) {
        // An invalid workflow is refused before any run row exists.
        return {
          started: false,
          runId: '',
          status: 'rejected' as const,
          steps: [],
          errors: validation.errors,
        };
      }

      const ordered = topologicalOrder(nodes, edges);
      if (!ordered) {
        return {
          started: false,
          runId: '',
          status: 'rejected' as const,
          steps: [],
          errors: ['workflow contains a cycle'],
        };
      }

      const steps = ordered.map((node) => ({
        nodeId: node.id,
        type: node.type,
        status: 'completed',
      }));

      const run = await db.workflowRuns.create({
        data: {
          workflowId: input.id,
          accountId,
          status: 'completed',
          steps,
          startedAt: new Date(),
          finishedAt: new Date(),
        },
      });

      return {
        started: true,
        runId: String(run.id),
        status: 'completed' as const,
        steps,
        errors: [],
      };
    }),
});
