'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const currentPackage = fs.readFileSync(path.join(root,'pixinsight/repo/updates.xri'),'utf8').match(/fileName="([^"]+)"/)[1];
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'loom-site-'));
  t.after(() => fs.rmSync(dir, {recursive:true, force:true}));
  const updater = path.join(dir, 'updater'); const output = path.join(dir, 'public');
  fs.mkdirSync(updater);
  for (const name of ['updates.xri', 'LICENSE', currentPackage]) fs.copyFileSync(path.join(root,'pixinsight/repo',name), path.join(updater,name));
  fs.writeFileSync(path.join(updater,'old.zip'), 'historical');
  fs.writeFileSync(path.join(updater,'secret.rs'), 'private source');
  const run = () => spawnSync(process.execPath,[path.join(__dirname,'stage-site.js'),'--updater',updater,'--out',output],{encoding:'utf8'});
  return {updater,output,run};
}
test('stages only current signed artifacts and approved site assets without changing bytes', t => {
  const f = fixture(t); const result = f.run(); assert.equal(result.status,0,result.stderr);
  for (const name of ['updates.xri',currentPackage,'LICENSE']) assert.deepEqual(fs.readFileSync(path.join(f.output,name)),fs.readFileSync(path.join(f.updater,name)));
  assert.equal(fs.existsSync(path.join(f.output,'old.zip')),false);
  assert.equal(fs.existsSync(path.join(f.output,'secret.rs')),false);
  assert.equal(fs.existsSync(path.join(f.output,'pixinsight')),false);
  assert.ok(fs.existsSync(path.join(f.output,'assets/loom.png')));
  const html=fs.readFileSync(path.join(f.output,'index.html'),'utf8');
  assert.ok(html.includes('href="'+currentPackage+'"')); assert.ok(!html.includes('{{'));
});
test('fails closed on a damaged archive before creating public output', t => {
  const f=fixture(t); fs.appendFileSync(path.join(f.updater,currentPackage),'damage');
  const result=f.run(); assert.notEqual(result.status,0); assert.match(result.stderr,/Archive checksum mismatch/); assert.equal(fs.existsSync(f.output),false);
});
test('fails closed on unsigned manifest and unsafe package paths', t => {
  for (const unsafe of [false,true]) {const f=fixture(t); let xml=fs.readFileSync(path.join(f.updater,'updates.xri'),'utf8');
    xml=unsafe ? xml.replace(currentPackage,'../private.zip') : xml.slice(0,xml.indexOf('<Signature'));
    fs.writeFileSync(path.join(f.updater,'updates.xri'),xml); const result=f.run(); assert.notEqual(result.status,0); assert.match(result.stderr,unsafe ? /Unsafe package/ : /signed manifest/); assert.equal(fs.existsSync(f.output),false);}
});
test('refuses an output containing unapproved files', t=> {const f=fixture(t); fs.mkdirSync(f.output);fs.writeFileSync(path.join(f.output,'private.rs'),'secret');const result=f.run();assert.notEqual(result.status,0);assert.match(result.stderr,/Unapproved output/);assert.equal(fs.readFileSync(path.join(f.output,'private.rs'),'utf8'),'secret');});
test('fails closed on malformed XML even when package attributes remain readable', t=> {
 const f=fixture(t); const name=path.join(f.updater,'updates.xri'); fs.writeFileSync(name,fs.readFileSync(name,'utf8').replace('</platform>','</wrong>'));
 const result=f.run();assert.notEqual(result.status,0);assert.match(result.stderr,/Invalid manifest XML/);assert.equal(fs.existsSync(f.output),false);
});
