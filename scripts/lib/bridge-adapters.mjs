import path from 'node:path';
import {writeFile,mkdir,readFile,lstat,readdir} from 'node:fs/promises';
import {randomUUID,createHash} from 'node:crypto';
import os from 'node:os';
import {execute,subscriptionEnv,failureReason,failureStatus,safe} from './bridge-process.mjs';
import {beginInvocation,finishInvocation} from './receipts.mjs';

export const resultSchema={type:'object',additionalProperties:false,required:['verdict','summary','material_findings','risk_checks_completed'],properties:{verdict:{type:'string',enum:['PASS','NEEDS_FIX','BLOCKED']},summary:{type:'string'},material_findings:{type:'array',items:{type:'string'}},risk_checks_completed:{type:'boolean'}}};
const GEMINI_REVIEWER_MODEL='gemini-3.8-flash-high';
const AGENT_NAME=/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const DEFINITION_HASH=/^[a-f0-9]{64}$/i;
const FRONTMATTER_KEYS=new Set(['name','description','tools','excludeDefaultComponents','inheritCustomizations','mainAgent','subagent','model','commandExecutionPolicy','mcpServers']);
const TOOL_EVENT_NAMES=new Set(['tool','tool_call','tool_result','function_call','function_response','view_file','read_file','write_file','edit_file','shell','run_shell_command','browser','mcp']);
const definitionHash=bytes=>createHash('sha256').update(bytes).digest('hex');
const agentDefinitionPath=agent=>path.join(os.homedir(),'.gemini','config','agents',agent,'agent.md');
const protocolError=(code,message)=>Object.assign(Error(message),{code});

export async function codexSessionIdentity(sessionId,sessionsRoot=path.join(os.homedir(),'.codex','sessions')) {
  if(!/^[0-9a-f-]{36}$/i.test(sessionId??''))return null;
  const entries=await readdir(sessionsRoot,{recursive:true,withFileTypes:true});
  const matches=entries.filter(entry=>entry.isFile()&&entry.name.endsWith(`-${sessionId}.jsonl`));
  if(matches.length!==1)return null;
  const rows=(await readFile(path.join(matches[0].parentPath,matches[0].name),'utf8')).split(/\r?\n/).filter(Boolean);
  const contexts=rows.flatMap(line=>{try{const row=JSON.parse(line);return row.type==='turn_context'&&row.payload?.model?[row.payload]:[];}catch{return [];}});
  const models=[...new Set(contexts.map(row=>row.model))],efforts=[...new Set(contexts.map(row=>row.effort).filter(Boolean))];
  return models.length===1&&efforts.length===1?{model:models[0],effort:efforts[0]}:null;
}

export function validateNoToolsAgentDefinition(source,agent) {
  if(typeof source!=='string'||!AGENT_NAME.test(agent??''))return false;
  const match=source.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if(!match)return false;
  const fields=new Map();
  for(const line of match[1].split(/\r?\n/)){
    if(!line.trim()||line.trimStart().startsWith('#'))continue;
    const entry=line.match(/^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(\S.*)$/);
    if(!entry||!FRONTMATTER_KEYS.has(entry[1])||fields.has(entry[1]))return false;
    fields.set(entry[1],entry[2]);
  }
  const required=['name','tools','excludeDefaultComponents','inheritCustomizations','mainAgent','subagent','commandExecutionPolicy','mcpServers'];
  return required.every(key=>fields.has(key))&&fields.get('name')===agent&&fields.get('tools')==='[]'&&
    fields.get('excludeDefaultComponents')==='true'&&fields.get('inheritCustomizations')==='false'&&
    fields.get('mainAgent')==='true'&&fields.get('subagent')==='false'&&
    fields.get('commandExecutionPolicy')==='off'&&fields.get('mcpServers')==='[]';
}

export function validateReviewerBinding(binding) {
  const b=validateBinding(binding);
  if(b.provider==='google'&&b.cli==='antigravity') {
    if(b.model!==GEMINI_REVIEWER_MODEL)throw Error(`Antigravity reviewer model must be '${GEMINI_REVIEWER_MODEL}'`);
    if(b.effort!=null)throw Error('Antigravity reviewer does not accept an effort override');
    if(!AGENT_NAME.test(b.agent??''))throw Error('Antigravity reviewer requires a safe agent name');
    if(!DEFINITION_HASH.test(b.agent_definition_sha256??''))throw Error('Antigravity reviewer requires agent_definition_sha256');
  }
  return b;
}

