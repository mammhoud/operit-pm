import { Icon } from '@/components/Common/Iconify/icons';
import { Button } from '@/components/ui/button';
import { workflowNodeTypes, type WorkflowNodeType } from '@shared/lib/workflow';

const NODE_ICON: Record<WorkflowNodeType, string> = {
  start: 'tabler:player-play',
  note: 'tabler:note',
  task: 'tabler:checklist',
  resource: 'tabler:link',
  ai: 'tabler:sparkles',
};

const NODE_LABEL: Record<WorkflowNodeType, string> = {
  start: 'Start',
  note: 'Note',
  task: 'Task',
  resource: 'Resource',
  ai: 'AI',
};

/**
 * Add-node controls. PlanInc-native types only (Open Decision 2) so every node
 * the palette can create has a runner interpretation.
 */
export function NodePalette({
  onCreate,
  disabled,
}: {
  onCreate: (type: WorkflowNodeType) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="toolbar" aria-label="Add workflow node">
      {workflowNodeTypes.map((type) => (
        <Button
          key={type}
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled}
          onClick={() => onCreate(type)}
          className="gap-2"
        >
          <Icon icon={NODE_ICON[type]} width={16} height={16} />
          {NODE_LABEL[type]}
        </Button>
      ))}
    </div>
  );
}

export { NODE_ICON, NODE_LABEL };
