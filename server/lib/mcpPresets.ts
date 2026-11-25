/**
 * Bundled MCP preset catalog (PI-036, Phase 3).
 *
 * Presets are static on purpose (Open Decision 3): installing one never fetches
 * a registry, so a compromised network cannot inject a command. Every stdio
 * preset still goes through `validateStdioServer` on install — the catalog is
 * not trusted input, it is just convenient input.
 */
import { validateStdioServer } from './mcpValidation';

export type McpPreset = {
  id: string;
  name: string;
  description: string;
  type: 'stdio' | 'sse' | 'streamable-http';
  command?: string;
  args?: string[];
  url?: string;
  /** Optional documentation link shown next to the install button. */
  docs?: string;
};

export const MCP_PRESETS: McpPreset[] = [
  {
    id: 'filesystem',
    name: 'Filesystem',
    description: 'Read and search files under a directory you choose.',
    type: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '.'],
  },
  {
    id: 'git',
    name: 'Git',
    description: 'Inspect commits, diffs and branches of the current repository.',
    type: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-git', '--repository', '.'],
  },
  {
    id: 'fetch',
    name: 'Fetch',
    description: 'Fetch a URL and convert it to markdown for the model.',
    type: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-fetch'],
  },
  {
    id: 'memory',
    name: 'Memory',
    description: 'A persistent knowledge-graph memory for the assistant.',
    type: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-memory'],
  },
  {
    id: 'sequential-thinking',
    name: 'Sequential thinking',
    description: 'Structured, step-by-step reasoning helper.',
    type: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-sequential-thinking'],
  },
  {
    id: 'time',
    name: 'Time',
    description: 'Current time and timezone conversion.',
    type: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-time'],
  },
];

export function findPreset(id: string): McpPreset | undefined {
  return MCP_PRESETS.find((preset) => preset.id === id);
}

export type PresetServerData = {
  name: string;
  description: string;
  type: 'stdio' | 'sse' | 'streamable-http';
  command: string | null;
  args: string[] | null;
  url: string | null;
  env: Record<string, unknown> | null;
  headers: Record<string, unknown> | null;
  isEnabled: boolean;
};

/**
 * Turn a preset (plus the caller's env/headers) into a server row, validating it
 * with the same rules as a manual create. Returns errors instead of throwing so
 * the router owns the HTTP shape and the unit tests can assert on it.
 */
export function preparePresetInstall(
  preset: McpPreset,
  overrides: { env?: Record<string, unknown>; headers?: Record<string, unknown> } = {}
): { ok: true; data: PresetServerData } | { ok: false; errors: string[] } {
  const errors: string[] = [];

  if (preset.type === 'stdio') {
    const validation = validateStdioServer(preset.command, preset.args);
    errors.push(...validation.errors);
  } else if (!preset.url) {
    errors.push(`${preset.type} transport requires a URL`);
  }

  if (errors.length > 0) return { ok: false, errors };

  return {
    ok: true,
    data: {
      name: preset.name,
      description: preset.description,
      type: preset.type,
      command: preset.type === 'stdio' ? preset.command ?? null : null,
      args: preset.type === 'stdio' ? preset.args ?? null : null,
      url: preset.type === 'stdio' ? null : preset.url ?? null,
      env: overrides.env ?? null,
      headers: overrides.headers ?? null,
      isEnabled: true,
    },
  };
}
