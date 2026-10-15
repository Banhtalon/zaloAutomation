import path from 'node:path';
import {lstatSync, readFileSync} from 'node:fs';

export function readSingleFileProductEvidence(packetDir, task) {
  const read = name => {
    try {
      const file = path.join(packetDir, name);
      const info = lstatSync(file);
      if (!info.isFile() || info.isSymbolicLink() || info.size > 2 * 1024 * 1024) return {invalid: true};
      return {value: JSON.parse(readFileSync(file, 'utf8')), invalid: false};
    } catch (error) {
      return error.code === 'ENOENT' ? {missing: true} : {invalid: true};
    }
  };
  const official = read('product_check.json');
  const legacy = read('ui_evidence.json');
  const value = official.value ?? null;
  const identityMismatch = task && (value?.task_id !== task.task_id || value?.revision !== task.revision ||
    value?.head !== task.candidate_head || (value?.candidate_head !== undefined && value.candidate_head !== task.candidate_head) ||
    value?.contract_sha256 !== task.contract_sha256);
  return {value, invalid: !!official.invalid || !!official.missing || !legacy.missing || !!identityMismatch};
}
