import path from 'node:path';
import { existsSync } from 'node:fs';
import {readJson} from './lib/workflow.mjs';
import {inspect,runBridge} from './lib/bridge.mjs';
import {redactText} from './lib/redact.mjs';
import {attachCheckpointReports,buildReport,formatReport} from './lib/report.mjs';

const [command,...args]=process.argv.slice(2);
const controller=new AbortController();
process.on('SIGINT',()=>controller.abort());process.on('SIGTERM',()=>controller.abort());
try {
  let result,packetDir;
  if (['pilot','run','quota-drill','activate'].includes(command)) {
    throw Object.assign(Error('New legacy dispatch is retired; use Direct. For an existing frozen packet, inspect and resume its original task.'), { code: 'LEGACY_NEW_DISPATCH_DISABLED' });
  }
  if(command==='report') {
    const [dir,...flags]=args;
    if(!dir)throw Error('Usage: bridge.mjs report <packets> [--audience owner|lead] [--format json|md]');
    const option=name=>{const index=flags.indexOf(name);return index>=0?flags[index+1]:undefined;};
    const audience=option('--audience')??'owner',format=option('--format')??'md';
    const known=new Set(['--audience','--format']);for(let index=0;index<flags.length;index+=2)if(!known.has(flags[index])||flags[index+1]===undefined)throw Error('Usage: bridge.mjs report <packets> [--audience owner|lead] [--format json|md]');
    const report=await buildReport(path.resolve(dir),{audience});
    process.stdout.write(formatReport(report,{format}));
    process.exitCode=0;
  } else if(command==='doctor') {
    const [config,cwd,dir,...flags]=args;
    const loadedConfig=await readJson(config);
    if (loadedConfig?.schema_version==='qq.bridge.v2' && loadedConfig.worker?.transport==='mcp') {
      const {healthCheck}=await import('./lib/harness-lifecycle.mjs');
      result=await healthCheck({repoRoot:path.resolve(cwd),config:loadedConfig});
    } else result=await inspect(path.resolve(cwd),loadedConfig,path.resolve(dir),flags.includes('--probe'),controller.signal);
  } else if(command==='resume') {
    const [config,task,cwd,dir]=args;
    if (!dir || !existsSync(path.join(dir, 'state.json'))) {
      throw Object.assign(Error('A frozen run packet with state.json is required before legacy resume.'), { code: 'LEGACY_RESUME_REQUIRES_PACKET' });
    }
    packetDir=dir;
    result=await runBridge({config:await readJson(config),taskPath:task,cwd,packetDir,pilot:args.includes('--pilot'),resume:true,signal:controller.signal});
  } else if(['request-changes','continue','checkpoint','reject-checkpoint','complete','recover','reconcile'].includes(command)) {
    const [config,task,cwd,dir,...rest]=args;
    if (command === 'recover' && (rest.length < 2 || !rest[0]?.trim() || !rest.slice(1).join(' ').trim())) {
      throw Object.assign(Error('recover requires the recorded claim owner and a reason.'), { code: 'RECOVERY_ARGUMENTS_REQUIRED' });
    }
    const lifecycle=await import('./lib/harness-lifecycle.mjs');
    const options={config:await readJson(config),taskPath:task,cwd,packetDir:dir};
    if(command==='request-changes'||command==='continue')options.instruction=rest.join(' ');
    if(command==='reject-checkpoint'){options.reason=rest.join(' ');options.rejectedBy=options.owner;}
    if(command==='recover'){options.decision='block';options.operator=rest[0];options.reason=rest.slice(1).join(' ');}
    result=command==='request-changes'?await lifecycle.requestChanges(options):command==='continue'?await lifecycle.continueHarnessLifecycle(options):command==='checkpoint'?await lifecycle.approveCheckpoint(options):command==='reject-checkpoint'?await lifecycle.rejectCheckpoint(options):command==='recover'?await lifecycle.recoverTask(options):command==='reconcile'?await lifecycle.reconcileTask(options):await lifecycle.completeTask(options);
  } else if(command==='inspect') {
    const [config,task,dir]=args;
    const {inspectTask}=await import('./lib/harness-lifecycle.mjs');
    result=await inspectTask({config:await readJson(config),taskPath:task,packetDir:dir});
  } else if(command==='stale-scan') {
    const {scanStaleTasks}=await import('./lib/harness-lifecycle.mjs');
    result=await scanStaleTasks({packetRoot:path.resolve(args[0])});
  } else if(command==='status')result=await readJson(path.join(args[0],'state.json'));
  else throw Error('Usage: bridge.mjs doctor <config> <repo> <packets> [--probe] | resume <config> <task> <repo> <packets> [--pilot] | request-changes/continue/checkpoint/complete <config> <task> <repo> <packets> [instruction] | status <packets> | report <packets> [--audience owner|lead] [--format json|md]');
  if(command!=='report'){
    if(result?.schema_version?.startsWith('qq.workflow.')){
      console.log(redactText(JSON.stringify(result,null,2)));
      process.exitCode=['PASS','FROZEN','CLAIMED','READY_TO_DISPATCH','RUNNING','READY_FOR_REVIEW','REQUEST_CHANGES','REWORKING','WAITING_FOR_CHECKPOINT','CHECKPOINTED','COMPLETED','PAUSED'].includes(result.status)?0:1;
    } else {
      const summary={status:result.status,head:result.head,repair_rounds:result.repair_rounds,senior_passes:result.senior_passes,reconciliation_required:result.reconciliation_required};
      const output=command==='resume'?await attachCheckpointReports(summary,packetDir):{result:summary,reportFailed:false};
      console.log(JSON.stringify(output.result,null,2));
      process.exitCode=output.reportFailed?1:['ACCEPTED','PROBED','READY_FOR_OWNER','DONE','QUOTA_DRILL_PASS'].includes(result.status)?0:1;
    }
  }
} catch(error){console.error(redactText(JSON.stringify(error?.harness ?? {code:error?.code??'INTERNAL_ERROR',message:error?.message??String(error),timestamp:new Date().toISOString()})));process.exitCode=1;}
