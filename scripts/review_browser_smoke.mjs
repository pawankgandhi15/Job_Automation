// Real React/HTTP workflow against the isolated synthetic server on port 8766.
import { spawn } from "node:child_process";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const root=process.cwd();
const profile=path.join(root,"data","review-browser-"+Date.now());
await mkdir(profile,{recursive:true});
const browser=spawn("C:/Program Files/Google/Chrome/Application/chrome.exe",["--headless=new","--disable-gpu","--no-first-run","--no-default-browser-check","--remote-debugging-address=127.0.0.1","--remote-debugging-port=0","--user-data-dir="+profile],{windowsHide:true,stdio:"ignore"});
let socket;
const passed=[];
try {
  let port;
  for(let i=0;i<100;i++){try{port=(await readFile(path.join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0];break;}catch{await new Promise(resolve=>setTimeout(resolve,100));}}
  if(!port)throw Error("Browser did not start");
  const targets=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket=new WebSocket(targets.find(item=>item.type==="page").webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
  let next=0;const pending=new Map();
  socket.addEventListener("message",event=>{const message=JSON.parse(event.data);if(pending.has(message.id)){const task=pending.get(message.id);pending.delete(message.id);message.error?task.reject(Error(message.error.message)):task.resolve(message.result);}});
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  async function evaluate(expression){const data=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(data.exceptionDetails)throw Error(data.exceptionDetails.exception?.description || data.exceptionDetails.text);return data.result.value;}
  async function until(expression){for(let i=0;i<100;i++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}throw Error("Timed out: "+expression+"; "+await evaluate("document.querySelector('.review-alert')?.textContent"));}
  async function click(expression){await evaluate(`${expression}.click()`);}
  async function fill(selector,value){await evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(${selector.includes("textarea")?"HTMLTextAreaElement":"HTMLInputElement"}.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
  await call("Page.enable");
  await call("Emulation.setDeviceMetricsOverride",{width:1400,height:1000,deviceScaleFactor:1,mobile:false});
  await call("Page.navigate",{url:"http://127.0.0.1:8766/?view=review"});
  await until("document.querySelector('.review-toolbar select') && !document.querySelector('.review-alert')");
  await evaluate("(()=>{const input=document.querySelector('.review-toolbar select');input.value='';input.dispatchEvent(new Event('change',{bubbles:true}));})()");
  await until("document.querySelectorAll('.queue-item').length === 1");
  await click("document.querySelector('.queue-item')");
  await until("document.querySelectorAll('.gate').length >= 16");
  if(!await evaluate("document.querySelector('#approve-action').disabled"))throw Error("Approval was enabled before report review");
  passed.push("React queue and 16-gate inspector");
  await click("document.querySelector('.review-header button')");
  await until("document.querySelector('.report-dialog pre')?.textContent.includes('SYNTHETIC')");
  await until("!document.querySelector('.report-dialog button').disabled");
  await click("document.querySelector('.report-dialog button')");
  await until("!document.querySelector('.report-dialog') && !document.querySelector('#approve-action').disabled");
  passed.push("explicit report review");
  await click("document.querySelector('#approve-action')");
  await until("document.querySelector('.detail-heading .eyebrow').textContent.includes('APPROVED')");
  passed.push("channel-specific approval");
  await fill("textarea[placeholder^='Record the official']","Synthetic QA: employer page, eligibility, destination and duplicate history checked in isolated fixture.");
  await click("[...document.querySelectorAll('button')].find(button=>button.textContent==='Record verification')");
  await until("document.querySelector('.review-success')?.textContent.includes('Review saved')");
  await click("document.querySelector('#handoff-action')");
  await until("document.querySelector('.handoff-box')?.textContent.includes('No submission has been performed')");
  passed.push("manual verification and expiring handoff");
  await fill("textarea[placeholder^='Actual confirmation']","Synthetic QA: timeout after a possible submission; do not retry.");
  await click("[...document.querySelectorAll('button')].find(button=>button.textContent==='Record outcome')");
  await until("document.querySelector('.detail-heading .eyebrow').textContent.includes('SUBMISSION UNCERTAIN')");
  if(!await evaluate("document.querySelector('#handoff-action').disabled"))throw Error("Uncertain handoff remained enabled");
  passed.push("uncertain outcome blocks retries");
  await call("Emulation.setDeviceMetricsOverride",{width:390,height:900,deviceScaleFactor:1,mobile:true});
  await new Promise(resolve=>setTimeout(resolve,300));
  const layout=await evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth})");
  if(layout.scroll>layout.width)throw Error("Mobile overflow: "+JSON.stringify(layout));
  const screenshot=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
  await writeFile(path.join(root,"data/review-workstation-mobile.png"),Buffer.from(screenshot.data,"base64"));
  passed.push("390px mobile layout");
  const result={browser_workflow:"PASS",checks:passed,layout,synthetic:true};
  await writeFile(path.join(root,"data/phase23-browser-verification.json"),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
  await call("Browser.close").catch(()=>{});
} finally {socket?.close();browser.kill();}
