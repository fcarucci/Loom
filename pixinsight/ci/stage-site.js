#!/usr/bin/env node
'use strict';
// Stage only explicitly public files. Signed artifacts are never rewritten.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const hash = (algorithm, bytes) => crypto.createHash(algorithm).update(bytes).digest('hex');
const safeZip = name => /^[a-zA-Z0-9][a-zA-Z0-9._-]*\.zip$/.test(name || '');
const escape = s => s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function python(script, input, args = []) {
  const result = spawnSync('python3', ['-c', script, ...args], {input, encoding:'utf8', maxBuffer:4*1024*1024});
  if (result.status !== 0) throw Error((result.stderr || result.error?.message || 'Python 3 unavailable').trim());
  return result.stdout;
}
function parseManifest(manifest) {
  const text = manifest.toString('utf8');
  const end = text.indexOf('</xri>');
  if (end < 0 || !/^\s*<Signature\b[^>]*>[^<]+<\/Signature>\s*$/.test(text.slice(end + 6))) throw Error('A signed manifest is required');
  // PixInsight appends its signature after the XML document. Parse the xri only.
  try {
    return JSON.parse(python(`import sys,json,xml.etree.ElementTree as ET
text=sys.stdin.read()
if '<!DOCTYPE' in text or '<!ENTITY' in text: raise ValueError('XML declarations are not allowed')
root=ET.fromstring(text)
if root.tag!='xri' or root.attrib.get('version')!='1.0': raise ValueError('expected xri version 1.0')
platforms=root.findall('./platform')
packages=[{'platform':p.attrib,'attributes':q.attrib} for p in platforms for q in p.findall('./package')]
if not packages or len(packages)!=len(list(root.iter('package'))): raise ValueError('expected platform/package entries')
if any(not p.findall('./package') for p in platforms): raise ValueError('expected current packages in each platform')
print(json.dumps(packages))`, text.slice(0, end + 6)));
  } catch (error) { throw Error('Invalid manifest XML: ' + error.message); }
}
function checkArchive(attrs, bytes) {
  if (!safeZip(attrs.fileName) || !/^[a-f0-9]{40}$/.test(attrs.sha1 || '')) throw Error('Unsafe package path or checksum');
  if (!bytes || hash('sha1', bytes) !== attrs.sha1) throw Error('Archive checksum mismatch: ' + attrs.fileName);
}
function validateNative(manifest, getBytes) {
  const packages = parseManifest(manifest);
  const identities = new Set();
  const files = new Map([['updates.xri', manifest]]);
  for (const {platform, attributes: attrs} of packages) {
    const {os, arch, version} = platform;
    if (![os,arch].every(v => typeof v === 'string' && /^[a-zA-Z0-9_-]+$/.test(v)) || !version || os === 'all' || arch === 'noarch') throw Error('Invalid native platform');
    if (attrs.type !== 'module') throw Error('Native package must have type module');
    if (!safeZip(attrs.fileName)) throw Error('Unsafe package path');
    if (files.has(attrs.fileName)) throw Error('Native platforms must use unique ZIP names');
    const bytes = getBytes(attrs.fileName);
    checkArchive(attrs, bytes);
    // The manifest format has no size field. Pinned release metadata checks the
    // exact compressed size; this check also validates local verified ZIP inputs.
    if (!bytes.length || bytes.length > 512*1024*1024) throw Error('Invalid native archive size');
    const binary = python(`import sys,io,zipfile,re,stat
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:
 entries=z.infolist()
 names=[i.filename for i in entries]
 if len(names)!=len(set(names)): raise ValueError('duplicate ZIP entries')
 binaries=[]
 for i in entries:
  n=i.filename
  if stat.S_ISLNK(i.external_attr >> 16): raise ValueError('symlink in native ZIP')
  if i.is_dir():
   if n!='bin/': raise ValueError('unapproved native ZIP directory')
   continue
  if re.fullmatch(r'(bin/)?[A-Za-z][A-Za-z0-9_-]*-pxm[.](dylib|dll|so)',n): binaries.append(n)
  elif not re.fullmatch(r'(bin/)?[A-Za-z][A-Za-z0-9_-]*-pxm[.]xsgn',n) and n not in ('LICENSE','LICENSE-PCL.txt','README.md'): raise ValueError('unapproved native ZIP file: '+n)
  if i.file_size>1024*1024*1024: raise ValueError('native ZIP entry too large')
 if len(binaries)!=1: raise ValueError('expected exactly one native binary')
 if re.sub(r'[.](dylib|dll|so)$','.xsgn',binaries[0]) not in names: raise ValueError('native binary signature missing')
 if z.testzip() is not None: raise ValueError('native ZIP CRC mismatch')
 print(re.sub(r'[.](dylib|dll|so)$','',binaries[0].split('/')[-1]))`, bytes).trim();
    const identity = os + '/' + arch + '/' + binary;
    if (identities.has(identity)) throw Error('Duplicate native module: ' + identity);
    identities.add(identity);
    files.set(attrs.fileName, bytes);
  }
  return files;
}
async function downloadNativeRelease(config, fetchImpl = fetch) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw Error('Invalid native release configuration');
  if (Object.keys(config).length === 0) return new Map();
  if (config.schemaVersion !== 1 || !Array.isArray(config.assets)) throw Error('Invalid native release schema');
  if (!config.assets.length) return new Map();
  const seen = new Set();
  let releaseTag;
  // Validate every pin before making any network request.
  for (const asset of config.assets) {
    if (!asset || (asset.fileName !== 'updates.xri' && !safeZip(asset.fileName)) || seen.has(asset.fileName)) throw Error('Invalid or duplicate pinned asset file name');
    seen.add(asset.fileName);
    if (!/^[a-f0-9]{64}$/.test(asset.sha256 || '') || !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > 512*1024*1024) throw Error('Invalid pinned asset checksum or size');
    const url = new URL(asset.url);
    const match = /^\/fcarucci\/Loom\/releases\/download\/([^/]+)\/([^/]+)$/.exec(url.pathname);
    if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port || url.username || url.password || url.search || url.hash || !match || match[2] !== asset.fileName || /latest/i.test(match[1])) throw Error('Native assets require immutable fcarucci/Loom release URLs');
    if (releaseTag && releaseTag !== match[1]) throw Error('Native assets must use one release tag');
    releaseTag = match[1];
  }
  if (!seen.has('updates.xri')) throw Error('Pinned native manifest is required');
  const files = new Map();
  for (const asset of config.assets) {
    const response = await fetchImpl(asset.url, {signal:AbortSignal.timeout(120000)});
    if (!response.ok) throw Error('Native asset download failed: ' + asset.fileName + ' HTTP ' + response.status);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length !== asset.size || hash('sha256', bytes) !== asset.sha256) throw Error('Pinned native asset checksum or size mismatch: ' + asset.fileName);
    files.set(asset.fileName, bytes);
  }
  return files;
}
async function nativeFiles(options) {
  if (options.modulesRepo) {
    const dir = options.modulesRepo;
    const read = name => {
      const target = path.join(dir, name);
      if (!fs.lstatSync(target).isFile()) throw Error('Native input must be a regular file');
      return fs.readFileSync(target);
    };
    return validateNative(read('updates.xri'), read);
  }
  const configPath = options.nativeRelease;
  if (!configPath || !fs.existsSync(configPath)) return new Map();
  const text = fs.readFileSync(configPath, 'utf8').trim();
  if (!text) return new Map();
  const downloaded = await downloadNativeRelease(JSON.parse(text), options.fetchImpl);
  if (!downloaded.size) return downloaded;
  const files = validateNative(downloaded.get('updates.xri'), name => downloaded.get(name));
  if (files.size !== downloaded.size || [...downloaded.keys()].some(name => !files.has(name))) throw Error('Pinned native assets must match the manifest exactly');
  // CI downloads only validated public artifacts into this build directory.
  if (options.modulesCache) {
    if (fs.existsSync(options.modulesCache) && (!fs.lstatSync(options.modulesCache).isDirectory() || fs.readdirSync(options.modulesCache).length)) throw Error('Native download directory must be empty');
    fs.mkdirSync(options.modulesCache, {recursive:true});
    for (const [name, bytes] of files) fs.writeFileSync(path.join(options.modulesCache, name), bytes);
  }
  return files;
}
async function stageSite(options) {
  const {updater, out} = options;
  const manifest = fs.readFileSync(path.join(updater, 'updates.xri'));
  const packages = parseManifest(manifest);
  if (packages.length !== 1) throw Error('Exactly one current script package is required');
  const attrs = packages[0].attributes;
  if (!safeZip(attrs.fileName)) throw Error('Unsafe package path');
  const archive = fs.readFileSync(path.join(updater, attrs.fileName));
  checkArchive(attrs, archive);
  const files = new Map([
    ['updates.xri', manifest], [attrs.fileName, archive], ['LICENSE', fs.readFileSync(path.join(updater, 'LICENSE'))],
    ['index.html', Buffer.from(fs.readFileSync(path.join(root, 'website/index.html'), 'utf8').replaceAll('{{PACKAGE_FILE}}', escape(attrs.fileName)).replaceAll('{{PACKAGE_TITLE}}', escape(attrs.title || 'Loom scripts')))],
    ...['styles.css','app.js'].map(name => [name, fs.readFileSync(path.join(root, 'website', name))]),
    ...['loom-mark.svg','favicon.svg','platesolve-gpu-stars.png','fly-through-iris.jpg','fly-through-elephant.jpg'].map(name => ['assets/' + name, fs.readFileSync(path.join(root, 'website/assets', name))]),
    ...['loom.png','frame-selector.png','fly-through.png','banner.jpg'].map(name => ['assets/' + name, fs.readFileSync(path.join(root, 'pixinsight/docs/images', name))])
  ]);
  const modules = await nativeFiles(options);
  for (const [name, bytes] of modules) files.set('pixinsight/' + name, bytes);
  const directories = new Set(['assets', ...(modules.size ? ['pixinsight'] : [])]);
  if (fs.existsSync(out)) {
    if (!fs.lstatSync(out).isDirectory()) throw Error('Output must be a regular directory');
    for (const entry of fs.readdirSync(out, {recursive:true, withFileTypes:true})) {
      const relative = path.relative(out, path.join(entry.parentPath || entry.path, entry.name));
      if (entry.isSymbolicLink() || (entry.isDirectory() ? !directories.has(relative) : !files.has(relative))) throw Error('Unapproved output file: ' + relative);
    }
  }
  // No public output writes occur until every source/artifact has passed checks.
  for (const [name, bytes] of files) {
    const target = path.join(out, name);
    fs.mkdirSync(path.dirname(target), {recursive:true});
    fs.writeFileSync(target, bytes);
  }
  return files.size;
}
function option(name, fallback) {
  const i = process.argv.indexOf(name);
  if (i < 0) return fallback;
  if (!process.argv[i+1] || process.argv[i+1].startsWith('--')) throw Error('Missing value for ' + name);
  return path.resolve(process.argv[i+1]);
}
if (require.main === module) {
  (async () => {
    const out = option('--out', path.join(root, 'pixinsight/dist/repo'));
    const count = await stageSite({out, updater:option('--updater', path.join(root, 'pixinsight/repo')), modulesRepo:option('--modules-repo'), nativeRelease:option('--native-release', path.join(root, 'website/native-release.json')), modulesCache:option('--modules-cache')});
    console.log('Staged ' + count + ' public files at ' + out);
  })().catch(error => {console.error('Site staging failed: ' + error.message); process.exitCode = 1;});
}
module.exports = {stageSite, downloadNativeRelease, validateNative};
