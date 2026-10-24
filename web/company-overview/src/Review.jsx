import { useCallback, useEffect, useRef, useState } from "react";
import { safeURL } from "./snapshot.js";
import "./review.css";

async function request(path, body, csrf) {
  const response = await fetch(`/api/v1${path}`, {credentials:"same-origin", cache:"no-store", ...(body ? {method:"POST", headers:{"Content-Type":"application/json", "X-Job-Agent-CSRF":csrf}, body:JSON.stringify(body)} : {})});
  if (!(response.headers.get("content-type") || "").includes("application/json")) throw new Error("The interactive review service is available on your computer.");
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed. Refresh and review the current details.");
  return data;
}

function Link({url, children}) { const value=safeURL(url); return value ? <a href={value} target="_blank" rel="noopener noreferrer">{children} ↗</a> : <span>{children}</span>; }

export default function Review() {
  const [csrf, setCSRF] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState(null);
  const [query, setQuery] = useState("");
  const [state, setState] = useState("NEEDS_CLARIFICATION");
  const [tier, setTier] = useState("");
  const [family, setFamily] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [channels, setChannels] = useState(["portal"]);
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [evidenceGate, setEvidenceGate] = useState("degree_level");
  const [gateStatus, setGateStatus] = useState("UNKNOWN");
  const [gateReason, setGateReason] = useState("");
  const [gateSource, setGateSource] = useState("");
  const [gateQuote, setGateQuote] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [answerBasis, setAnswerBasis] = useState("");
  const [consentQuestion, setConsentQuestion] = useState("");
  const [consentAnswer, setConsentAnswer] = useState("");
  const [highlight, setHighlight] = useState("");
  const [report, setReport] = useState(null);
  const [handoff, setHandoff] = useState(null);
  const [attempt, setAttempt] = useState("");
  const [outcome, setOutcome] = useState("uncertain");
  const [confirmation, setConfirmation] = useState("");
  const [notifications, setNotifications] = useState([]);
  const searchRef=useRef(null), reasonRef=useRef(null), jdRef=useRef(null);

  const reload = useCallback(async () => {
    const data = await request(`/queue?q=${encodeURIComponent(query)}&state=${encodeURIComponent(state)}&offset=${offset}&limit=50&tier=${tier}&family=${family}`);
    setItems(data.items); setHasMore(data.has_more);
    const notes=await request("/notifications"); setNotifications(notes.items);
  }, [query, state, offset, tier, family]);

  const loadDetail = useCallback(async id => {
    if (!id) {setDetail(null); return;}
    const value=await request(`/opportunities/${encodeURIComponent(id)}`);
    setDetail(value); setGateSource(current=>current || value.payload.canonical_url); setAttempt(current => value.attempts.some(item=>item.token===current) ? current : value.attempts[0]?.token || "");
  }, []);

  useEffect(() => { let active=true; request("/session").then(data=>{if(active) {setCSRF(data.csrf);setError("");}}).catch(error=>{if(active)setError(error.message);}); return()=>{active=false;}; }, []);
  useEffect(() => {if(!csrf)return;const timer=setTimeout(()=>reload().catch(error=>setError(error.message)),200);return()=>clearTimeout(timer);}, [csrf,reload]);
  useEffect(() => {setHighlight("");setReason("");setEvidence("");setGateSource("");setGateReason("");setGateQuote("");setQuestion("");setAnswer("");setAnswerBasis("");setConsentQuestion("");setConsentAnswer("");setHandoff(null);loadDetail(selected).catch(error=>setError(error.message));}, [selected, loadDetail]);
  useEffect(() => {
    if(!csrf)return;
    const source=new EventSource("/api/v1/events/stream"); let timer;
    const update=()=>{clearTimeout(timer);timer=setTimeout(()=>{reload().catch(()=>{});if(selected)loadDetail(selected).catch(()=>{});},200);};
    source.addEventListener("ledger",update);window.addEventListener("focus",update);
    return()=>{source.close();clearTimeout(timer);window.removeEventListener("focus",update);};
  }, [csrf,reload,selected,loadDetail]);

  async function perform(action, extra={}) {
    if(!detail || busy)return;
    setBusy(true);setError("");setMessage("");
    try {
      const result=await request(`/opportunities/${detail.opportunity_id}/${action}`, {expected_scope_hash:detail.scope_hash,...extra}, csrf);
      if(action==="handoff")setHandoff(result);
      setMessage(action==="approve" ? "Selected actions approved. No application or message has been sent." : action==="handoff" ? "Manual handoff created. Record the actual outcome after performing the approved action." : "Review saved.");
      await loadDetail(selected);await reload();
    } catch(error) {setError(error.message);await loadDetail(selected).catch(()=>{});}
    finally {setBusy(false);}
  }

  async function makeReport() {
    setBusy(true);setError("");
    try {setReport(await request("/reports",{},csrf));await reload();if(selected)await loadDetail(selected);}
    catch(error){setError(error.message);}finally{setBusy(false);}
  }

  async function markReviewed() {
    setBusy(true);
    try {await request(`/reports/${report.report_id}/review`,{expected_hash:report.review_hash},csrf);setReport(null);setMessage("Report review recorded. Eligible actions can now be approved individually.");await reload();if(selected)await loadDetail(selected);}
    catch(error){setError(error.message);}finally{setBusy(false);}
  }

  async function reconcile() {
    setBusy(true);setError("");
    try {await request(`/attempts/${attempt}/reconcile`,{outcome,evidence:confirmation},csrf);setMessage("Actual outcome recorded. This does not perform an employer action.");setConfirmation("");await loadDetail(selected);await reload();}
    catch(error){setError(error.message);}finally{setBusy(false);}
  }

  async function copyDraft() {
    try {await navigator.clipboard.writeText(detail.package.outreach_draft.body);setMessage("Draft copied. Review the recipient and attachment before using it.");}
    catch {setError("Clipboard access was unavailable. Select the draft text to copy it.");}
  }

  useEffect(() => {
    const keydown=event=>{
      if(event.ctrlKey || event.metaKey || event.altKey || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName) || event.target.isContentEditable || report)return;
      const index=items.findIndex(item=>item.opportunity_id===selected);
      if(event.key==="j" || event.key==="k") {event.preventDefault();const next=event.key==="j"?Math.min(items.length-1,index+1):Math.max(0,index-1);if(items[next])setSelected(items[next].opportunity_id);}
      if(event.key==="/"){event.preventDefault();searchRef.current?.focus();}
      if(event.key==="a")document.getElementById("approve-action")?.click();
      if(event.key==="s")reason.trim()?document.getElementById("skip-action")?.click():reasonRef.current?.focus();
      if(event.key==="h")document.getElementById("handoff-action")?.click();
      if(event.key==="c" && detail)copyDraft();
    };
    window.addEventListener("keydown",keydown);return()=>window.removeEventListener("keydown",keydown);
  });

  const job=detail?.payload, evaluation=detail?.evaluation, pack=detail?.package;
  const description=job?.description || "No description recorded.";
  const position=highlight?description.indexOf(highlight):-1;
  const activeAttempt=detail?.attempts?.find(item=>item.token===attempt);
  const possibleChannels=(pack?.actions || []).map(item=>item.channel);
  const approved=detail?.state==="APPROVED" || detail?.follow_up_status==="AWAITING_APPROVAL";

  return <div className="workstation">
    <header className="review-header"><div><a href="./">j. Company briefs</a><h1>Review your next move.</h1><p>Inspect the evidence, approve exact actions, and track the outcome.</p></div><button onClick={makeReport} disabled={!csrf || busy}>Generate review report</button></header>
    <div className="review-toolbar"><label>Search roles<input ref={searchRef} placeholder="Company, role, location or skill" value={query} onChange={event=>{setQuery(event.target.value);setOffset(0);}} /></label><label>Queue<select value={state} onChange={event=>{setState(event.target.value);setOffset(0);}}><option value="">All recorded roles</option>{["NEEDS_CLARIFICATION","AWAITING_APPROVAL","APPROVED","EXECUTING","SUBMISSION_UNCERTAIN","SUBMITTED_CONFIRMED","OUTREACH_SENT_CONFIRMED","DISMISSED","REJECTED"].map(value=><option key={value} value={value}>{value.replaceAll("_"," ")}</option>)}</select></label><label>Company tier<select aria-label="Company tier" value={tier} onChange={event=>{setTier(event.target.value);setOffset(0);}}><option value="">All tiers</option>{["GOOD","BETTER","GREAT"].map(item=><option key={item}>{item}</option>)}</select></label><label>Role family<select aria-label="Role family" value={family} onChange={event=>{setFamily(event.target.value);setOffset(0);}}><option value="">All families</option><option value="cloud">Cloud / DevOps</option><option value="backend">Backend</option><option value="sde">Software engineering</option></select></label><span className="shortcut-help">j / k navigate · a approve · s skip · h handoff · c copy · / search</span></div>
    {error && <div className="review-alert" role="alert">{error}{!csrf && <p>Start <code>python -m job_agent serve</code>, then <a href="http://127.0.0.1:8765/review">open the local review workstation</a>.</p>}</div>}
    {message && <p className="review-success" role="status">{message}</p>}
    {notifications.length>0 && <details className="notification-box"><summary>{notifications.length} activity alerts</summary>{notifications.map(item=><p key={item.id}>{item.kind.replaceAll("_"," ")} · {item.message}</p>)}</details>}
    <div className="review-layout"><aside className="review-queue" aria-label="Opportunity queue">{items.length===0?<p className="queue-empty">{csrf?"No roles in this view. Try another queue or search.":"Connecting to your local ledger…"}</p>:items.map(item=><button className="queue-item" aria-pressed={selected===item.opportunity_id} key={item.opportunity_id} onClick={()=>setSelected(item.opportunity_id)}><span>{item.company}<small>{item.tier} · {item.opportunity_id}</small></span><strong>{item.role}</strong><small>{item.location}</small><em>{item.state.replaceAll("_"," ")}</em></button>)}<div className="queue-pages"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous</button><button disabled={!hasMore} onClick={()=>setOffset(offset+50)}>Next</button></div></aside>
    <section className="review-detail" aria-label="Opportunity evidence">{!detail?<div className="detail-placeholder"><h2>Select a role to inspect its evidence.</h2><p>Unknown eligibility stays unresolved until you have supporting information.</p></div>:<>
      <div className="detail-heading"><p className="eyebrow">{detail.opportunity_id} · {detail.state.replaceAll("_"," ")}</p><h2>{job.role}</h2><p>{job.company} · {job.location}</p><Link url={job.application_url || job.canonical_url}>Open official application</Link><span className="detail-score">{evaluation?.match_score==null?"Score unconfirmed":`${evaluation.match_score}/10 evidence-based match`}</span></div>
      <h3>Eligibility evidence</h3><div className="gate-grid">{Object.entries(evaluation?.gates || {}).map(([name, gate])=><article key={name} className={`gate gate-${gate.status.toLowerCase()}`}><button className="gate-select" onClick={()=>{setHighlight(gate.evidence?.quote || "");jdRef.current?.scrollIntoView({behavior:"smooth",block:"start"});}}><strong>{name.replaceAll("_"," ")}</strong><span>{gate.status}</span><small>{gate.reason}</small></button><Link url={gate.source}>Source</Link></article>)}</div>
      {pack?.blockers?.length>0 && <div className="blocker-box"><strong>Resolve before approval</strong><ul>{pack.blockers.map(text=><li key={text}>{text}</li>)}</ul></div>}
      <details className="jd-view" ref={jdRef} open={Boolean(highlight)}><summary>Full job description and evidence</summary><pre>{position>=0?<>{description.slice(0,position)}<mark>{highlight}</mark>{description.slice(position+highlight.length)}</>:description}</pre></details>
      <details className="evidence-editor"><summary>Add reviewed eligibility evidence</summary><p>Record only checks you actually performed. New evidence invalidates stale approvals and may require a new report.</p><label>Eligibility gate<select value={evidenceGate} onChange={event=>setEvidenceGate(event.target.value)}>{Object.keys(evaluation?.gates || {}).filter(name=>name!=="role_scope").map(name=><option key={name} value={name}>{name.replaceAll("_"," ")}</option>)}</select></label><label>Evidence status<select value={gateStatus} onChange={event=>setGateStatus(event.target.value)}><option>UNKNOWN</option><option>PASS</option><option>FAIL</option></select></label><label>Specific reason<textarea value={gateReason} onChange={event=>setGateReason(event.target.value)} /></label><label>Primary source URL<input value={gateSource} onChange={event=>setGateSource(event.target.value)} /></label><label>Exact excerpt, when available<textarea value={gateQuote} onChange={event=>setGateQuote(event.target.value)} /></label><button disabled={busy || !gateReason.trim() || !gateSource.trim()} onClick={()=>perform("evidence",{gate:evidenceGate,status:gateStatus,reason:gateReason,source:gateSource,quote:gateQuote})}>Record reviewed evidence</button></details>
      {(job.required_questions?.length>0 || job.required_consents?.length>0)&&<details className="screening-editor"><summary>Required answers and declarations</summary>{job.required_questions?.length>0&&<><label>Exact screening question<select value={question} onChange={event=>setQuestion(event.target.value)}><option value="">Select a required question</option>{job.required_questions.map(value=><option key={value}>{value}</option>)}</select></label><label>Your confirmed answer<textarea value={answer} onChange={event=>setAnswer(event.target.value)} /></label><label>Basis for the answer<textarea value={answerBasis} onChange={event=>setAnswerBasis(event.target.value)} placeholder="Candidate confirmation or the relevant resume evidence" /></label><button disabled={busy || !question || !answer.trim() || !answerBasis.trim()} onClick={()=>perform("answer",{question,answer,evidence:answerBasis})}>Record confirmed answer</button></>}{job.required_consents?.length>0&&<><label>Exact mandatory declaration<select value={consentQuestion} onChange={event=>setConsentQuestion(event.target.value)}><option value="">Select the declaration</option>{job.required_consents.map(value=><option key={value}>{value}</option>)}</select></label><label>Your explicit response<select value={consentAnswer} onChange={event=>setConsentAnswer(event.target.value)}><option value="">Choose Yes or No</option><option value="yes">Yes</option><option value="no">No</option></select></label><button disabled={busy || !consentQuestion || !consentAnswer} onClick={()=>perform("consent",{question:consentQuestion,answer:consentAnswer})}>Record declaration response</button></>}</details>}
      <section className="action-box"><h3>Review and authorize</h3><p>Generate and read the report before approving. Approval records permission for the selected actions.</p><div className="channel-options">{possibleChannels.map(channel=><label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={event=>setChannels(event.target.checked?[...channels,channel]:channels.filter(item=>item!==channel))} />{channel}</label>)}{detail.outreach_status==="OUTREACH_SENT_CONFIRMED"&&<label><input type="checkbox" checked={channels.includes("follow_up")} onChange={event=>setChannels(event.target.checked?["follow_up"]:["portal"])} />Separate follow-up approval</label>}</div><button id="approve-action" disabled={busy || !evaluation || (channels[0]!=="follow_up" && (evaluation.status!=="READY_FOR_REVIEW" || !detail.reviewed_report)) || (channels[0]!=="follow_up" && pack?.blockers?.length>0) || channels.length===0} onClick={()=>perform("approve",{channels})}>Approve selected actions</button><label>Reason for skipping<textarea ref={reasonRef} value={reason} onChange={event=>setReason(event.target.value)} placeholder="Explain why you are dismissing this role" /></label><button id="skip-action" disabled={busy || !reason.trim()} onClick={()=>perform("skip",{reason})}>Skip role</button></section>
      <section className="action-box"><h3>Prepare a manual handoff</h3><label>Current verification evidence<textarea value={evidence} onChange={event=>setEvidence(event.target.value)} placeholder="Record the official URLs and the vacancy, eligibility, recipient and duplicate checks you actually performed" /></label><button disabled={busy || !evidence.trim()} onClick={()=>perform("recheck",{evidence})}>Record verification</button><button id="handoff-action" disabled={busy || !approved || channels.length!==1} onClick={()=>perform("handoff",{channel:channels[0]})}>Create handoff for selected channel</button>{handoff&&<div className="handoff-box"><strong>Manual handoff · expires {handoff.expires_at}</strong><p>No submission has been performed. Use the exact approved destination and attachment.</p><pre>{JSON.stringify(handoff.action,null,2)}</pre></div>}</section>
      <details className="draft-view"><summary>Outreach draft and screening answers</summary><button onClick={copyDraft}>Copy outreach draft</button><pre>{pack?.outreach_draft?.body}</pre><pre>{JSON.stringify(pack?.screening_answers,null,2)}</pre></details>
      {detail.attempts.length>0&&<section className="action-box"><h3>Record the actual outcome</h3><label>Attempt<select value={attempt} onChange={event=>setAttempt(event.target.value)}>{detail.attempts.map(item=><option key={item.token} value={item.token}>{item.channel} · {item.status} · {item.started_at}</option>)}</select></label><label>Observed outcome<select value={outcome} onChange={event=>setOutcome(event.target.value)}><option value="uncertain">Uncertain — stop retries</option><option value="confirmed">Confirmed by portal or provider</option><option value="failed">Failed before submission</option><option value="not_submitted">Checked and confirmed not submitted</option></select></label><label>Confirmation or verification evidence<textarea value={confirmation} onChange={event=>setConfirmation(event.target.value)} placeholder="Actual confirmation reference, observed failure or evidence of non-submission" /></label><button disabled={busy || !confirmation.trim() || !["EXECUTING","SUBMISSION_UNCERTAIN"].includes(activeAttempt?.status)} onClick={reconcile}>Record outcome</button></section>}
      <details className="audit-view"><summary>Audit history</summary>{detail.audit.map(item=><p key={item.id}><time>{item.at}</time> · {item.action} · {item.result}</p>)}</details>
    </>}</section></div>
    {report&&<div className="report-backdrop"><section className="report-dialog" role="dialog" aria-modal="true" aria-labelledby="report-title"><h2 id="report-title">Review report {report.report_id}</h2><p>Read the findings and exact action packages. Recording review does not approve any application.</p><pre tabIndex={0}>{report.markdown}</pre><div><button disabled={busy} onClick={markReviewed}>I have reviewed this report</button><button onClick={()=>setReport(null)}>Close without recording review</button></div></section></div>}
  </div>;
}
