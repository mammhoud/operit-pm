/**
 * MCP stdio safety rules (PI-036).
 *
 * These used to live inside `routerTrpc/mcpServers.ts`. They were extracted so
 * the preset installer and the manual create/update path share **one** code
 * path — the plan forbids a second, weaker validation path for presets
 * (Review Focus 2).
 */
import path from 'path';

/** Commands a stdio MCP server may use. */
export const ALLOWED_MCP_COMMANDS = ['npx', 'node', 'python', 'python3', 'bun', 'deno'];

export function isCommandAllowed(command: string): boolean {
  if (!command || typeof command !== 'string') return false;
  return ALLOWED_MCP_COMMANDS.includes(path.basename(command).toLowerCase());
}

/** Shell metacharacters and filesystem escapes that have no place in argv. */
export const UNSAFE_ARG_PATTERNS: RegExp[] = [
  /[;&|`$]/g, // shell metacharacters
  /\$\(/g, // command substitution
  /`/g, // backtick substitution
  />\s*\//g, // redirect to an absolute path
  /\/etc\//g,
  /\/proc\//g,
  /\/sys\//g,
  /\.\./g, // traversal
  /&&/g,
  /\|\|/g,
  /<|>/g, // redirection
];

export function areArgsSafe(args: string[]): boolean {
  if (!Array.isArray(args)) return false;
  for (const arg of args) {
    if (typeof arg !== 'string') return false;
    for (const pattern of UNSAFE_ARG_PATTERNS) {
      // Patterns are created with /g for repeated use elsewhere; reset is
      // implicit in `test`, but keep it explicit so behaviour is obvious.
      pattern.lastIndex = 0;
      if (pattern.test(arg)) return false;
    }
  }
  return true;
}

export type McpValidation = { ok: boolean; errors: string[] };

/** Validate a stdio server definition against the shared whitelist + arg rules. */
export function validateStdioServer(command: string | null | undefined, args?: string[] | null): McpValidation {
  const errors: string[] = [];

  if (!command || !isCommandAllowed(command)) {
    errors.push(`Command not allowed. Allowed commands: ${ALLOWED_MCP_COMMANDS.join(', ')}`);
  }
  if (args && args.length > 0 && !areArgsSafe(args)) {
    errors.push('Arguments contain disallowed characters or patterns that could lead to command injection');
  }

  return { ok: errors.length === 0, errors };
}
