export function dateText(value) {
  if (!value) return "Date not disclosed";
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00+05:30` : value);
  return Number.isNaN(parsed.getTime()) ? "Date unconfirmed" : new Intl.DateTimeFormat("en-IN", {
    day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata",
  }).format(parsed);
}

export function safeURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function staleResearch(company, clock = Date.now()) {
  const checked = Date.parse(`${company.checked_on}T23:59:59+05:30`);
  return Boolean(company.research_stale || !company.checked_on || !Number.isFinite(checked) || clock - checked > 30 * 86400000);
}

export function checkedCEO(company) {
  return Boolean(company.ceo?.name && safeURL(company.ceo.source) && !staleResearch(company));
}

export function istDay(clock = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Kolkata",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(clock);
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

export function todayDomains(snapshot, day = istDay()) {
  return snapshot?.daily_selection?.date === day ? snapshot.daily_selection.domains : [];
}

export function employmentLabel(value) {
  return ({internship:"Internship", full_time:"Full time", graduate_programme:"Graduate programme", part_time:"Part time"})[value] || "Type unconfirmed";
}

function companyText(company) {
  return [company.name, company.sector, company.about, company.ceo?.name, company.recent_update?.title, ...(company.tags || [])].filter(Boolean).join(" ").toLocaleLowerCase();
}

export function matchingJobs(company, query = "", {employment = "all", location = ""} = {}) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const place = location.toLocaleLowerCase().trim();
  return company.jobs.filter(job => {
    const details = job.details;
    const highlights = [details?.summary, ...(details?.requirements || []), ...(details?.skills || []), ...(details?.preferred || []), ...(details?.stack || [])].filter(Boolean).join(" ");
    const text = `${companyText(company)} ${job.title} ${job.location} ${employmentLabel(job.employment_type)} ${highlights}`.toLocaleLowerCase();
    return job.review_needed && (employment === "all" || job.employment_type === employment) &&
      (!place || job.location.toLocaleLowerCase().includes(place)) && terms.every(term => text.includes(term));
  });
}

export function filterCompanies(companies, query, filter, dailyDomains = [], options = {}) {
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  const roleFilters = (options.employment && options.employment !== "all") || options.location?.trim();
  const result = companies.filter(company => {
    const roles = matchingJobs(company, query, options);
    const companyMatch = terms.every(term => companyText(company).includes(term));
    return (roles.length > 0 || companyMatch && !roleFilters && !["review", "today"].includes(filter)) &&
      (filter !== "leadership" || checkedCEO(company)) &&
      (filter !== "today" || dailyDomains.includes(company.domain)) &&
      (filter !== "saved" || (options.savedDomains || []).includes(company.domain));
  });
  const nameOrder = (a, b) => a.name.localeCompare(b.name, "en", {sensitivity:"base"});
  if (options.sort === "name") result.sort(nameOrder);
  if (options.sort === "roles") result.sort((a,b) => matchingJobs(b, query, options).length - matchingJobs(a, query, options).length || nameOrder(a,b));
  if (options.sort === "verified") result.sort((a,b) => (Date.parse(b.checked_on) || 0) - (Date.parse(a.checked_on) || 0) || nameOrder(a,b));
  return result;
}

export function readSavedCompanies(storage) {
  try {
    const saved = JSON.parse(storage.getItem("job-search:saved-companies") || "[]");
    return Array.isArray(saved) && saved.length <= 1000 && saved.every(item => typeof item === "string") ? [...new Set(saved)] : [];
  } catch { return []; }
}

export function discoveryHealth(snapshot, clock = Date.now()) {
  const checked = Date.parse(snapshot?.discovery_at);
  const age = clock - checked;
  const counts = snapshot?.discovery_counts;
  if (!Number.isFinite(checked) || age < -300000 || !Number.isInteger(counts?.sources_checked) || counts.sources_checked <= 0) return {state:"unknown", label:"Discovery not verified"};
  if (age > 86400000) return {state:"stale", label:"Discovery needs a refresh"};
  if (counts.sources_failed > 0) return {state:"partial", label:counts.sources_failed >= counts.sources_checked ? "Sources unavailable" : "Some sources need attention"};
  return {state:"current", label:"Discovery up to date"};
}

export function validRoleDetails(value) {
  if (value == null) return true;
  if (typeof value !== "object" || Array.isArray(value)) return false;
  const text = item => item == null || typeof item === "string" && item.length <= 1000;
  return [value.summary, value.source, value.method].every(text) &&
    ["requirements", "skills", "preferred", "stack"].every(key => value[key] == null || Array.isArray(value[key]) && value[key].length <= 8 && value[key].every(item => typeof item === "string" && item.length <= 1000));
}

export function validSnapshot(value) {
  const text = item => item == null || typeof item === "string";
  if (value?.kind !== "job_agent_company_snapshot" || value.schema_version !== 1 || typeof value.generated_at !== "string" ||
      !Array.isArray(value.companies) || value.companies.length > 1000 || !Number.isInteger(value.review_count) || value.review_count < 0) return false;
  if (!text(value.discovery_at) || (value.discovery_counts != null &&
      !["board_roles_seen", "sources_checked", "sources_failed", "new_leads"].every(key => value.discovery_counts[key] == null || Number.isInteger(value.discovery_counts[key]) && value.discovery_counts[key] >= 0))) return false;
  if ((value.discovery_counts?.sources_failed || 0) > (value.discovery_counts?.sources_checked || 0)) return false;
  if (value.daily_selection != null && (typeof value.daily_selection.date !== "string" || !Array.isArray(value.daily_selection.domains) || !value.daily_selection.domains.every(domain => typeof domain === "string"))) return false;
  const domains = new Set();
  for (const company of value.companies) {
    if (!company || typeof company.name !== "string" || company.name.length > 200 || typeof company.domain !== "string" || domains.has(company.domain) ||
        !Number.isInteger(company.review_count) || company.review_count < 0 || !Number.isInteger(company.recorded_count) ||
        !Array.isArray(company.jobs) || company.jobs.length > 10000 ||
        ![company.sector, company.about, company.checked_on, company.research_note, company.ceo?.name, company.ceo?.title,
          company.recent_update?.title, company.recent_update?.summary, company.recent_update?.published_on].every(text) ||
        (company.tags != null && (!Array.isArray(company.tags) || !company.tags.every(tag => typeof tag === "string"))) ||
        !company.jobs.every(job => job && [job.id, job.title, job.location, job.status].every(item => typeof item === "string") && text(job.application_url) && text(job.employment_type) && text(job.last_verified_at) && validRoleDetails(job.details) && typeof job.review_needed === "boolean") ||
        company.recorded_count !== company.jobs.length || company.review_count !== company.jobs.filter(job => job.review_needed).length) return false;
    domains.add(company.domain);
  }
  return value.review_count === value.companies.reduce((total, company) => total + company.review_count, 0);
}

export async function readSnapshotFile(file) {
  if (file.size > 4_000_000) throw new Error("Choose a company snapshot smaller than 4 MB.");
  let data;
  try { data = JSON.parse(await file.text()); }
  catch { throw new Error("This file is not valid JSON."); }
  if (!validSnapshot(data)) throw new Error("Choose the .companies.json file from a job-search report.");
  return data;
}
