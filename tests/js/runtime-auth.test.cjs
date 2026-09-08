const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('app/static/microsite-runtime-v1.js', 'utf8');
function setup() {
  let required = false, failSession = false;
  const redirects = [], store = new Map(), calls = [];
  const window = {
    location: {pathname:'/sites/sample-site/', search:'?month=2026-09', href:'https://example.test/sites/sample-site/?month=2026-09', origin:'https://example.test', assign: url=>redirects.push(url)},
    document:{body:{dataset:{}},addEventListener(){}},
    addEventListener(){},dispatchEvent(){},setTimeout,
    localStorage:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)},
    fetch:async (url, options)=>{
      calls.push({url:String(url),options});
      if (String(url).startsWith('/api/session')) {
        if(failSession)throw Error('offline');
        return {ok:true,status:200,json:async()=>({authenticated:!required,login_required:required})};
      }
      return {ok:false,status:401,json:async()=>({detail:'Unauthorized'})};
    },
  };
  const context={window,URL,CustomEvent:class{},setTimeout};
  Object.defineProperty(context,'fetch',{get:()=>window.fetch});
  vm.runInNewContext(source, context);
  return {window,redirects,calls,setExpired:()=>{required=true},setOffline:()=>{failSession=true}};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,25));
test('authenticated browser is not redirected by a credentials-omitting 401',async()=>{
 const s=setup();await settle();
 const response=await s.window.fetch('/api/runtime/sites/sample-site/documents/settings',{credentials:'omit'});
 assert.equal(response.status,401);await settle();assert.equal(s.redirects.length,0);
 assert.equal(s.calls.at(-1).options.credentials,'same-origin');
});
test('expired session redirects once and preserves failed-save recovery draft',async()=>{
 const s=setup();await settle();s.setExpired();
 const doc=s.window.MicrositeData.document('settings');
 await assert.rejects(doc.save({draft:true},{revision:1}),e=>e.status===401);
 doc.clearDraft();await settle();
 assert.equal(doc.loadDraft().value.draft,true);
 assert.equal(s.redirects.length,1);
 assert.equal(s.redirects[0],'/admin/login?next=%2Fsites%2Fsample-site%2F%3Fmonth%3D2026-09');
});
test('unverifiable session and third-party 401 do not trigger login loops',async()=>{
 const s=setup();await settle();s.setOffline();
 await s.window.fetch('/api/failure');await settle();assert.equal(s.redirects.length,0);
 const count=s.calls.length;
 await s.window.fetch('https://other.test/private');await settle();
 assert.equal(s.calls.length,count+1);assert.equal(s.redirects.length,0);
});
