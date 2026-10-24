// Inject into the built index in a temporary page served by the local preview.
// Runs against the real React bundle; no production test hooks are required.
addEventListener("DOMContentLoaded", async () => {
  const output = document.createElement("output");
  output.id = "qa-result";
  const completed = [];
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  async function until(condition) {
    for (let attempt = 0; attempt < 150; attempt++) {
      if (condition()) return;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error("Timed out waiting for React state");
  }
  function search(value) {
    const input = document.querySelector("#search");
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", {bubbles:true}));
  }
  function selectFile(text) {
    const transfer = new DataTransfer();
    transfer.items.add(new File([text], "snapshot.companies.json", {type:"application/json"}));
    const input = document.querySelector("#import-file");
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", {bubbles:true}));
  }
  function select(id, value) {
    const input = document.querySelector(id);
    input.value = value;
    input.dispatchEvent(new Event("change", {bubbles:true}));
  }
  try {
    const data = await (await fetch("/companies.json")).json();
    const total = data.companies.length;
    const day = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Kolkata",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
    const daily = data.daily_selection.date === day ? data.companies.filter(company => data.daily_selection.domains.includes(company.domain) && company.review_count > 0).length : 0;
    await until(() => document.querySelector("#result-count")?.textContent.includes("Showing"));
    check(document.querySelector("[data-filter=today]").getAttribute("aria-pressed") === "true", "Daily filter is not the default");
    check(document.querySelectorAll(".card").length === daily, "Daily company selection mismatch");
    check([...document.querySelectorAll(".role-title")].every(link => link.getClientRects().length > 0), "Role titles are hidden on initial load");
    check([...document.querySelectorAll(".card")].every(card => card.querySelector(".opportunities").compareDocumentPosition(card.querySelector(".about")) & Node.DOCUMENT_POSITION_FOLLOWING), "Roles should appear before company background");
    completed.push("daily companies");
    document.querySelector("[data-filter=all]").click();
    await until(() => document.querySelectorAll(".card").length === total);
    check(Object.keys(document.querySelector("#root")).some(key => key.startsWith("__reactContainer")), "React root is missing");
    completed.push("React render");
    select("#employment-filter", "internship");
    const internships = data.companies.filter(company => company.jobs.some(job => job.review_needed && job.employment_type === "internship"));
    await until(() => document.querySelectorAll(".card").length === internships.length);
    check([...document.querySelectorAll(".role")].every(role => role.textContent.includes("Internship")), "Employment filter leaked another role type");
    select("#location-filter", "remote");
    const remote = internships.filter(company => company.jobs.some(job => job.review_needed && job.employment_type === "internship" && /remote/i.test(job.location)));
    await until(() => document.querySelectorAll(".card").length === remote.length);
    check([...document.querySelectorAll(".role")].every(role => /remote/i.test(role.textContent)), "Location filter leaked another location");
    select("#employment-filter", "all"); select("#location-filter", "");
    await until(() => document.querySelectorAll(".card").length === total);
    select("#sort-filter", "name");
    const names = data.companies.map(company => company.name).sort((a,b) => a.localeCompare(b,"en",{sensitivity:"base"}));
    await until(() => document.querySelector(".company-title h2").textContent === names[0]);
    select("#sort-filter", "recommended");
    await until(() => document.querySelector(".company-title h2").textContent === data.companies[0].name);
    completed.push("role and location filters, sorting");
    document.querySelector(".save-company").click();
    await until(() => document.querySelector(".save-company").getAttribute("aria-pressed") === "true");
    check(JSON.parse(localStorage.getItem("job-search:saved-companies")).includes(data.companies[0].domain), "Saved choice was not persisted");
    document.querySelector("[data-filter=saved]").click();
    await until(() => document.querySelectorAll(".card").length === 1);
    document.querySelector(".save-company").click();
    await until(() => document.querySelector("#empty"));
    document.querySelector("#reset").click();
    await until(() => document.querySelectorAll(".card").length === total);
    completed.push("saved companies and removal");
    search("Stripe");
    await until(() => document.querySelectorAll(".card").length === 1);
    check(document.querySelector(".company-title h2").textContent === "Stripe", "Wrong search result");
    completed.push("search");
    search("no-such-company");
    await until(() => document.querySelector("#empty"));
    document.querySelector("#reset").click();
    await until(() => document.querySelectorAll(".card").length === total);
    completed.push("empty state and reset");
    document.querySelector("[data-filter=leadership]").click();
    const leadership = data.companies.filter(company => company.ceo?.name && !company.research_stale).length;
    await until(() => document.querySelectorAll(".card").length === leadership);
    completed.push("CEO filter");
    document.querySelector("[data-filter=all]").click();
    await until(() => document.querySelectorAll(".card").length === total);
    const expectedApplications = data.companies.flatMap(company => company.jobs.filter(job => job.review_needed));
    const roleTitles = [...document.querySelectorAll(".role-title")];
    check(roleTitles.length === expectedApplications.length, "A role title is missing");
    check(roleTitles.every((link, index) => link.textContent.includes(expectedApplications[index].title) && link.getClientRects().length > 0), "Role titles must be visible without expanding a section");
    const renderedRoles = [...document.querySelectorAll(".role")];
    expectedApplications.forEach((job, index) => {
      const role = renderedRoles[index];
      if (job.details?.summary) check(role.querySelector(".role-summary")?.textContent.includes(job.details.summary), "Role summary is missing or incorrect");
      check(role.querySelectorAll(".role-requirements li").length === (job.details?.requirements?.length || 0), "Requirements are missing");
      check(role.querySelectorAll(".role-skills span").length === (job.details?.skills?.length || 0), "Skills are missing");
      check(role.querySelectorAll(".role-preferred li").length === (job.details?.preferred?.length || 0), "Optional skills must remain separate");
      check(role.querySelector("a.full-jd")?.href === job.url, "Full job description source is incorrect");
    });
    completed.push("role summaries, requirements and skills");
    const applyLinks = [...document.querySelectorAll("a.apply-link")];
    check(applyLinks.length === expectedApplications.length, "A role is missing its application link");
    check(applyLinks.every((link, index) => link.href === (expectedApplications[index].application_url || expectedApplications[index].url) && link.target === "_blank"), "Application links do not match the stored destinations");
    check(applyLinks.every(link => link.getClientRects().length > 0), "Apply links are hidden");
    completed.push("visible role titles");
    completed.push("application links");
    document.querySelector(".sources summary").click();
    check(document.querySelector(".sources").open, "Source expansion failed");
    completed.push("roles and sources");
    selectFile("not JSON");
    await until(() => document.querySelector("#import-message").textContent.includes("Unable"));
    check(document.querySelectorAll(".card").length === total, "Invalid import replaced existing data");
    completed.push("invalid import recovery");
    const response = await fetch("/companies.json");
    const snapshot = await response.json();
    snapshot.companies = snapshot.companies.slice(0,1);
    snapshot.review_count = snapshot.companies[0].review_count;
    snapshot.companies[0].name = '<img src=x onerror="window.qaInjected=true">';
    snapshot.companies[0].website = "javascript:alert(1)";
    snapshot.companies[0].jobs[0].details = {summary:'<img src=x onerror="window.qaInjected=true">',requirements:["Safe requirement"],skills:["Safe skill"],source:"javascript:alert(1)",method:"reviewed"};
    selectFile(JSON.stringify(snapshot));
    await until(() => document.querySelectorAll(".card").length === 1 && document.querySelector("#import-message").textContent.includes("New snapshot"));
    check(!document.querySelector(".company-title img") && !window.qaInjected, "Imported markup executed");
    check(!document.querySelector("a.website"), "Unsafe external URL remained clickable");
    check(!document.querySelector(".role-brief img") && !window.qaInjected, "Role summary markup executed");
    check(document.querySelector("a.full-jd")?.href === snapshot.companies[0].jobs[0].url, "Unsafe summary source did not fall back to the official JD");
    completed.push("snapshot import and escaping");
    output.textContent = "PASS: " + completed.join(", ");
  } catch (error) {
    output.textContent = "FAIL: " + error.message + "; completed " + completed.join(", ");
  }
  document.body.append(output);
});
