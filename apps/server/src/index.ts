import { createApp } from './app.js';
import { loadConfig } from './config.js';

const config = loadConfig();
const app = await createApp({ ...config, logger: true });

try {
  await app.listen({ port: config.port, host: config.host });
  app.log.info(
    { dataDir: config.dataDir, parserUrl: config.parserUrl },
    'ADP server started',
  );
} catch (error) {
  app.log.error(error);
  process.exit(1);
}

const shutdown = async (signal: string) => {
  app.log.info({ signal }, 'shutting down');
  await app.close();
  process.exit(0);
};
process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
