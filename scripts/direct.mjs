import { readFile } from 'node:fs/promises';
import { prepareDirect, checkDirect, runDirect, statusDirect, verifyProductDirect, acceptDirect } from './lib/direct-run.mjs';
const json = async file => JSON.parse(await readFile(file, 'utf8'));
const [command, ...args] = process.argv.slice(2);
const controller = new AbortController();
process.on('SIGINT', () => controller.abort());
process.on('SIGTERM', () => controller.abort());
try {
  let result;
  if (command === 'prepare' && args.length === 4) result = await prepareDirect(args[0], await json(args[1]), await json(args[2]), args[3]);
  else if (command === 'check' && args.length === 1) { await checkDirect(args[0]); result = { status: 'READY', provider_invocations: 0 }; }
  else if (command === 'run' && args.length === 1) result = await runDirect(args[0], controller.signal);
  else if (command === 'status' && args.length === 1) result = await statusDirect(args[0]);
  else if (command === 'verify-product' && args.length === 1) result = await verifyProductDirect(args[0], controller.signal);
  else if (command === 'accept' && args.length === 2) result = await acceptDirect(args[0], args[1]);
  else throw Object.assign(Error('Usage: direct.mjs prepare <repo> <task.json> <config.json> <new-output-dir> | check/run/status/verify-product <prepared.json> | accept <prepared.json> <approvedBy>'), { code: 'INVALID_ARGS' });
  console.log(JSON.stringify(result, null, 2));
  if (['run', 'verify-product'].includes(command)) process.exitCode = result.status === 'WAITING_FOR_CHECKPOINT' ? 0 : 1;
} catch (error) {
  console.error(JSON.stringify({ status: 'BLOCKED', code: /^[A-Z][A-Z0-9_]{0,63}$/.test(error.code ?? '') ? error.code : 'DIRECT_COMMAND_FAILED' }));
  process.exitCode = 1;
}
