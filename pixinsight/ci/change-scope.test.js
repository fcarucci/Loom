'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const {detectCodeChanges}=require('./change-scope.js');
const workflow=fs.readFileSync(path.resolve(__dirname,'../../.github/workflows/ci.yml'),'utf8');
function jobBody(job){const body=workflow.split('\n  '+job+':\n')[1]?.split(/\n  [a-z][\w-]*:\n/)[0];assert.ok(body,'missing job '+job);return body;}
function requiredSuccess(job,context){
 const raw=jobBody(job).match(/^    needs: (.+)$/m)?.[1];assert.ok(raw,'missing dependency list '+job);
 return raw.replace(/[\[\]]/g,'').split(',').map(s=>s.trim()).every(name=>context.needs[name]?.result==='success');
}
function condition(job){
 const body=jobBody(job);
 const expression=body.match(/^    if: (.+)$/m)?.[1];assert.ok(expression,'missing explicit condition '+job);
 return expression.replace(/^\$\{\{\s*/, '').replace(/\s*\}\}$/, '');
}
function allowed(job,context){return vm.runInNewContext(condition(job),{...context,always:()=>true,cancelled:()=>!!context.cancelled,contains:(text,needle)=>String(text||'').includes(needle)});}
function fixture(t){
 const cwd=fs.mkdtempSync(path.join(os.tmpdir(),'loom-scope-'));t.after(()=>fs.rmSync(cwd,{recursive:true,force:true}));
 const git=(...args)=>execFileSync('git',args,{cwd,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();git('init','-q');
 function commit(){git('add','-A');git('-c','user.name=scope-test','-c','user.email=scope@example.invalid','-c','commit.gpgsign=false','commit','-qm','fixture');return git('rev-parse','HEAD');}
 function write(file,text){fs.mkdirSync(path.dirname(path.join(cwd,file)),{recursive:true});fs.writeFileSync(path.join(cwd,file),text);}
 write('website/index.html','old');write('pixinsight/script/Loom.js','old');write('.github/workflows/ci.yml','old');const base=commit();return {cwd,git,commit,write,base};
}
function push(f,head,extra={}){return detectCodeChanges({cwd:f.cwd,eventName:'push',refType:'branch',head,event:{before:f.base},...extra});}
function graph(code,eventName='push',refType='branch',ref='refs/heads/main',message='Update [publish-site]'){
 const needs={scope:{result:'success',outputs:{code:String(code)}},site:{result:'success'},test:{result:'skipped'},package:{result:'skipped'}};
 const context={needs,github:{event_name:eventName,ref_type:refType,ref,event:{head_commit:{message}}}};
 if(allowed('test',context))needs.test.result='success';
 if(requiredSuccess('package',context)&&allowed('package',context))needs.package.result='success';
 return {test:needs.test.result,package:needs.package.result,pages:allowed('pages',context),context};
}
test('required five workflow scenarios exercise actual job conditions',t=>{
 const f=fixture(t);f.write('website/index.html','new');const web=f.commit();assert.equal(push(f,web).code,false);
 assert.deepEqual((({test,package:pkg,pages})=>[test,pkg,pages])(graph(false)),['skipped','skipped',true]);
 f.write('pixinsight/script/Loom.js','new');const mixed=f.commit();assert.equal(push(f,mixed).code,true);
 assert.deepEqual((({test,package:pkg,pages})=>[test,pkg,pages])(graph(true)),['success','success',true]);
 f.write('.github/workflows/ci.yml','new');const workflowHead=f.commit();assert.equal(push(f,workflowHead,{event:{before:mixed}}).code,true);
 assert.equal(graph(true).pages,true);
 assert.equal(detectCodeChanges({eventName:'workflow_dispatch'}).code,true);assert.equal(graph(true,'workflow_dispatch').pages,true);
 assert.equal(detectCodeChanges({eventName:'push',refType:'tag'}).code,true);assert.equal(graph(true,'push','tag','refs/tags/v1.0.0','release').pages,true);
});
test('PR head/base, renames, deletions and unknown diffs classify conservatively',t=>{
 const f=fixture(t);f.write('website/index.html','new');const head=f.commit();
 assert.equal(detectCodeChanges({cwd:f.cwd,eventName:'pull_request',event:{pull_request:{base:{sha:f.base},head:{sha:head}}}}).code,false);
 f.git('mv','pixinsight/script/Loom.js','website/renamed.js');const moved=f.commit();assert.equal(push(f,moved,{event:{before:head}}).code,true);
 f.git('rm','.github/workflows/ci.yml');const deleted=f.commit();assert.equal(push(f,deleted,{event:{before:moved}}).code,true);
 for(const before of ['0'.repeat(40),'deadbeef','1'.repeat(40)])assert.equal(push(f,head,{event:{before}}).code,true);
 assert.equal(push(f,head,{event:{before:head}}).code,true,'empty diff does not establish website-only scope');
});
test('CI helper edits and nonwebsite companions keep full checks',t=>{
 const f=fixture(t);for(const file of ['pixinsight/ci/stage-site.js','README.md']){
  const before=f.git('rev-parse','HEAD');f.write(file,'changed');const head=f.commit();assert.equal(push(f,head,{event:{before}}).code,true);
 }
});
test('Pages refuses failed/cancelled gates and unauthorised events, permits only expected skipped jobs',()=>{
 for(const code of [true,false])for(const gate of ['scope','site',...(code?['test','package']:[])])for(const outcome of ['failure','cancelled']){
  const {context}=graph(code);context.needs[gate].result=outcome;assert.equal(allowed('pages',context),false,code+' '+gate+' '+outcome);
 }
 const {context}=graph(false);context.cancelled=true;assert.equal(allowed('pages',context),false);
 context.cancelled=false;context.needs.test.result='success';assert.equal(allowed('pages',context),false);
 assert.equal(graph(false,'pull_request').pages,false);assert.equal(graph(false,'push','branch','refs/heads/main','normal update').pages,false);
 assert.equal(graph(true,'workflow_dispatch','branch','refs/heads/other').pages,false);
});

test('failed or cancelled website checks block tag package and release publication',()=>{
 for(const outcome of ['failure','cancelled']){
  const {context}=graph(true,'push','tag','refs/tags/v1.0.0','release');context.needs.site.result=outcome;
  assert.equal(requiredSuccess('package',context)&&allowed('package',context),false,'tag package/release blocked by site '+outcome);
  assert.equal(allowed('pages',context),false);
 }
});