async function verifyAntigravityReviewerAgent(binding) {
  const file=agentDefinitionPath(binding.agent);
  const info=await lstat(file);
  if(!info.isFile()||info.isSymbolicLink())throw Error('Antigravity reviewer agent definition must be a regular file');
  const bytes=await readFile(file);
  if(definitionHash(bytes).toLowerCase()!==binding.agent_definition_sha256.toLowerCase())throw Error('Antigravity reviewer agent definition hash mismatch');
  if(!validateNoToolsAgentDefinition(bytes.toString('utf8'),binding.agent))throw Error('Antigravity reviewer agent must explicitly disable tools and customizations');
  return {path:file,sha256:definitionHash(bytes)};
}

export function antigravityReviewerArgs(binding,prompt,timeoutSeconds=300) {
  validateReviewerBinding(binding);
  if(typeof prompt!=='string')throw Error('Antigravity reviewer prompt must be a string');
  const seconds=Number.isFinite(timeoutSeconds)&&timeoutSeconds>0?timeoutSeconds:300;
  return [...binding.command,'--agent',binding.agent,'--model',binding.model,'--sandbox','--mode','plan','--disable-slash-commands','--input-format','stream-json','--output-format','stream-json','--print-timeout',`${seconds}s`];
}

export function antigravityReviewerInput(prompt) {
  if(typeof prompt!=='string')throw Error('Antigravity reviewer prompt must be a string');
  return JSON.stringify({event:'user',message:{content:prompt}})+'\n';
}

export function validateBinding(b) {
  if(!b||!['openai','google'].includes(b.provider)||!Array.isArray(b.command)||!b.command.length||b.command.some(x=>typeof x!=='string'||!x)||!/^[-a-zA-Z0-9_.]+$/.test(b.model??''))throw Error('invalid CLI binding');
  if(b.command.length>2||(b.command.length===2&&(!/node(?:\.exe)?$/i.test(b.command[0])||! /\.(?:mjs|cjs|js)$/i.test(b.command[1]))))throw Error('binding must be executable or node entry point');
  if(b.effort!=null&&!['low','medium','high','xhigh','max','ultra'].includes(b.effort))throw Error('invalid effort');
  if(b.provider==='google'&&b.cli!=null&&!['gemini','antigravity'].includes(b.cli))throw Error('invalid Google CLI');
  if(b.provider==='openai'&&b.cli!=null&&b.cli!=='codex')throw Error('invalid OpenAI CLI');
  return b;
}

export async function invocation(binding,{cwd,packetDir,role,prompt,timeoutSeconds}) {
  const b=validateBinding(binding),env=subscriptionEnv();await mkdir(packetDir,{recursive:true});
  if(b.provider==='openai'){
    const schema=path.join(packetDir,'result-schema.json');await writeFile(schema,JSON.stringify(resultSchema));
    const args=[...b.command,'exec','--ignore-user-config','-c','forced_login_method="chatgpt"','-c','model_provider="openai"','-c','approval_policy="never"','-m',b.model,'-s',role==='worker'||role==='senior'?'workspace-write':'read-only','--json','--output-schema',schema,'-C',cwd];
    if(b.effort)args.push('-c',`model_reasoning_effort="${b.effort}"`);args.push('-');return {argv:args,env,input:prompt};
  }
  if(b.cli==='antigravity'&&((role==='reviewer'||role==='elevated_reviewer')||(role==='probe'&&b.agent))) {
    const settingsPath=path.join(os.homedir(),'.gemini','antigravity-cli','settings.json');
    const settings=JSON.parse(await readFile(settingsPath,'utf8'));assertSubscriptionSettings(settings);
    validateReviewerBinding(b);await verifyAntigravityReviewerAgent(b);
    return {argv:antigravityReviewerArgs(b,prompt,timeoutSeconds),env,input:antigravityReviewerInput(prompt)};
  }
  if(b.cli!=='gemini'){
    const settingsPath=path.join(os.homedir(),'.gemini','antigravity-cli','settings.json');const settings=JSON.parse(await readFile(settingsPath,'utf8'));assertSubscriptionSettings(settings);
    const schema=path.join(packetDir,'result-schema.json');await writeFile(schema,JSON.stringify(resultSchema));
    return {argv:[...b.command,'--add-dir',cwd,'--input-format','stream-json','--output-format','stream-json','--json-schema',schema,'--disable-slash-commands','--model',b.model,...(role==='worker'||role==='senior'?['--mode','accept-edits']:[])],env,input:JSON.stringify({event:'user',message:{content:`The task repository is ${cwd}. Work only in this directory, not the default CLI scratch directory. Use built-in file tools; do not invoke shell commands or delegate. The Lead runs git and gates.\n${prompt}`}})+'\n'};
  }
  const settings=path.join(packetDir,'gemini-subscription.json');await writeFile(settings,JSON.stringify({security:{auth:{selectedType:'oauth-personal',enforcedType:'oauth-personal'},enablePermanentToolApproval:false},general:{enableAutoUpdate:false},tools:{autoAccept:false},mcpServers:{}}));env.GEMINI_CLI_SYSTEM_SETTINGS_PATH=settings;
  return {argv:[...b.command,'--model',b.model,'--approval-mode',role==='worker'||role==='senior'?'auto_edit':'plan','--output-format','json','--extensions','none','--prompt','Follow the task supplied on stdin.'],env,input:prompt};
}

