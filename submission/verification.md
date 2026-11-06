# Submission acceptance checklist — 6 November 2026

This is an evidence record, not a guarantee of future service availability.

| Check | Result and scope |
|---|---|
| Public product homepage + health | Fresh anonymous HTTP 200 at game-on-victoria.vercel.app |
| Public demo homepage + health | Fresh anonymous HTTP 200 at game-on-w3-demo.vercel.app |
| Real hosted authentication | Owner operated sign-in; agent observed authenticated synthetic acceptance group with two persisted members |
| Hosted game + maps | Existing voted 90-minute Singles game loaded; two suburb avatar markers and venue map controls observed |
| Hosted Ernie | Fresh deterministic run completed, three recommendations, explicit built-in-rule label |
| Missing price | Correctly showed Price to confirm for the 90-minute context; did not reuse a one-hour quote |
| Hosted organiser approval | Approved Carlson Reserve for handoff; address, exact time, two players and Not booked displayed |
| Hosted assistant brief | Clicked Send to Personal Assistant; Copied! and explicit not-booked completion observed. Clipboard body not extracted from owner's browser |
| New invitation/join/vote creation | Not repeated in this submission check; existing authenticated group/votes reused. Automated source tests cover these paths |
| Product source suite | PASS: 282 run in 302.980s, 227 passed, 55 optional PostgreSQL tests skipped; no shared database tests enabled |
| Demo desktop/mobile | PASS: existing browser_smoke.py against hosted demo, --live-map, widths 1280/375, A/B/C/B/A and reset |
| Demo map/price/approval/copy/calendar | PASS with fictional data. Clipboard privacy and tentative .ics asserted; official URL navigation intercepted, no provider request |
| Agent Brain replay | PASS: existing browser_brain.py at both widths, A/B/C, event ordering, empty first search, second search, help state, reset/replay; zero extra plan/approve requests during replay |
| Demo offline tests | PASS: test_demo.py 2 tests in 36.013s; test_graph_events.py 1 test in 6.489s |
| Anonymous security boundaries | Auth config200; account and nonexistent-group API401; environment/internal handoff paths404 |
| Real Playbypoint evidence | Historical I-069 only: exact approved Court 3 summary at timestamp in historical-live-evidence.json. No fresh provider test |
| Three-minute video | Actual hosted fictional scenario recordings, edited timing; synthetic local narration; historical pricing screenshot labelled |
| Documents | Three-page PDF rendered and all pages visually reviewed; both SVG diagrams rendered and reviewed |
| Privacy review | Selected source snapshot, fresh history; no private database, auth state, internal logs, telemetry or handoffs. Deployment endpoint/project IDs replaced; synthetic test credentials are not live credentials |

## Commands and reproducibility

Product: `python -m unittest discover -s tests -v` from the clean submission root.
Browser fixtures use installed local Chromium, not live provider pages. PostgreSQL tests skip without an explicitly configured disposable TEST_DATABASE_URL.

Demo browser acceptance uses the existing public [Game-on-LIVE tests](https://github.com/shyamsrini364/Game-on-LIVE/tree/main/tests):

```sh
python tests/browser_smoke.py https://game-on-w3-demo.vercel.app --live-map
python tests/browser_brain.py https://game-on-w3-demo.vercel.app
```

The first packaging run found a missing `scripts/one_court_pilot_demo.py` support module. It was restored from existing product source; its isolated test passed. This was a submission packaging error, not a product feature change. Test-browser executable configuration now permits an environment override while retaining the original default.

A subsequent full run had one browser tool timeout while video encoding was active (282 run, 55 skipped). The exact test passed alone in 5.423s without code changes; the final complete rerun then passed as recorded above. Timing contention is a plausible explanation, not proven root cause. No deadline safeguard was loosened.

Inspect [captured demo execution events](demo-execution-evidence.json) for the actual ordered graph responses behind the video. Group data and provider observations in this file are fictional; this is separate from the [historical local quote and graph-consumption record](historical-live-evidence.json).

## Release and limitation checklist

- Product hosting is unchanged: release b85c583, through I-065. Source includes I-069's local extension.
- Existing demo implementation remains unchanged. Provider responses in A/B/C are fictional; graph execution is real and deterministic.
- No fresh full two-account creation/invitation journey or fresh-session recovery test is claimed for this checkpoint.
- No live unavailable-slot replanning, hosted provider browser, transferable prefilled checkout, automatic booking or payment is verified.
- Historical Pickleholic total is $51.25 with provider selector 1; currency code and separate tax unknown. It is not a universal venue rate.
- Level Up's full live graph acceptance remains blocked by a security challenge; no bypass was attempted.
- No new deployment, database schema, paid model call, provider hold, reservation, payment or third-party invitation was performed.
- Instructor can use the no-login demo without supplying credentials; optional real-product testing requires their own login.
