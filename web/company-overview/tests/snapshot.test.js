import test from "node:test";
import assert from "node:assert/strict";
import { discoveryHealth, filterCompanies, istDay, matchingJobs, todayDomains, readSavedCompanies, readSnapshotFile, safeURL, staleResearch, validRoleDetails, validSnapshot } from "../src/snapshot.js";

function snapshot() {
  return {
    kind: "job_agent_company_snapshot", schema_version: 1, generated_at: "2026-10-22T09:00:00+05:30", review_count: 1,
    companies: [{name:"Example", domain:"example.com", sector:"Backend", ceo:null, tags:["Python"], recorded_count:1, review_count:1,
      jobs:[{id:"JOB-001",title:"Backend Intern",location:"India",status:"NEEDS_CLARIFICATION",review_needed:true}]}],
  };
}

test("external links allow HTTPS without embedded credentials", () => {
  assert.equal(safeURL("https://example.com/news"), "https://example.com/news");
  for (const url of ["javascript:alert(1)", "data:text/html,test", "https://user:secret@example.com", "http://example.com", null]) assert.equal(safeURL(url), null);
});

test("role type, location and search must match the same reviewable vacancy", () => {
  const company = snapshot().companies[0];
  company.jobs[0].employment_type = "internship";
  company.jobs.push({id:"JOB-002",title:"Cloud Engineer",location:"Remote, Global",employment_type:"full_time",review_needed:true},
    {id:"JOB-003",title:"Rejected Intern",location:"Remote, India",employment_type:"internship",review_needed:false});
  assert.equal(matchingJobs(company, "cloud remote", {employment:"full_time",location:"remote"}).length, 1);
  assert.equal(matchingJobs(company, "", {employment:"internship",location:"remote"}).length, 0);
  assert.equal(filterCompanies([company], "Cloud Engineer", "review").length, 1);
  assert.equal(filterCompanies([company], "Rejected Intern", "review").length, 0);
  assert.equal(filterCompanies([company], "", "all", [], {employment:"internship",location:"remote"}).length, 0);
  assert.equal(filterCompanies([company], "", "saved", [], {savedDomains:[company.domain]}).length, 1);
  assert.equal(filterCompanies([company], "", "saved").length, 0);
});

test("sorting is stable and leaves discovery order intact", () => {
  const first = snapshot().companies[0];
  first.name = "Zulu";
  const second = {...structuredClone(first),name:"Alpha",domain:"alpha.example",checked_on:"2026-10-23"};
  first.checked_on = "2026-10-21";
  const companies = [first, second];
  for (const sort of ["name", "roles", "verified"]) assert.equal(filterCompanies(companies, "", "all", [], {sort})[0].name, "Alpha");
  assert.equal(companies[0].name, "Zulu");
});

test("saved companies tolerate corrupt and inaccessible browser storage", () => {
  assert.deepEqual(readSavedCompanies({getItem:()=>'["example.com","example.com"]'}), ["example.com"]);
  for (const value of ["not JSON", "{}", '[42]']) assert.deepEqual(readSavedCompanies({getItem:()=>value}), []);
  assert.deepEqual(readSavedCompanies({getItem:()=>{throw Error("Storage disabled");}}), []);
});

test("role highlights are searchable and malformed imports cannot replace the page", () => {
  const data = snapshot();
  const job = data.companies[0].jobs[0];
  job.details = {summary:"Build reliable APIs.", requirements:["Production incident response."], skills:["PostgreSQL"], preferred:["Rust experience is optional."], stack:["Kubernetes"]};
  for (const query of ["PostgreSQL", "incident response", "Rust", "Kubernetes"]) assert.equal(filterCompanies(data.companies, query, "review").length, 1);
  assert.equal(validSnapshot(data), true);
  assert.equal(validRoleDetails(undefined), true);
  for (const value of [[], "text", {summary:{}}, {requirements:"Python"}, {skills:[{}]}, {preferred:[42]}, {stack:Array(9).fill("Go")}]) {
    job.details = value;
    assert.equal(validSnapshot(data), false);
  }
});