export function assertSubscriptionSettings(settings){
  if(!settings||typeof settings!=='object'||Array.isArray(settings))throw Error('Antigravity requires a JSON object for subscription settings');
  if(Object.hasOwn(settings,'useG1Credits')&&settings.useG1Credits!==false)throw Error('Antigravity requires useG1Credits=false when present; missing uses the documented default');
  for(const key of ['modelProvider','apiKey','apiKeyEnv','baseUrl','endpoint'])if(Object.hasOwn(settings,key))throw Error(`Antigravity forbids explicit ${key}; use the default subscription account`);
}

export function protocolMetadata(provider,stdout,cli) {
  const events=stdout.trim().split(/\r?\n/).flatMap(line=>{try{return [JSON.parse(line)];}catch{return [];}});const session=provider==='openai'?events.find(e=>e.type==='thread.started')?.thread_id:cli==='antigravity'?events.find(e=>e.event==='result')?.result?.conversation_id??events.find(e=>e.event==='init')?.conversation_id:events[0]?.session_id;const denied=cli==='antigravity'?events.flatMap(e=>e.result?.denied_actions??[]).map(x=>x.action).filter(x=>typeof x==='string'):(Array.isArray(events[0]?.denied_actions)?events[0].denied_actions.map(x=>typeof x==='string'?x:x?.action).filter(Boolean):[]);return safe({...(typeof session==='string'&&session?{session_id:session}:{}),...(denied.length?{denied_actions:denied}:{})});
}

