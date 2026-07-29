"""Test JobSpy scraping directly to debug job count issues."""

from jobspy import scrape_jobs

SEARCH_TERMS = ["software engineer", "backend developer"]
LOCATIONS = ["Manila", None]
SOURCES = ["indeed", "linkedin"]
HOURS_OLD = 24
RESULTS = 50

print(f"Testing JobSpy: terms={SEARCH_TERMS}, locations={LOCATIONS}")
print(f"Sources={SOURCES}, hours_old={HOURS_OLD}, results={RESULTS}")
print("=" * 60)

total = 0
for term in SEARCH_TERMS:
    for loc in LOCATIONS:
        loc_label = loc or "(any)"
        for site in SOURCES:
            try:
                df = scrape_jobs(
                    site_name=site,
                    search_term=term,
                    location=loc,
                    results_wanted=RESULTS,
                    hours_old=HOURS_OLD,
                    country_indeed="Philippines" if loc else "USA",
                )
                count = len(df)
                total += count
                print(f"  {site:12s} | {term:25s} | {loc_label:10s} | {count:3d} jobs")
                if count > 0:
                    titles = df["title"].head(3).tolist()
                    for t in titles:
                        print(f"           → {t}")
            except Exception as e:
                print(f"  {site:12s} | {term:25s} | {loc_label:10s} | ERROR: {e}")

print("=" * 60)
print(f"TOTAL: {total} jobs across all queries")
