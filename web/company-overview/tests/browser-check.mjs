import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.resolve(project, "../../data");
const chrome = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
if (!existsSync(chrome)) throw Error("Set CHROME_PATH to an installed Chrome executable for browser checks.");
const profile = path.join(output, `company-browser-check-${Date.now()}`);
await mkdir(profile, {recursive:true});
const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url, "http://localhost");
    const files = url.pathname === "/__qa.js" ? path.join(project,"tests/browser-smoke.js") : path.join(project,"dist", url.pathname === "/" ? "index.html" : url.pathname.slice(1));
    if (!files.startsWith(path.join(project,"dist") + path.sep) && files !== path.join(project,"tests/browser-smoke.js")) { response.writeHead(404).end(); return; }
    let content = await readFile(files);
    if (url.pathname === "/" && !url.searchParams.has("persist")) content = Buffer.from(content.toString().replace("</body>", '<script src="/__qa.js"></script></body>'));
    response.setHeader("Content-Type", files.endsWith(".js") ? "text/javascript" : files.endsWith(".css") ? "text/css" : files.endsWith(".json") ? "application/json" : "text/html");
    response.end(content);
  } catch { response.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0,"127.0.0.1",resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = spawn(chrome, ["--headless=new","--disable-gpu","--no-first-run","--no-default-browser-check","--remote-debugging-address=127.0.0.1","--remote-debugging-port=0",`--user-data-dir=${profile}`], {windowsHide:true,stdio:"ignore"});
let socket;
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
try {
  let port;
  for (let attempt=0;attempt<100;attempt++) {
    try { port = (await readFile(path.join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0]; break; } catch { await delay(100); }
  }
  if (!port) throw Error("Headless browser did not start");
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(targets.find(target => target.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
  let next = 0;
  const pending = new Map();
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (!pending.has(message.id)) return;
    const {resolve,reject,timer} = pending.get(message.id); pending.delete(message.id); clearTimeout(timer);
    message.error ? reject(Error(message.error.message)) : resolve(message.result);
  });
  const call = (method, params={}) => new Promise((resolve,reject) => {
    const id = ++next;
    const timer = setTimeout(() => {pending.delete(id);reject(Error(`Browser command timed out: ${method}`));},15000);
    pending.set(id,{resolve,reject,timer}); socket.send(JSON.stringify({id,method,params}));
  });
  const evaluate = async expression => {
    const result = await call("Runtime.evaluate",{expression,returnByValue:true});
    if (result.exceptionDetails) throw Error(result.exceptionDetails.text);
    return result.result.value;
  };
  const until = async expression => {
    for (let attempt=0;attempt<150;attempt++) {const value=await evaluate(expression);if(value)return value;await delay(100);}
    throw Error(`Browser state timed out: ${expression}`);
  };
  await call("Page.enable");
  await call("Emulation.setDeviceMetricsOverride",{width:1280,height:1000,deviceScaleFactor:1,mobile:false});
  await call("Page.navigate",{url:origin});
  const result = await until('document.querySelector("#qa-result")?.textContent');
  if (!result.startsWith("PASS:")) throw Error(result);
  // Reload the actual app to verify persistence without rerunning the smoke script.
  await evaluate('document.querySelector(".save-company").click()');
  await until('JSON.parse(localStorage.getItem("job-search:saved-companies") || "[]").length === 1');
  await call("Page.navigate",{url:origin+"/?persist=1"});
  await until('document.querySelector("#result-count")?.textContent.includes("Showing")');
  await evaluate('document.querySelector("[data-filter=saved]").click()');
  await until('document.querySelectorAll(".card").length === 1 && document.querySelector(".save-company")?.getAttribute("aria-pressed") === "true"');
  await evaluate('document.querySelector("[data-filter=all]").click()');
  await until('document.querySelectorAll(".card").length > 1');
  const desktop = await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
  await writeFile(path.join(output,"company-overview-advanced-desktop.png"),Buffer.from(desktop.data,"base64"));
  await call("Emulation.setDeviceMetricsOverride",{width:390,height:900,deviceScaleFactor:1,mobile:true});
  await delay(200);
  const layout = await evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})');
  if (layout.scroll > layout.width) throw Error(`Mobile overflow: ${JSON.stringify(layout)}`);
  const mobile = await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});
  await writeFile(path.join(output,"company-overview-advanced-mobile.png"),Buffer.from(mobile.data,"base64"));
  console.log(`${result}, persistence across reload, responsive layout`);
  await call("Browser.close").catch(()=>{});
} finally {socket?.close();browser.kill();await new Promise(resolve=>server.close(resolve));}
