"""Render a large dummy graph to eyeball the visualizer at demo scale.

Run from the repo root:
    PYTHONPATH=src .venv/bin/python scripts/render_demo.py
    xdg-open out/graph-demo.html

Everything is fake; no API calls. The point is volume: six department
clusters, ~35 cited file nodes with relevance notes, and a set of
permission-denied nodes, so the force layout has something to show off.
"""

from pathlib import Path

from agentic.client import viz
from agentic.contracts.messages import GatheredFile, GatherResult, VetoVerdict
from agentic.state_manager import output

PROMPT = (
    "prepare the Q3 business review: engineering delivery vs budget, "
    "marketing pipeline, product roadmap risks, and security posture"
)

# department -> [(filename, relevance note)]
CITED = {
    "engineering": [
        ("roadmap.md", "names the auth service and search rewrite as Q3 deliverables"),
        ("infra-spend-q3.csv", "actual infra spend of $41,200 against $50,000 budgeted"),
        ("oncall-postmortems/2026-07-outage.md", "July outage cost 4.5 hours of downtime"),
        ("oncall-postmortems/2026-08-cache.md", "cache stampede traced to missing TTL jitter"),
        ("hiring-plan.md", "three backend openings slipped to Q4"),
        ("arch/auth-service-design.md", "auth service design signed off 2026-06-12"),
        ("arch/search-rewrite-rfc.md", "search rewrite scoped to 6 engineer-weeks"),
        ("velocity-report.md", "sprint velocity up 12% quarter over quarter"),
    ],
    "finance": [
        ("q3-budget.csv", "line 2 gives the Q3 infra budget of $50,000"),
        ("q3-actuals.csv", "engineering actuals at 82% of budget through August"),
        ("forecast-fy26.xlsx.md", "FY26 forecast assumes 9% opex growth"),
        ("vendor-contracts/cloud-renewal.md", "cloud contract renews Oct 1 at +7%"),
        ("vendor-contracts/saas-audit.md", "14 unused SaaS seats flagged for cancellation"),
        ("headcount-costs.csv", "fully-loaded engineering headcount at $220k/quarter"),
    ],
    "marketing": [
        ("pipeline-q3.csv", "qualified pipeline of $1.2M, up 18%"),
        ("campaign-results/summer-launch.md", "summer launch drove 3,400 signups"),
        ("campaign-results/webinar-series.md", "webinar series converting at 4.1%"),
        ("brand-refresh-brief.md", "brand refresh lands mid-Q4, no Q3 spend"),
        ("competitive/landscape-2026.md", "two competitors shipped graph views this year"),
        ("events-calendar.md", "booth committed for DataConf, $30k"),
    ],
    "product": [
        ("roadmap-q3q4.md", "graph visualizer flagged as the top Q4 bet"),
        ("user-research/enterprise-interviews.md", "8 of 10 admins asked for permission audit trails"),
        ("user-research/churn-analysis.md", "churn concentrated in accounts without SSO"),
        ("metrics/activation-funnel.csv", "activation up 6 points after onboarding rework"),
        ("prds/permissions-ui.md", "permissions UI PRD blocked on security review"),
        ("prds/export-api.md", "export API scoped for Q4, needs finance sign-off"),
    ],
    "security": [
        ("posture-review-q3.md", "no criticals open; two highs in remediation"),
        ("pentest-2026-summer.md", "summer pentest found path traversal, fixed in June"),
        ("vendor-reviews/llm-providers.md", "model providers approved for internal data tier 2"),
        ("incident-log.csv", "zero reportable incidents in Q3"),
        ("access-reviews/q3-recert.md", "quarterly access recert 94% complete"),
    ],
    "legal": [
        ("contract-templates/msa-2026.md", "new MSA template cuts redline cycles by a week"),
        ("compliance/soc2-status.md", "SOC 2 Type II audit window opens November"),
        ("ip/patent-filings.md", "two provisional patents filed for graph ranking"),
    ],
}

DENIED = {
    "hr": ["comp-bands.md", "performance-reviews/q3.md", "org-changes-draft.md"],
    "legal": ["disputes/active-litigation.md", "board-minutes/2026-08.md"],
}


def main() -> None:
    files = [
        GatheredFile(
            path=path,
            department=dept,
            content=f"(dummy contents for {dept}/{path})\n{note}",
            relevance_note=note,
        )
        for dept, entries in CITED.items()
        for path, note in entries
    ]

    # Prose cites the load-bearing claims inline, the way the synthesizer is
    # meant to; every gathered file still reaches the graph via the emitted
    # "## Sources" section, so the node count doesn't depend on the body.
    answer_body = (
        "**Engineering** shipped the auth service on schedule, per the signed-off "
        "design ([[engineering/arch/auth-service-design.md]]) and the Q3 roadmap "
        "([[engineering/roadmap.md]]). Velocity is up 12% quarter over quarter, "
        "though the July outage cost 4.5 hours of downtime "
        "([[engineering/oncall-postmortems/2026-07-outage.md]]) and three backend "
        "hires slipped to Q4.\n\n"
        "**Spend** finished at 82% of budget: $41,200 actual "
        "([[engineering/infra-spend-q3.csv]]) against the $50,000 line "
        "([[finance/q3-budget.csv]]). Two watch items for Q4 — the cloud contract "
        "renews October 1 at +7% ([[finance/vendor-contracts/cloud-renewal.md]]), "
        "and finance flagged 14 unused SaaS seats for cancellation.\n\n"
        "**Marketing** grew qualified pipeline 18% to $1.2M "
        "([[marketing/pipeline-q3.csv]]), driven largely by the summer launch's "
        "3,400 signups ([[marketing/campaign-results/summer-launch.md]]).\n\n"
        "**Product** research points squarely at permissions tooling: 8 of 10 "
        "enterprise admins asked for audit trails "
        "([[product/user-research/enterprise-interviews.md]]), and churn "
        "concentrates in accounts without SSO. The permissions UI PRD is blocked "
        "on security review ([[product/prds/permissions-ui.md]]).\n\n"
        "**Security and legal** are clean going into audit season — no criticals "
        "open ([[security/posture-review-q3.md]]), zero reportable incidents, and "
        "the SOC 2 Type II window opens in November "
        "([[legal/compliance/soc2-status.md]])."
    )

    compiled = output.emit(
        prompt=PROMPT, answer_body=answer_body, files=files, revision=1
    )

    results = [
        GatherResult(
            request_id=f"{dept}-{i}",
            department=dept,
            files=[f for f in files if f.department == dept],
        )
        for i, dept in enumerate(CITED)
    ] + [
        GatherResult(request_id=f"{dept}-denied", department=dept, denied=paths)
        for dept, paths in DENIED.items()
    ]

    verdict = VetoVerdict(
        approved=True,
        reasons=[
            "checked every figure against the cited files; all supported",
            "revision 1 resolved the uncited pipeline number from revision 0",
        ],
    )

    out = viz.render_html(
        compiled, Path("out/graph-demo.html"), results=results, verdict=verdict
    )
    node_count = sum(len(v) for v in CITED.values()) + sum(
        len(v) for v in DENIED.values()
    ) + len(set(CITED) | set(DENIED)) + 1
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {node_count} nodes)")


if __name__ == "__main__":
    main()
