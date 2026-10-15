import { access, readFile, writeFile } from 'node:fs/promises';
import process from 'node:process';
import { pathsFor, saveState, validateLease, withTaskMutationLock } from './harness-lifecycle.mjs';

const [taskPath, packetDir, barrierRoot, mode] = process.argv.slice(2);
if (!taskPath || !packetDir || !barrierRoot) throw new Error('taskPath, packetDir and barrierRoot are required');

const task = JSON.parse(await readFile(taskPath, 'utf8'));
const paths = pathsFor(taskPath, packetDir, task.task_id);

async function waitFor(file) {
  while (true) {
    try {
      await access(file);
      return;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  }
}

if (mode === 'hold-lock') {
  await withTaskMutationLock(paths, async () => {
    await writeFile(`${barrierRoot}.locked`, `${process.pid}\n`, { flag: 'wx' });
    await waitFor(`${barrierRoot}.release`);
  });
  await writeFile(`${barrierRoot}.result.json`, JSON.stringify({ status: 'RELEASED' }) + '\n', { flag: 'wx' });
  process.exit(0);
}

const claim = JSON.parse(await readFile(paths.claimPath, 'utf8'));
const state = JSON.parse(await readFile(paths.statePath, 'utf8'));
await validateLease(taskPath, { owner: claim.owner, token: claim.lease_token, version: claim.version });
await writeFile(`${barrierRoot}.validated`, `${process.pid}\n`, { flag: 'wx' });
await waitFor(`${barrierRoot}.resume`);

try {
  await saveState(state, paths, { history: [...state.history, { marker: 'A-stale-write' }] }, claim);
  await writeFile(`${barrierRoot}.result.json`, JSON.stringify({ status: 'SAVED' }) + '\n', { flag: 'wx' });
} catch (error) {
  await writeFile(`${barrierRoot}.result.json`, JSON.stringify({ status: 'REJECTED', code: error.code, message: error.message }) + '\n', { flag: 'wx' });
}
