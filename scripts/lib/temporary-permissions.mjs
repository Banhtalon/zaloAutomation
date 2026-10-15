import path from 'node:path';
import { lstat, readFile, realpath, writeFile } from 'node:fs/promises';
import { harnessError } from './harness-errors.mjs';

const WILDCARD = /[*?\[\]{}]/;

function error(code, message, details = {}) {
  return harnessError(code, message, { retryable: false, details });
}

function samePath(left, right) {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function under(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function assertAbsolute(value, label) {
  if (typeof value !== 'string' || !value.trim() || !path.isAbsolute(value) || /[\0\r\n]/.test(value)) {
    throw error('INVALID_INPUT', `${label} must be a non-empty absolute path`);
  }
  if (value.startsWith('\\\\') || value.startsWith('//') || WILDCARD.test(value) || value.split(/[\\/]+/).includes('..')) {
    throw error('INVALID_INPUT', `${label} contains an unsupported path form`);
  }
  return path.resolve(value);
}

async function canonicalDirectory(value, label, code = 'WORKTREE_ROOT_INVALID') {
  const lexical = assertAbsolute(value, label);
  let info;
  try { info = await lstat(lexical); }
  catch (cause) { throw error(code, `${label} is unavailable`, { cause }); }
  if (!info.isDirectory() || info.isSymbolicLink()) throw error(code, `${label} must be a real directory`);
  let canonical;
  try { canonical = await realpath(lexical); }
  catch (cause) { throw error(code, `${label} cannot be resolved`, { cause }); }
  if (!samePath(canonical, lexical)) throw error(code, `${label} must not be a junction or symlink`);
  return lexical;
}

async function validateFile(value, workspaceRoot) {
  const lexical = assertAbsolute(value, 'permission file');
  if (!under(workspaceRoot, lexical) || samePath(workspaceRoot, lexical)) {
    throw error('SCOPE_VIOLATION', 'permission file must be a specific file inside the task workspace');
  }

  await canonicalDirectory(path.dirname(lexical), 'permission file parent');
  try {
    const info = await lstat(lexical);
    if (!info.isFile() || info.isSymbolicLink()) throw error('SCOPE_VIOLATION', 'permission file must not be a directory, junction or symlink');
    const canonical = await realpath(lexical);
    if (!samePath(canonical, lexical)) throw error('SCOPE_VIOLATION', 'permission file must not resolve through a junction or symlink');
  } catch (cause) {
    if (cause?.code !== 'ENOENT') throw cause;
  }
  return lexical;
}

async function validateFiles(workspaceRoot, files) {
  if (!Array.isArray(files) || files.length === 0) throw error('INVALID_INPUT', 'an approved non-empty file scope is required');
  const unique = new Map();
  for (const file of files) {
    const validated = await validateFile(file, workspaceRoot);
    const key = process.platform === 'win32' ? validated.toLowerCase() : validated;
    unique.set(key, validated);
  }
  return [...unique.values()];
}

function parseSettings(bytes) {
  let settings;
  try { settings = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, '')); }
  catch (cause) { throw error('INVALID_CONFIG', 'settings file is not valid JSON', { cause }); }
  if (!settings || typeof settings !== 'object' || Array.isArray(settings) ||
      !settings.permissions || typeof settings.permissions !== 'object' || Array.isArray(settings.permissions) ||
      !Array.isArray(settings.permissions.allow) || !settings.permissions.allow.every(rule => typeof rule === 'string')) {
    throw error('INVALID_CONFIG', 'settings.permissions.allow must be an array of rules');
  }
  return settings;
}

function encodeSettings(settings, bom) {
  const body = Buffer.from(`${JSON.stringify(settings, null, 2)}\n`, 'utf8');
  return bom ? Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), body]) : body;
}

async function readSettings(settingsPath) {
  const absolute = assertAbsolute(settingsPath, 'settingsPath');
  await canonicalDirectory(path.dirname(absolute), 'settings directory', 'INVALID_CONFIG');
  let info;
  try { info = await lstat(absolute); }
  catch (cause) { throw error('INVALID_CONFIG', 'settings file is unavailable', { cause }); }
  if (!info.isFile() || info.isSymbolicLink()) throw error('INVALID_CONFIG', 'settingsPath must point to a real file');
  const bytes = await readFile(absolute);
  return { path: absolute, bytes, settings: parseSettings(bytes), bom: bytes.slice(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])) };
}

function ruleFor(file) {
  return `write_file(${file})`;
}

async function removeTemporaryRules(snapshot, grantedBytes, addedRules) {
  let current;
  try { current = await readSettings(snapshot.path); }
  catch (cause) {
    throw error('CONTROL_STATE_MUTATED', 'settings changed to an unsafe format; temporary permission was not declared cleaned up', { cause });
  }

  if (current.bytes.equals(grantedBytes)) {
    try { await writeFile(snapshot.path, snapshot.bytes); }
    catch (cause) { throw error('CONTROL_STATE_MUTATED', 'temporary permission cleanup could not restore settings safely', { cause }); }
    return;
  }

  if (addedRules.some(rule => current.settings.permissions.allow.includes(rule))) {
    throw error('CONTROL_STATE_MUTATED', 'settings changed while temporary permission ownership could not be verified');
  }
}

export async function withTemporaryWritePermissions({ settingsPath, workspaceRoot, files } = {}, callback) {
  if (typeof callback !== 'function') throw error('INVALID_INPUT', 'temporary permission callback is required');
  const root = await canonicalDirectory(workspaceRoot, 'workspaceRoot');
  const validatedFiles = await validateFiles(root, files);
  const snapshot = await readSettings(settingsPath);
  const rules = validatedFiles.map(ruleFor);
  const existing = new Set(snapshot.settings.permissions.allow);
  const addedRules = rules.filter(rule => !existing.has(rule));

  if (addedRules.length === 0) return callback({ files: validatedFiles, addedRules: [] });

  const current = await readSettings(snapshot.path);
  if (!current.bytes.equals(snapshot.bytes)) {
    throw error('CONTROL_STATE_MUTATED', 'settings changed before temporary permission grant; refusing to overwrite it');
  }
  const granted = {
    ...snapshot.settings,
    permissions: { ...snapshot.settings.permissions, allow: [...snapshot.settings.permissions.allow, ...addedRules] }
  };
  const grantedBytes = encodeSettings(granted, snapshot.bom);
  await writeFile(snapshot.path, grantedBytes);

  let callbackError;
  try {
    return await callback({ files: validatedFiles, addedRules });
  } catch (cause) {
    callbackError = cause;
    throw cause;
  } finally {
    // Forced termination skips finally; callers must reconcile settings and verify
    // no temporary permission remains before reusing the workspace.
    try {
      await removeTemporaryRules(snapshot, grantedBytes, addedRules);
    } catch (cleanupError) {
      if (callbackError) cleanupError.cause = callbackError;
      throw cleanupError;
    }
  }
}
