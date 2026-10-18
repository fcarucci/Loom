#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');

// Only a proven, nonempty website/** diff can bypass script tests/package.
// Workflow edits, CI helpers, mixed paths and unavailable history run full CI.
function detectCodeChanges({eventName, refType, event = {}, head, cwd = process.cwd()}) {
  if (refType === 'tag' || eventName === 'workflow_dispatch') return {code:true, reason:'release or manual run'};
  let base;
  if (eventName === 'pull_request') {
    base = event.pull_request?.base?.sha;
    head = event.pull_request?.head?.sha;
  } else if (eventName === 'push') {
    base = event.before;
  } else return {code:true, reason:'unknown event'};
  const valid = sha => typeof sha === 'string' && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(sha) && !/^0+$/.test(sha);
  if (!valid(base) || !valid(head)) return {code:true, reason:'unknown diff endpoints'};
  // --no-renames keeps both sides of a rename. Moving a script into website/
  // must still run script checks because the old script path was removed.
  const diff = spawnSync('git', ['diff','--name-only','--no-renames','-z',base,head,'--'], {cwd, encoding:'utf8', maxBuffer:8*1024*1024});
  if (diff.status !== 0) return {code:true, reason:'diff unavailable'};
  const files = diff.stdout.split('\0').filter(Boolean);
  const websiteOnly = files.length > 0 && files.every(file => file.startsWith('website/'));
  return {code:!websiteOnly, reason:websiteOnly ? 'website-only diff' : 'code, mixed, or empty diff'};
}
if (require.main === module) {
  let result;
  try {
    result = detectCodeChanges({eventName:process.env.GITHUB_EVENT_NAME, refType:process.env.GITHUB_REF_TYPE, head:process.env.GITHUB_SHA, event:JSON.parse(fs.readFileSync(process.env.GITHUB_EVENT_PATH, 'utf8'))});
  } catch { result = {code:true, reason:'event data unavailable'}; }
  console.log('Script checks: ' + result.code + ' (' + result.reason + ')');
  if (!process.env.GITHUB_OUTPUT) throw Error('GITHUB_OUTPUT is required');
  fs.appendFileSync(process.env.GITHUB_OUTPUT, 'code=' + result.code + '\n');
}
module.exports = {detectCodeChanges};
