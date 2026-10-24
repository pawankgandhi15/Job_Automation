import { checkedCEO, dateText, employmentLabel, safeURL, staleResearch } from "./snapshot.js";

const palettes = [["#eeebff", "#6654b8"], ["#e4f5e9", "#308858"], ["#e8f1fc", "#517aad"], ["#fff0df", "#b17c38"], ["#f1ebf5", "#8b669f"]];

export function ExternalLink({ href, children, ...props }) {
  const url = safeURL(href);
  return url ? <a {...props} href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <span {...props}>{children}</span>;
}

export default function CompanyCard({ company, index, jobs, saved = false, onSave }) {
  const [backgroundColor, color] = palettes[index % palettes.length];
  const mark = company.name === "Stripe" ? "s" : company.name === "Supabase" ? "S" : company.name.split(/\s+/).slice(0, 2).map(word => word[0]).join("");
  const update = company.recent_update;
  const pending = jobs || company.jobs.filter(job => job.review_needed);
  const excluded = company.recorded_count - company.review_count;
  return (
    <article className="card">
      <div className="card-heading">
        <span className="company-mark" aria-hidden="true" style={{ backgroundColor, color }}>{mark}</span>
        <div className="company-title"><h2>{company.name}</h2><p className="sector">{company.sector || "Company research pending"}</p></div>
        <div className="company-actions">
          <button className="save-company" type="button" aria-pressed={saved} aria-label={`${saved ? "Unsave" : "Save"} ${company.name}`} onClick={onSave}>{saved ? "★" : "☆"}</button>
          <ExternalLink className="website" href={company.website} aria-label={`Official ${company.name} website`}>↗</ExternalLink>
        </div>
      </div>
      <section className="opportunities" aria-label={`Roles at ${company.name}`}>
        <div className="opportunities-heading"><h3>Roles to review</h3><span className="role-count">{pending.length} {pending.length === 1 ? "role" : "roles"}{pending.length < company.review_count ? " matching filters" : ""}</span></div>
        <div className="role-list">
          {pending.map(job => {
            const applicationURL = safeURL(job.application_url);
            const destination = applicationURL || safeURL(job.url);
            return <div className="role" key={job.id}>
              <div className="role-heading">
                <ExternalLink className="role-title" href={job.url}>{job.title} ↗</ExternalLink>
                {destination && <ExternalLink className="apply-link" href={destination} aria-label={`${applicationURL ? "Apply for" : "View opening for"} ${job.title} at ${company.name}`}>
                  {applicationURL ? "Apply" : "View opening"} <span aria-hidden="true">↗</span>
                </ExternalLink>}
              </div>
              <small>{job.location} · {employmentLabel(job.employment_type)}</small>
              <small>Vacancy checked {dateText(job.last_verified_at)} · {job.id}</small>
              <div className="role-brief">
                {job.details?.summary && <p className="role-summary"><strong>About the role</strong>{job.details.summary}</p>}
                {job.details?.requirements?.length > 0 && <div className="role-requirements"><strong>Key requirements</strong><ul>{job.details.requirements.map((requirement, number) => <li key={number}>{requirement}</li>)}</ul></div>}
                {job.details?.skills?.length > 0 && <div className="role-skill-group"><strong>Skills</strong><div className="role-skills">{job.details.skills.map(skill => <span key={skill}>{skill}</span>)}</div></div>}
                {job.details?.stack?.length > 0 && <p className="role-extra"><strong>Team’s stack:</strong> {job.details.stack.join(" · ")}</p>}
                {job.details?.preferred?.length > 0 && <div className="role-preferred"><strong>Preferred, optional</strong><ul>{job.details.preferred.map((item, number) => <li key={number}>{item}</li>)}</ul></div>}
                {(!job.details || job.details.method === "unavailable") && <p className="role-extra">A role summary, requirements and skills have not yet been verified.</p>}
                <ExternalLink className="full-jd" href={safeURL(job.details?.source) || job.url}>Full job description ↗</ExternalLink>
              </div>
              <div className="review-state">{job.status === "NEEDS_CLARIFICATION" ? "Eligibility or application details need confirmation" : "Review required before any application"}</div>
            </div>;
          })}
          {excluded > 0 && <p className="role-note">{excluded} other recorded {excluded === 1 ? "role is" : "roles are"} outside the current review queue.</p>}
          {pending.length === 0 && <p className="role-note">No roles currently awaiting review.</p>}
        </div>
      </section>
      <p className="about">{company.about || "A source-confirmed company description has not been added yet."}</p>
      <div className="fact-row">
        <span className="fact-label">CEO</span>
        <div className="leader">{checkedCEO(company) ? <>
          <ExternalLink href={company.ceo.source}>{company.ceo.name} ↗</ExternalLink>
          <small>{company.ceo.title || "CEO"}</small>
        </> : <span className="unverified">{company.ceo?.name && staleResearch(company) ? `${company.ceo.name} · recheck needed` : "Not verified"}</span>}</div>
      </div>
      <div className="update">
        <div className="update-label"><span>RECENT PROJECT / UPDATE</span><time>{update ? dateText(update.published_on) : ""}</time></div>
        <h3>{update?.title || "Recent project not verified"}</h3>
        <p>{update?.summary || "No dated product or project update has been confirmed from the official sources reviewed."}</p>
        {safeURL(update?.source) && <ExternalLink className="update-source" href={update.source}>{update.published_on ? "Read announcement" : "View company source"} ↗</ExternalLink>}
      </div>
      <div className="tags" aria-label="Company focus">{(company.tags || []).slice(0, 4).map((tag, number) => <span className="tag" key={`${tag}-${number}`}>{tag}</span>)}</div>
      <details className="sources">
        <summary>Sources &amp; verification <span className="checked-date">{company.checked_on ? `Checked ${dateText(company.checked_on)}` : "Research needed"}</span></summary>
        <div className="source-list">
          {safeURL(company.about_source) && <ExternalLink href={company.about_source}>Company overview ↗</ExternalLink>}
          {safeURL(company.ceo?.source) && <ExternalLink href={company.ceo.source}>Leadership ↗</ExternalLink>}
          {safeURL(update?.source) && <ExternalLink href={update.source}>Project / update ↗</ExternalLink>}
          {staleResearch(company) && <p>Company research is missing or more than 30 days old. Recheck leadership and recent news.</p>}
          {company.research_note && <p>{company.research_note}</p>}
          <p>Role counts come from the job-search ledger. A listed role is not an approved application.</p>
        </div>
      </details>
    </article>
  );
}
