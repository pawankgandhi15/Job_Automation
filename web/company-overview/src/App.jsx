import { useEffect, useMemo, useRef, useState } from "react";
import CompanyCard from "./CompanyCard.jsx";
import { dateText, discoveryHealth, employmentLabel, filterCompanies, istDay, matchingJobs, readSavedCompanies, readSnapshotFile, todayDomains, validSnapshot } from "./snapshot.js";

const filters = [["today", "Today’s companies"], ["all", "All companies"], ["review", "Roles to review"], ["saved", "Saved"], ["leadership", "CEO confirmed"]];

export default function App() {
  const [data, setData] = useState(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("today");
  const [employment, setEmployment] = useState("all");
  const [location, setLocation] = useState("");
  const [sort, setSort] = useState("recommended");
  const [saved, setSaved] = useState(() => { try { return readSavedCompanies(localStorage); } catch { return []; } });
  const [day, setDay] = useState(istDay);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [imported, setImported] = useState(false);
  const inputRef = useRef(null);
  const searchRef = useRef(null);
  const requestRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    let pending = false;
    async function load() {
      setDay(istDay());
      if (controller.signal.aborted || pending) return;
      pending = true;
      try {
        const response = await fetch(`${import.meta.env.BASE_URL}companies.json`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Company snapshot could not be loaded.");
        const snapshot = await response.json();
        if (!validSnapshot(snapshot)) throw new Error("Company snapshot needs to be regenerated.");
        if (!controller.signal.aborted) { setData(snapshot); setLoadError(""); }
      } catch (error) {
        if (!controller.signal.aborted) setLoadError(`${error.message} You can load a company JSON file below.`);
      } finally {
        pending = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    load();
    const interval = setInterval(() => { if (document.visibilityState === "visible") load(); }, 60000);
    window.addEventListener("focus", load);
    return () => { controller.abort(); clearInterval(interval); window.removeEventListener("focus", load); };
  }, []);

  const options = useMemo(() => ({employment, location, sort, savedDomains:saved}), [employment, location, sort, saved]);
  const companies = useMemo(() => data ? filterCompanies(data.companies, query, filter, todayDomains(data, day), options) : [], [data, query, filter, day, options]);
  const roleCount = companies.reduce((total, company) => total + matchingJobs(company, query, options).length, 0);
  const dailyCount = data ? filterCompanies(data.companies, "", "today", todayDomains(data, day)).length : 0;
  const savedCount = data ? data.companies.filter(company => saved.includes(company.domain)).length : 0;
  const health = discoveryHealth(data);
  const counts = data?.discovery_counts;
  const types = useMemo(() => [...new Set(data?.companies.flatMap(company => company.jobs.filter(job => job.review_needed).map(job => job.employment_type).filter(Boolean)) || [])].sort(), [data]);
  const locations = useMemo(() => [...new Set(data?.companies.flatMap(company => company.jobs.filter(job => job.review_needed).map(job => job.location)) || [])].sort(), [data]);
  const hasFilters = query || employment !== "all" || location || sort !== "recommended";

  function reset() { setQuery(""); setEmployment("all"); setLocation(""); setSort("recommended"); setFilter("all"); searchRef.current?.focus(); }
  function toggleSaved(company) {
    const next = saved.includes(company.domain) ? saved.filter(domain => domain !== company.domain) : [...saved, company.domain];
    setSaved(next);
    try {
      localStorage.setItem("job-search:saved-companies", JSON.stringify(next));
      setMessage(`${company.name} ${next.includes(company.domain) ? "saved" : "removed from saved companies"}. Saved companies stay in this browser.`);
    } catch { setMessage("Saved for this tab. Browser storage is unavailable, so this choice will not survive a reload."); }
  }

  async function importSnapshot(event) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) return;
    try {
      const next = await readSnapshotFile(file);
      requestRef.current?.abort();
      setData(next); reset(); setDay(istDay()); setLoading(false); setLoadError(""); setImported(true);
      setMessage("New snapshot loaded in this browser tab. No file was uploaded to the server.");
    } catch (error) { setMessage(`Unable to load snapshot. ${error.message}`); }
    input.value = "";
  }

  return <>
    <a className="skip" href="#companies">Skip to companies</a>
    <header className="topbar"><div className="topbar-inner">
      <a className="brand" href="./"><span className="brand-mark" aria-hidden="true">j.</span> Job search<span className="brand-separator">/</span><span className="brand-section">Company briefs</span></a>
      <a className="private-label" href="?view=review"><span className="dot" /> Open review queue ↗</a>
    </div></header>
    <main>
      <section className="intro" aria-labelledby="heading">
        <div><p className="eyebrow">A little context before you apply</p><h1 id="heading">Get to know the company.</h1><p className="intro-copy">Explore the roles, save your favourites, and check the details that matter.</p></div>
        <div className="snapshot"><span className="snapshot-label">JOB SEARCH SNAPSHOT</span><time id="snapshot-date">{loading ? "Loading…" : data ? dateText(data.generated_at) : "No snapshot loaded"}</time><span id="snapshot-meta">{data ? `${(counts?.board_roles_seen || 0).toLocaleString("en-IN")} postings checked` : ""}</span></div>
      </section>
      <section className="summary-strip" aria-label="Overview"><p><strong id="company-count">{data?.companies.length ?? "—"}</strong> companies in your search</p><p><strong id="review-count">{data?.review_count ?? "—"}</strong> roles to review</p><p><strong>{savedCount}</strong> saved {savedCount === 1 ? "company" : "companies"}</p><p className="quiet">Company context + official sources</p></section>
      {data && <section className={`discovery-health ${health.state}`} aria-label="Discovery status">
        <div><span className="health-dot" aria-hidden="true" /><strong>{health.label}</strong><span>Last search: {dateText(data.discovery_at)}</span></div>
        <p>{counts?.sources_checked > 0 ? `${counts.sources_checked - (counts.sources_failed || 0)} of ${counts.sources_checked} sources reached` : "Source checks unavailable"}{counts?.new_leads > 0 ? ` · ${counts.new_leads} new leads` : ""} · {imported ? "Imported snapshot" : "Published snapshot"}</p>
      </section>}
      <section className="daily-note" aria-label="Daily company selection"><strong>{dailyCount} new {dailyCount === 1 ? "company" : "companies"} for {dateText(day)}</strong><span>Different employers each day. Earlier results stay in All companies.</span></section>
      <section className="toolbar" aria-label="Find a company">
        <div className="filters" role="group" aria-label="Filter companies">{filters.map(([key, label]) => <button type="button" key={key} data-filter={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}{key === "saved" && savedCount > 0 ? ` (${savedCount})` : ""}</button>)}</div>
        <label className="search"><span aria-hidden="true">⌕</span><span className="sr-only">Search companies, roles or locations</span><input ref={searchRef} id="search" type="search" placeholder="Company, role, location…" autoComplete="off" value={query} onChange={event => setQuery(event.target.value)} /></label>
      </section>
      <section className="advanced-filters" aria-label="Refine roles">
        <label>Role type<select id="employment-filter" value={employment} onChange={event => setEmployment(event.target.value)}><option value="all">All role types</option>{types.map(type => <option value={type} key={type}>{employmentLabel(type)}</option>)}</select></label>
        <label>Location<select id="location-filter" value={location} onChange={event => setLocation(event.target.value)}><option value="">All locations</option><option value="remote">Remote mentioned</option>{locations.map(place => <option value={place} key={place}>{place}</option>)}</select></label>
        <label>Sort companies<select id="sort-filter" value={sort} onChange={event => setSort(event.target.value)}><option value="recommended">Discovery order</option><option value="roles">Most matching roles</option><option value="name">Name A–Z</option><option value="verified">Recently researched</option></select></label>
        {hasFilters && <button className="clear-filters" type="button" onClick={reset}>Clear filters</button>}
      </section>
      <p id="result-count" className="result-count" aria-live="polite">{loading ? "Loading company briefs…" : `Showing ${companies.length} of ${data?.companies.length ?? 0} companies · ${roleCount} matching ${roleCount === 1 ? "role" : "roles"}`}</p>
      {loadError && <p className="empty" role="alert">{loadError}</p>}
      <section id="companies" className="cards" aria-label="Company overviews" tabIndex={-1} aria-busy={loading}>{companies.map((company, index) => <CompanyCard key={company.domain} company={company} index={index} jobs={matchingJobs(company, query, options)} saved={saved.includes(company.domain)} onSave={() => toggleSaved(company)} />)}</section>
      {!loading && data && companies.length === 0 && <div id="empty" className="empty"><span aria-hidden="true">⌕</span><h2>{filter === "saved" && !hasFilters ? "Your saved companies will appear here" : filter === "today" && !hasFilters ? "No new companies selected for today" : "No companies found"}</h2><p>{filter === "saved" && !hasFilters ? "Use the star on any company to keep it handy in this browser." : filter === "today" && !hasFilters ? "The daily search returns fewer companies when new evidence is insufficient. You can still review earlier results." : "Try another role or location, or clear the filters."}</p><button id="reset" type="button" onClick={reset}>{["today", "saved"].includes(filter) && !hasFilters ? "Browse all companies" : "Clear filters"}</button></div>}
      <footer>
        <p>Roles need eligibility review before applying. Remote locations still need confirmation that you can work from India.</p>
        <p>Daily discovery updates local files. This hosted snapshot changes when republished. Company research may need a recheck after 30 days.</p>
        <div className="import-row"><button id="import-button" type="button" onClick={() => inputRef.current?.click()}>Load newer snapshot <span aria-hidden="true">↥</span></button><input ref={inputRef} id="import-file" type="file" accept="application/json,.json" hidden onChange={importSnapshot} /><span>Use the company JSON from a newer job-search report.</span></div>
        <p id="import-message" role="status">{message}</p>
      </footer>
    </main>
  </>;
}
