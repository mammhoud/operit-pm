import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { observer } from 'mobx-react-lite';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/Common/ScrollArea';
import { Icon } from '@/components/Common/Iconify/icons';
import { WorkflowCanvas, NodePalette, loadWorkflowScriptFile } from '@/components/PlanincWorkflow';
import { api } from '@/lib/trpc';
import {
  WORKFLOW_SCHEMA_VERSION,
  createWorkflowNode,
  validateWorkflow,
  type WorkflowEdge,
  type WorkflowNode,
  type WorkflowNodeType,
} from '@shared/lib/workflow';

type SavedWorkflow = {
  id: number;
  name: string;
  version: number;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};

type RunResult = {
  started: boolean;
  runId: string;
  status: string;
  steps: Array<{ nodeId: string; type: string; status: string }>;
  errors: string[];
};

/**
 * `/workflow` — design a node/edge workflow, load one from a script file, then
 * save or run it. Validation runs locally on every edit (instant feedback) and
 * again on the server before a save or a run, so the two can never disagree.
 */
const WorkflowPage = observer(() => {
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [name, setName] = useState('Untitled workflow');
  const [nodes, setNodes] = useState<WorkflowNode[]>([]);
  const [edges, setEdges] = useState<WorkflowEdge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedWorkflow[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const nodeCounter = useRef(0);

  const validation = useMemo(() => validateWorkflow(nodes, edges), [nodes, edges]);

  const refreshSaved = useCallback(async () => {
    try {
      const list = await api.workflow.list.query();
      setSaved(list as SavedWorkflow[]);
    } catch (error) {
      setNotice((error as Error).message);
    }
  }, []);

  useEffect(() => {
    refreshSaved();
  }, [refreshSaved]);

  const addNode = (type: WorkflowNodeType) => {
    nodeCounter.current += 1;
    const id = `${type}-${Date.now().toString(36)}-${nodeCounter.current}`;
    const node = createWorkflowNode(type, id);
    setNodes((current) => [...current, node]);
    if (selectedId) {
      setEdges((current) => [
        ...current,
        { id: `e-${id}`, from: selectedId, to: id },
      ]);
    }
    setSelectedId(id);
    setNotice(null);
  };

  const deleteNode = (id: string) => {
    setNodes((current) => current.filter((node) => node.id !== id));
    setEdges((current) => current.filter((edge) => edge.from !== id && edge.to !== id));
    setSelectedId((current) => (current === id ? null : current));
  };

  const connectSelected = (to: string) => {
    if (!selectedId || selectedId === to) return;
    setEdges((current) => [...current, { id: `e-${selectedId}-${to}`, from: selectedId, to }]);
  };

  const save = async () => {
    if (!validation.ok) {
      setNotice(validation.errors.join('; '));
      return;
    }
    setBusy(true);
    try {
      const result = await api.workflow.upsert.mutate({
        id: workflowId ?? undefined,
        name: name.trim() || 'Untitled workflow',
        version: WORKFLOW_SCHEMA_VERSION,
        nodes,
        edges,
      });
      setWorkflowId((result as SavedWorkflow).id);
      setNotice('Workflow saved.');
      await refreshSaved();
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const runWorkflow = async () => {
    if (workflowId === null) {
      setNotice('Save the workflow before running it.');
      return;
    }
    setBusy(true);
    try {
      const result = (await api.workflow.run.mutate({ id: workflowId })) as RunResult;
      setRun(result);
      setNotice(result.started ? `Run ${result.status}.` : result.errors.join('; '));
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const openSaved = (workflow: SavedWorkflow) => {
    setWorkflowId(workflow.id);
    setName(workflow.name);
    setNodes(workflow.nodes ?? []);
    setEdges(workflow.edges ?? []);
    setSelectedId(null);
    setRun(null);
    setNotice(null);
  };

  const loadScript = async (file: File | null | undefined) => {
    if (!file) return;
    const result = await loadWorkflowScriptFile(file);
    if (!result.ok) {
      setNotice(result.errors.join('; '));
      return;
    }
    setWorkflowId(null);
    setName(result.workflow.name);
    setNodes(result.workflow.nodes);
    setEdges(result.workflow.edges);
    setSelectedId(null);
    setRun(null);
    setNotice('Workflow script loaded.');
  };

  return (
    <ScrollArea className="h-full">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-6 md:px-6">
        <header className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-semibold">Workflow</h1>
            <p className="text-sm text-muted-foreground">
              Build a node graph, load it from a script, then run it.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => fileInput.current?.click()} disabled={busy}>
              <Icon icon="tabler:file-import" width={16} height={16} className="mr-2" />
              Load script
            </Button>
            <Button variant="outline" size="sm" onClick={save} disabled={busy || !validation.ok}>
              <Icon icon="tabler:device-floppy" width={16} height={16} className="mr-2" />
              Save
            </Button>
            <Button size="sm" onClick={runWorkflow} disabled={busy || workflowId === null}>
              <Icon icon="tabler:player-play" width={16} height={16} className="mr-2" />
              Run
            </Button>
          </div>
        </header>

        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(event) => loadScript(event.target.files?.[0])}
        />

        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-label="Workflow name"
              className="sm:max-w-sm"
            />
            <span className="text-xs text-muted-foreground">
              {workflowId === null ? 'Not saved yet' : `Saved as #${workflowId}`}
            </span>
          </div>

          <NodePalette onCreate={addNode} disabled={busy} />

          {selectedId && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>Selected {selectedId}</span>
              <Button variant="outline" size="sm" onClick={() => deleteNode(selectedId)}>
                Delete node
              </Button>
              {nodes
                .filter((node) => node.id !== selectedId)
                .slice(0, 3)
                .map((node) => (
                  <Button key={node.id} variant="ghost" size="sm" onClick={() => connectSelected(node.id)}>
                    Connect to {node.label}
                  </Button>
                ))}
            </div>
          )}

          <div className="h-[380px]">
            <WorkflowCanvas
              nodes={nodes}
              edges={edges}
              selectedNodeId={selectedId}
              onSelectNode={setSelectedId}
              onDeleteNode={deleteNode}
            />
          </div>

          {validation.ok ? (
            <p className="text-xs text-muted-foreground">
              {nodes.length} node{nodes.length === 1 ? '' : 's'} · {edges.length} edge
              {edges.length === 1 ? '' : 's'} · valid
            </p>
          ) : (
            <ul className="text-xs text-destructive">
              {validation.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}

          {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded-lg border border-border bg-card p-4">
            <h2 className="mb-2 text-sm font-semibold">Saved workflows</h2>
            {saved.length === 0 ? (
              <p className="text-xs text-muted-foreground">Nothing saved yet.</p>
            ) : (
              <ul className="divide-y divide-border text-sm">
                {saved.map((workflow) => (
                  <li key={workflow.id} className="flex items-center justify-between gap-2 py-2">
                    <button className="truncate text-left hover:underline" onClick={() => openSaved(workflow)}>
                      {workflow.name}
                    </button>
                    <span className="text-xs text-muted-foreground">
                      {workflow.nodes?.length ?? 0} nodes
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-lg border border-border bg-card p-4">
            <h2 className="mb-2 text-sm font-semibold">Last run</h2>
            {!run ? (
              <p className="text-xs text-muted-foreground">No run in this session.</p>
            ) : (
              <div className="text-xs">
                <p className="mb-2">
                  {run.status} · run {run.runId || '—'}
                </p>
                <ol className="space-y-1">
                  {run.steps.map((step) => (
                    <li key={step.nodeId} className="flex items-center justify-between gap-2">
                      <span className="truncate">{step.nodeId}</span>
                      <span className="text-muted-foreground">{step.status}</span>
                    </li>
                  ))}
                </ol>
                {run.errors.length > 0 && <p className="mt-2 text-destructive">{run.errors.join('; ')}</p>}
              </div>
            )}
          </section>
        </div>
      </div>
    </ScrollArea>
  );
});

export default WorkflowPage;