test("discovery health distinguishes current, stale, partial and unverified searches", () => {
  const clock = Date.parse("2026-10-23T09:30:00+05:30");
  const data = {discovery_at:"2026-10-23T09:00:00+05:30",discovery_counts:{sources_checked:7,sources_failed:0}};
  assert.equal(discoveryHealth(data, clock).state, "current");
  data.discovery_counts.sources_failed = 1;
  assert.equal(discoveryHealth(data, clock).state, "partial");
  data.discovery_at = "2026-10-21T09:00:00+05:30";
  assert.equal(discoveryHealth(data, clock).state, "stale");
  assert.equal(discoveryHealth({}, clock).state, "unknown");
  data.discovery_at = "2026-10-24T09:00:00+05:30";
  assert.equal(discoveryHealth(data, clock).state, "unknown");
  const invalid = snapshot();
  invalid.discovery_counts = {sources_checked:1,sources_failed:2};
  assert.equal(validSnapshot(invalid), false);
  invalid.discovery_counts = {sources_checked:"seven"};
  assert.equal(validSnapshot(invalid), false);
});

test("research expires after thirty days and missing dates never become confirmed", () => {
  assert.equal(staleResearch({checked_on:"2026-10-01"}, Date.parse("2026-10-22T09:00:00+05:30")), false);
  assert.equal(staleResearch({checked_on:"2026-10-01"}, Date.parse("2026-11-15T09:00:00+05:30")), true);
  assert.equal(staleResearch({}), true);
});

test("search covers names, sectors and skills while preserving review filters", () => {
  const companies = snapshot().companies;
  assert.equal(filterCompanies(companies, " PYTHON ", "all").length, 1);
  assert.equal(filterCompanies(companies, "backend", "review").length, 1);
  assert.equal(filterCompanies(companies, "unrelated", "all").length, 0);
  assert.equal(filterCompanies(companies, "", "leadership").length, 0);
});

test("imports reject raw ledgers, malformed fields and inconsistent role counts", () => {
  assert.equal(validSnapshot(snapshot()), true);
  assert.equal(validSnapshot({opportunities:[]}), false);
  for (const mutate of [data => {data.companies[0].tags = {};}, data => {data.review_count = 42;},
    data => {data.companies[0].ceo = {name:{}};}, data => {data.companies.push(data.companies[0]);}]) {
    const data = snapshot(); mutate(data); assert.equal(validSnapshot(data), false);
  }
});

test("snapshot files are checked before they can replace current page data", async () => {
  const valid = JSON.stringify(snapshot());
  assert.deepEqual(await readSnapshotFile({size:valid.length,text:async()=>valid}), snapshot());
  await assert.rejects(readSnapshotFile({size:4_000_001}), /smaller than 4 MB/);
  await assert.rejects(readSnapshotFile({size:10,text:async()=>"not JSON"}), /not valid JSON/);
  await assert.rejects(readSnapshotFile({size:2,text:async()=>"{}"}), /companies.json/);
});

test("daily companies use the India date and do not label yesterday as today", () => {
  assert.equal(istDay(new Date("2026-10-22T20:00:00Z")), "2026-10-23");
  const data = snapshot();
  data.daily_selection = {date:istDay(), domains:["example.com"]};
  assert.deepEqual(todayDomains(data), ["example.com"]);
  assert.equal(filterCompanies(data.companies, "", "today", todayDomains(data)).length, 1);
  data.daily_selection.date = "2000-01-01";
  assert.deepEqual(todayDomains(data), []);
  assert.equal(filterCompanies(data.companies, "", "today", todayDomains(data)).length, 0);
  data.daily_selection.domains = {};
  assert.equal(validSnapshot(data), false);
});