function transientOpenAITransportEvent(event) {if(event.type!=='error')return false;const message=event.message??event.error?.message??'';return /^Reconnecting\.\.\. \d+\/\d+ \(/.test(message)||/^Falling back from WebSockets to HTTPS transport\./.test(message);}
function hasToolEvent(events){return events.some(event=>{
  if(!event||typeof event!=='object'||event.event==='init')return false;
  const name=typeof event.event==='string'?event.event.toLowerCase():'';
  const update=event.event==='step_update'&&event.step_update&&typeof event.step_update==='object'?event.step_update:null;
  return TOOL_EVENT_NAMES.has(name)||/^tool(?:[_-].*)?$/.test(name)||/^function(?:[_-].*)?$/.test(name)||
    Object.hasOwn(event,'tool_name')||Object.hasOwn(event,'tool_call')||Object.hasOwn(event,'tool_result')||
    update?.step_type==='tool'||typeof update?.tool_name==='string';
});}

export function parseProtocol(provider,stdout,cli='gemini',{capabilityProbe=false,expectedModel=null,expectedAgent=null}={}) {
  const parseJson=(text,message)=>{try{return JSON.parse(text);}catch{throw protocolError('INVALID_PROTOCOL',message);}};
  let session,models=[],body,usage=null;
  if(provider==='openai') {const events=stdout.trim().split(/\r?\n/).map(line=>parseJson(line,'invalid protocol JSON'));const starts=events.filter(e=>e.type==='thread.started');const completed=events.findLastIndex(e=>e.type==='turn.completed'),lastTransient=events.findLastIndex(transientOpenAITransportEvent);if(starts.length!==1||completed<0||lastTransient>=completed||events.some(e=>e.type==='turn.failed'||(e.type==='error'&&!transientOpenAITransportEvent(e))))throw protocolError('INVALID_PROTOCOL','incomplete Codex protocol');session=starts[0].thread_id;const messages=events.filter(e=>e.type==='item.completed'&&e.item?.type==='agent_message');body=messages.at(-1)?.item.text;models=[...new Set(events.flatMap(e=>e.model?[e.model]:[]))];usage=events.filter(e=>e.type==='turn.completed').at(-1)?.usage??null;
  } else if(cli==='antigravity') {const events=stdout.trim().split(/\r?\n/).filter(Boolean).map(line=>parseJson(line,'invalid protocol JSON'));const results=events.filter(e=>e.event==='result');if(results.length!==1)throw protocolError('INVALID_PROTOCOL','incomplete Antigravity protocol');const response=results[0].result;if(response?.status==='ERROR'){const errText=typeof response.error==='string'?response.error:(typeof response.error?.message==='string'?response.error.message:JSON.stringify(response.error??''));if(/quota|rate.?limit|usage limit|resource.exhausted|\b429\b/i.test(errText)&&typeof response.conversation_id==='string'&&response.conversation_id.trim())return safe({session_id:response.conversation_id,status:'WAITING_QUOTA',reason:'RESOURCE_EXHAUSTED'});throw protocolError('INVALID_PROTOCOL','incomplete Antigravity protocol');}if(response?.status!=='SUCCESS')throw protocolError('INVALID_PROTOCOL','incomplete Antigravity protocol');const initEvents=events.filter(e=>e.event==='init'&&e.init&&typeof e.init==='object');models=[...new Set(initEvents.map(e=>e.init.model).filter(model=>typeof model==='string'&&model.trim()))];const agents=[...new Set(initEvents.map(e=>e.init.agent).filter(agent=>typeof agent==='string'&&agent.trim()))];if(hasToolEvent(events))throw protocolError('PROTOCOL_GUARD','Antigravity reviewer emitted a tool event');if(expectedModel&&(!models.length||models.some(model=>model!==expectedModel)))throw protocolError('PROTOCOL_GUARD',`Antigravity observed model does not match '${expectedModel}'`);if(expectedAgent&&(!agents.length||agents.some(agent=>agent!==expectedAgent)))throw protocolError('PROTOCOL_GUARD',`Antigravity observed agent does not match '${expectedAgent}'`);session=response.conversation_id;usage=response.usage??response.stats??null;body=Object.hasOwn(response,'structured_output')?(typeof response.structured_output==='string'?response.structured_output:JSON.stringify(response.structured_output)):response.response;
  } else {const response=parseJson(stdout,'invalid protocol JSON');if(response.error)throw protocolError('INVALID_PROTOCOL','Gemini protocol error');session=response.session_id;models=Object.keys(response.stats?.models??{});body=response.response;usage=response.usage??response.stats??null;}
  if(expectedModel&&(!models.length||models.some(model=>model!==expectedModel)))throw protocolError('PROTOCOL_GUARD',`observed model does not match '${expectedModel}'`);
  if(typeof session!=='string'||!session.trim()||typeof body!=='string')throw protocolError('INVALID_PROTOCOL','missing session or structured response');const result=parseJson(body.replace(/^```(?:json)?\s*\n?/,'').replace(/\n?```\s*$/,''),'invalid structured result JSON');if(result&&Object.keys(result).some(k=>!['verdict','summary','material_findings','risk_checks_completed'].includes(k)))throw protocolError('INVALID_PROTOCOL','unexpected structured result field');if(!result||!['PASS','NEEDS_FIX','BLOCKED'].includes(result.verdict)||typeof result.summary!=='string'||!Array.isArray(result.material_findings)||!result.material_findings.every(x=>typeof x==='string')||typeof result.risk_checks_completed!=='boolean'||(result.verdict==='PASS'&&result.material_findings.length&&!capabilityProbe))throw protocolError('INVALID_PROTOCOL','invalid structured result');return safe({session_id:session,observed_models:models,usage,result});
}

export async function invoke(binding,options) {
  let spec,started_at,context;const receiptKind=options.receiptKind;
  try {spec=await invocation(binding,options);started_at=new Date().toISOString();context=await beginInvocation({packetDir:options.packetDir,role:options.role,receiptKind,binding,prompt:options.prompt,started_at});}
  catch(error){Object.defineProperty(error,'bridge_phase',{value:'preflight',configurable:true});throw error;}
  let r;
  try {r=await execute(spec.argv,{...spec,cwd:options.cwd,timeoutSeconds:options.timeoutSeconds,signal:options.signal});}
  catch(error){const finished_at=new Date().toISOString();const failed=safe({provider:binding.provider,requested_model:binding.model,requested_effort:binding.effort??null,argv:spec.argv,code:null,reason:error.code?`EXECUTION_THROW:${error.code}`:'EXECUTION_THROW',started_at,finished_at,status:'BLOCKED_TECHNICAL'});await finishInvocation({packetDir:options.packetDir,receiptRoot:options.receiptRoot,role:options.role,receiptKind,binding,prompt:options.prompt,result:failed,started_at,finished_at,context});throw error;}
  const record=safe({provider:binding.provider,requested_model:binding.model,requested_effort:binding.effort??null,argv:spec.argv,code:r.code,reason:failureReason(r),started_at:r.started_at??started_at,finished_at:r.finished_at??new Date().toISOString(),status:failureStatus(r)});Object.assign(record,protocolMetadata(binding.provider,r.stdout,binding.cli??(binding.provider==='openai'?'codex':'antigravity')));
  if(!record.status&&record.denied_actions?.length){record.status='WAITING_CAPABILITY';record.reason='TOOL_PERMISSION_DENIED';}
  if(!record.status){try{const review=['reviewer','elevated_reviewer'].includes(options.role);const parsed=parseProtocol(binding.provider,r.stdout,binding.cli??(binding.provider==='openai'?'codex':'antigravity'),{capabilityProbe:receiptKind==='PROBE',expectedModel:review&&binding.provider!=='openai'?binding.model:null,expectedAgent:review?binding.agent:null});if(review&&binding.provider==='openai'){const identity=await codexSessionIdentity(parsed.session_id);if(identity?.model!==binding.model||identity?.effort!==binding.effort)throw protocolError('PROTOCOL_GUARD',`Codex session identity does not match '${binding.model}' at '${binding.effort}' effort`);parsed.observed_models=[identity.model];}Object.assign(record,parsed);}catch(error){record.status='BLOCKED_TECHNICAL';record.reason=error.code==='PROTOCOL_GUARD'?`PROTOCOL_GUARD:${error.message}`:'INVALID_PROTOCOL';}}
  await finishInvocation({packetDir:options.packetDir,receiptRoot:options.receiptRoot,role:options.role,receiptKind,binding,prompt:options.prompt,result:record,started_at:record.started_at,finished_at:record.finished_at,context});return record;
}

export async function doctor(binding,{cwd,packetDir,probe=false,signal,receiptRoot}={}) {
  validateBinding(binding);const version=await execute([...binding.command,'--version'],{cwd,timeoutSeconds:20,signal});const report={provider:binding.provider,requested_model:binding.model,cli_version:safe(version.stdout.trim()),status:failureStatus(version)??'UNPROBED',auth:'UNVERIFIED',structured_output:false};if(report.status!=='UNPROBED')return report;
  if(binding.provider==='openai'){const auth=await execute([...binding.command,'login','status'],{cwd,timeoutSeconds:20,signal});if(auth.code!==0||! /Logged in using ChatGPT/i.test(auth.stdout+auth.stderr))return {...report,status:'WAITING_CAPABILITY',auth:'CHATGPT_LOGIN_REQUIRED'};report.auth='CHATGPT';}
  if(!probe)return report;const invocationDir=path.join(packetDir,randomUUID());await mkdir(invocationDir,{recursive:true});let result;
  try{result=await invoke(binding,{cwd,packetDir:invocationDir,receiptRoot:receiptRoot??packetDir,role:'probe',receiptKind:'PROBE',timeoutSeconds:90,signal,prompt:'Capability probe. Do not use tools or change files. Reply only with JSON: {"verdict":"PASS","summary":"subscription CLI probe","material_findings":[],"risk_checks_completed":false}'});}
  catch{return {...report,status:'WAITING_CAPABILITY',auth:'SUBSCRIPTION_CONFIGURATION_REQUIRED'};}
  return {...report,status:result.status??'PROBED',auth:result.status?report.auth:binding.provider==='google'?'GOOGLE_OAUTH':'CHATGPT',structured_output:!result.status,execution:result};
}
