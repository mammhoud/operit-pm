import { router, authProcedure, superAdminAuthMiddleware } from '@server/middleware';
import { z } from 'zod';
import { getDockerStatus, listContainers } from '@server/lib/docker';

/**
 * Docker status surface (PI-034).
 *
 * Read-only and superadmin-only: the plugin and the settings panel consume this
 * instead of holding the daemon socket, and nothing beyond the projected fields
 * in `server/lib/docker.ts` is ever returned (plan Review Focus 4).
 */
const dockerStatusSchema = z.object({
  available: z.boolean(),
  version: z.string().optional(),
  error: z.string().optional(),
});

const dockerContainerSchema = z.object({
  id: z.string(),
  name: z.string(),
  image: z.string(),
  state: z.string(),
  status: z.string(),
});

export const dockerRouter = router({
  status: authProcedure
    .use(superAdminAuthMiddleware)
    .meta({
      openapi: {
        method: 'GET',
        path: '/v1/docker/status',
        summary: 'Docker daemon status',
        protect: true,
        tags: ['Docker'],
      },
    })
    .input(z.void())
    .output(dockerStatusSchema)
    .query(async () => getDockerStatus()),

  list: authProcedure
    .use(superAdminAuthMiddleware)
    .meta({
      openapi: {
        method: 'GET',
        path: '/v1/docker/list',
        summary: 'List containers with curated fields',
        protect: true,
        tags: ['Docker'],
      },
    })
    .input(z.void())
    .output(z.array(dockerContainerSchema))
    .query(async () => listContainers()),
});
