#!/usr/bin/env node
'use strict';
// Stage only explicitly public files. Never rewrite the signed updater artifacts.
const fs=require('node:fs'); const path=require('node:path'); const crypto=require('node:crypto');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..');
function option(name,fallback){const i=process.argv.indexOf(name);return i<0?fallback:path.resolve(process.argv[i+1]);}
const updater=option('--updater',path.join(root,'pixinsight/repo'));
const out=option('--out',path.join(root,'pixinsight/dist/repo'));
function escape(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
try {
 const manifest=fs.readFileSync(path.join(updater,'updates.xri'));
 const text=manifest.toString('utf8'); const end=text.indexOf('</xri>');
 if(end<0 || !/^\s*<Signature\b[^>]*>[^<]+<\/Signature>\s*$/.test(text.slice(end+6))) throw Error('A signed manifest is required');
 // PixInsight appends its signature after the XML document; parse only the xri.
 const xml=text.slice(0,end+6);
 // Python's standard XML parser validates structure and decodes attributes.
 // Python 3 is present on the Ubuntu Pages runner; no third-party parser is used.
 const parsed=spawnSync('python3',['-c',`import sys,json,xml.etree.ElementTree as ET
try:
 text=sys.stdin.read()
 if '<!DOCTYPE' in text or '<!ENTITY' in text: raise ValueError('XML declarations are not allowed')
 root=ET.fromstring(text)
 if root.tag!='xri' or root.attrib.get('version')!='1.0': raise ValueError('expected xri version 1.0')
 packages=root.findall('./platform/package')
 if len(packages)!=1 or len(list(root.iter('package')))!=1: raise ValueError('expected exactly one current package')
 print(json.dumps(packages[0].attrib))
except (ET.ParseError,ValueError) as error:
 print(str(error),file=sys.stderr)
 sys.exit(1)
`],{input:xml,encoding:'utf8'});
 if(parsed.status!==0) throw Error('Invalid manifest XML: '+(parsed.stderr||parsed.error?.message||'parser unavailable').trim());
 const attrs=JSON.parse(parsed.stdout);
 if(!/^[a-zA-Z0-9][a-zA-Z0-9._-]*\.zip$/.test(attrs.fileName||'') || !/^[a-f0-9]{40}$/.test(attrs.sha1||'')) throw Error('Unsafe package path or checksum');
 const archive=fs.readFileSync(path.join(updater,attrs.fileName));
 if(crypto.createHash('sha1').update(archive).digest('hex')!==attrs.sha1) throw Error('Archive checksum mismatch');
 const files=new Map([
 ['updates.xri',manifest],[attrs.fileName,archive],['LICENSE',fs.readFileSync(path.join(updater,'LICENSE'))],
 ['index.html',Buffer.from(fs.readFileSync(path.join(root,'website/index.html'),'utf8').replaceAll('{{PACKAGE_FILE}}',escape(attrs.fileName)).replaceAll('{{PACKAGE_TITLE}}',escape(attrs.title||'Loom scripts')))],
 ...['styles.css','app.js'].map(name=>[name,fs.readFileSync(path.join(root,'website',name))]),
 ...['loom-mark.svg','favicon.svg','platesolve-gpu-stars.png'].map(name=>['assets/'+name,fs.readFileSync(path.join(root,'website/assets',name))]),
 ...['loom.png','frame-selector.png','fly-through.png','banner.jpg'].map(name=>['assets/'+name,fs.readFileSync(path.join(root,'pixinsight/docs/images',name))])
 ]);
 if(fs.existsSync(out)) {
  for(const entry of fs.readdirSync(out,{recursive:true,withFileTypes:true})) {
   const relative=path.relative(out,path.join(entry.parentPath||entry.path,entry.name));
   if(entry.isSymbolicLink() || (!entry.isDirectory()&&!files.has(relative)) || (entry.isDirectory()&&relative!=='assets')) throw Error('Unapproved output file: '+relative);
  }
 }
 for(const [name,bytes] of files){const target=path.join(out,name);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,bytes);}
 console.log('Staged '+files.size+' public files at '+out);
} catch(error) {console.error('Site staging failed: '+error.message);process.exitCode=1;}
