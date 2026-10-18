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
  const run = (extra=[]) => spawnSync(process.execPath,[path.join(__dirname,'stage-site.js'),'--updater',updater,'--out',output,...(extra.includes('--native-release') ? [] : ['--native-release',path.join(dir,'missing-native.json')]),...extra],{encoding:'utf8'});
  return {updater,output,run};
}
test('stages only current signed artifacts and approved site assets without changing bytes', t => {
  const f = fixture(t); const result = f.run(); assert.equal(result.status,0,result.stderr);
  for (const name of ['updates.xri',currentPackage,'LICENSE']) assert.deepEqual(fs.readFileSync(path.join(f.output,name)),fs.readFileSync(path.join(f.updater,name)));
  assert.equal(fs.existsSync(path.join(f.output,'old.zip')),false);
  assert.equal(fs.existsSync(path.join(f.output,'secret.rs')),false);
  assert.equal(fs.existsSync(path.join(f.output,'pixinsight')),false);
  assert.ok(fs.existsSync(path.join(f.output,'assets/loom.png')));
  for(const name of ['fly-through-iris.jpg','fly-through-elephant.jpg'])assert.deepEqual(fs.readFileSync(path.join(f.output,'assets',name)),fs.readFileSync(path.join(root,'website/assets',name)));
  const html=fs.readFileSync(path.join(f.output,'index.html'),'utf8');
  assert.ok(html.includes('href="pixinsight.html"'));
  assert.ok(!html.includes('Install Loom<br>in PixInsight.'));
  assert.ok(!html.includes('Manage Repositories'));
  assert.ok(!html.includes('{{'));
  const instructions=fs.readFileSync(path.join(f.output,'pixinsight.html'),'utf8');
  assert.ok(instructions.includes('Install Loom<br>in PixInsight.'));
  assert.ok(instructions.includes('https://fcarucci.github.io/Loom/'));
  assert.ok(instructions.includes('https://fcarucci.github.io/Loom/pixinsight/'));
  assert.ok(instructions.includes('href="'+currentPackage+'"'));
  assert.ok(!instructions.includes('{{'));
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

function nativeFixture(t) {
 const f=fixture(t);const modules=path.join(path.dirname(f.updater),'modules');fs.mkdirSync(modules);
 const zip=spawnSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('bin/Loom-pxm.dylib',b'module'); z.writestr('bin/Loom-pxm.xsgn',b'signature'); z.close()",path.join(modules,'mac.zip')]);assert.equal(zip.status,0);
 const bytes=fs.readFileSync(path.join(modules,'mac.zip'));const sha1=require('node:crypto').createHash('sha1').update(bytes).digest('hex');
 const platform=`<platform os="macosx" arch="arm64" version="1.9.5:1.9.99"><package fileName="mac.zip" sha1="${sha1}" type="module"/></platform>`;
 const manifest=`<xri version="1.0">${platform}</xri>\n<Signature developerId="fixture">fixture-signature</Signature>`;
 fs.writeFileSync(path.join(modules,'updates.xri'),manifest);return {...f,modules,manifest,platform};
}
test('persists a native feed under pixinsight without changing root script artifacts', t=> {
 const f=nativeFixture(t);fs.writeFileSync(path.join(f.modules,'private.rs'),'private');
 const result=f.run(['--modules-repo',f.modules]);assert.equal(result.status,0,result.stderr);
 for(const name of ['updates.xri','mac.zip'])assert.deepEqual(fs.readFileSync(path.join(f.output,'pixinsight',name)),fs.readFileSync(path.join(f.modules,name)));
 assert.equal(fs.existsSync(path.join(f.output,'pixinsight/private.rs')),false);
 assert.deepEqual(fs.readFileSync(path.join(f.output,'updates.xri')),fs.readFileSync(path.join(f.updater,'updates.xri')));
 assert.equal(f.run(['--modules-repo',f.modules]).status,0);
 assert.notEqual(f.run().status,0,'a later deploy cannot silently drop an existing native feed');
});
test('rejects corrupt modules, duplicate module identities, traversal and unsigned native feed before writes', t=> {
 for(const kind of ['corrupt','duplicate','traversal','unsigned']){const f=nativeFixture(t);
 if(kind==='corrupt')fs.appendFileSync(path.join(f.modules,'mac.zip'),'broken');
 else fs.writeFileSync(path.join(f.modules,'updates.xri'),kind==='duplicate'?f.manifest.replace('</xri>',f.platform+'</xri>'):kind==='traversal'?f.manifest.replace('mac.zip','../mac.zip'):f.manifest.split('<Signature')[0]);
 const result=f.run(['--modules-repo',f.modules]);assert.notEqual(result.status,0,kind);assert.equal(fs.existsSync(f.output),false,kind);}
});
test('missing native metadata allows existing script-only publication; configured failures stop it', t=> {
 const f=fixture(t);const config=path.join(path.dirname(f.updater),'native-release.json');
 assert.equal(f.run(['--native-release',config]).status,0);
 fs.writeFileSync(config,'{}');assert.equal(f.run(['--native-release',config]).status,0);
 fs.writeFileSync(config,'{"schemaVersion":1,"assets":[{"fileName":"updates.xri"}]}');
 const other=fixture(t);const result=other.run(['--native-release',config]);assert.notEqual(result.status,0);assert.equal(fs.existsSync(other.output),false);
});
test('pinned asset downloader checks SHA256 and exact compressed size, rejects unexpected files and URLs',async t=>{
 const {downloadNativeRelease}=require('./stage-site.js');
 const body=Buffer.from('signed bytes');const crypto=require('node:crypto');const asset={fileName:'updates.xri',url:'https://github.com/fcarucci/Loom/releases/download/native-test/updates.xri',sha256:crypto.createHash('sha256').update(body).digest('hex'),size:body.length};
 const fetchImpl=async()=>({ok:true,arrayBuffer:async()=>body});
 const result=await downloadNativeRelease({schemaVersion:1,assets:[asset]},fetchImpl);assert.deepEqual(result.get('updates.xri'),body);
 for(const patch of [{size:body.length+1},{sha256:'0'.repeat(64)},{fileName:'engine.rs'},{url:'https://example.com/private.zip'}])await assert.rejects(downloadNativeRelease({schemaVersion:1,assets:[{...asset,...patch}]},fetchImpl));
 await assert.rejects(downloadNativeRelease({schemaVersion:1,assets:[asset]},async()=>({ok:false,status:404})));
});

test('stages combined platform feed and pinned immutable assets on every fresh deployment',async t=>{
 const f=nativeFixture(t);const windows='windows.zip';
 const generated=spawnSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('bin/Loom-pxm.dll',b'windows module'); z.writestr('bin/Loom-pxm.xsgn',b'signature'); z.close()",path.join(f.modules,windows)]);assert.equal(generated.status,0);
 const crypto=require('node:crypto');const bytes=fs.readFileSync(path.join(f.modules,windows));
 const entry=`<platform os="windows" arch="x64" version="1.9.5:1.9.99"><package fileName="${windows}" sha1="${crypto.createHash('sha1').update(bytes).digest('hex')}" type="module"/></platform>`;
 fs.writeFileSync(path.join(f.modules,'updates.xri'),f.manifest.replace('</xri>',entry+'</xri>'));
 const assets=['updates.xri','mac.zip',windows].map(fileName=>{const body=fs.readFileSync(path.join(f.modules,fileName));return {fileName,url:'https://github.com/fcarucci/Loom/releases/download/native-fixture/'+fileName,sha256:crypto.createHash('sha256').update(body).digest('hex'),size:body.length};});
 const config=path.join(path.dirname(f.updater),'native-release.json');fs.writeFileSync(config,JSON.stringify({schemaVersion:1,assets}));
 const {stageSite}=require('./stage-site.js');const fetchImpl=async url=>({ok:true,arrayBuffer:async()=>fs.readFileSync(path.join(f.modules,new URL(url).pathname.split('/').pop()))});
 for(const suffix of ['first','future']){
  const out=f.output+'-'+suffix;const cache=f.output+'-cache-'+suffix;
  await stageSite({updater:f.updater,out,nativeRelease:config,modulesCache:cache,fetchImpl});
  for(const name of ['updates.xri','mac.zip',windows]){assert.deepEqual(fs.readFileSync(path.join(out,'pixinsight',name)),fs.readFileSync(path.join(f.modules,name)));assert.deepEqual(fs.readFileSync(path.join(cache,name)),fs.readFileSync(path.join(f.modules,name)));}
  for(const name of ['updates.xri',currentPackage])assert.deepEqual(fs.readFileSync(path.join(out,name)),fs.readFileSync(path.join(f.updater,name)));
 }
 assets[1].size++;fs.writeFileSync(config,JSON.stringify({schemaVersion:1,assets}));
 await assert.rejects(stageSite({updater:f.updater,out:f.output,nativeRelease:config,fetchImpl}),/checksum or size mismatch/);assert.equal(fs.existsSync(f.output),false);
});
test('rejects a checksum-valid native ZIP containing private source',t=>{
 const f=nativeFixture(t);const zipPath=path.join(f.modules,'mac.zip');
 const generated=spawnSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'a'); z.writestr('src/engine.rs',b'private'); z.close()",zipPath]);assert.equal(generated.status,0);
 const sha1=require('node:crypto').createHash('sha1').update(fs.readFileSync(zipPath)).digest('hex');fs.writeFileSync(path.join(f.modules,'updates.xri'),f.manifest.replace(/sha1="[^"]+"/,`sha1="${sha1}"`));
 const result=f.run(['--modules-repo',f.modules]);assert.notEqual(result.status,0);assert.match(result.stderr,/unapproved native ZIP file/);assert.equal(fs.existsSync(f.output),false);
});
test('preserves distinct modules on the same platform, including repeated platform blocks',t=>{
 for(const grouped of [true,false]) {
  const f=nativeFixture(t);const other=path.join(f.modules,'frame-selector.zip');
  const generated=spawnSync('python3',['-c',"import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('bin/LoomFrameSelector-pxm.dylib',b'other module'); z.writestr('bin/LoomFrameSelector-pxm.xsgn',b'signature'); z.close()",other]);assert.equal(generated.status,0);
  const digest=require('node:crypto').createHash('sha1').update(fs.readFileSync(other)).digest('hex');
  const entry=`<package fileName="frame-selector.zip" sha1="${digest}" type="module"/>`;
  const xml=grouped?f.manifest.replace('</platform>',entry+'</platform>'):f.manifest.replace('</xri>',`<platform os="macosx" arch="arm64" version="1.9.5:1.9.99">${entry}</platform></xri>`);
  fs.writeFileSync(path.join(f.modules,'updates.xri'),xml);
  const result=f.run(['--modules-repo',f.modules]);assert.equal(result.status,0,result.stderr);
  for(const name of ['mac.zip','frame-selector.zip','updates.xri'])assert.deepEqual(fs.readFileSync(path.join(f.output,'pixinsight',name)),fs.readFileSync(path.join(f.modules,name)));
 }
});
test('rejects a second current package for the same module under a different ZIP name',t=>{
 const f=nativeFixture(t);fs.copyFileSync(path.join(f.modules,'mac.zip'),path.join(f.modules,'new-mac.zip'));
 fs.writeFileSync(path.join(f.modules,'updates.xri'),f.manifest.replace('</xri>',f.platform.replace('mac.zip','new-mac.zip')+'</xri>'));
 const result=f.run(['--modules-repo',f.modules]);assert.notEqual(result.status,0);assert.match(result.stderr,/Duplicate native module/);assert.equal(fs.existsSync(f.output),false);
});
