# Three-minute video storyboard

Actual hosted demo recordings, edited for timing; synthetic narration. Provider results in A/B/C are fixtures. The historical screenshot is reused evidence, not a fresh provider test. No signed-in account footage is published.

## 0:00–0:30 | Game On! — From group chat to court

**Screen/actions:** Show title and both public URLs.

**Label:** Product + separate fictional Agent Brain demo

**Narration:** Game On helps friends agree on a pickleball time, compare courts, and reach a booking handoff. The submission contains the real product code. Its hosted application uses authentication and persistent groups. For this video, we use the separate, no-login Agent Brain demo with fictional players and clearly simulated provider data. No booking or payment is made.

## 0:30–1:00 | A — Successful recommendation

**Screen/actions:** Select A → Start → Next → Next → Run Ernie → expand Agent Brain → Next events → Approve & continue.

**Label:** Actual hosted LangGraph; simulated provider observations

**Narration:** Choose scenario A, start the demo, and move through crew and time to the court step. Run Ernie. The bounded LangGraph reads the group, evaluates eligible times, and calls discovery. A suitable result becomes a recommendation. Expand Agent Brain and step through the recorded decisions, tools and observations. Organiser approval leads to handoff, not a confirmed reservation.

## 1:00–1:30 | B — Replan within agreed times

**Screen/actions:** Select B → repeat run → step through empty first search and second search → approve proposal.

**Label:** Actual second search; fixture inventory; edited capture

**Narration:** Switch to scenario B and run the same graph. The first discovery returns no courts. Ernie observes that result and searches another time already approved by the fictional group. This is a second tool execution, not just a different message. Agent Brain replays the completed event record. It is not live streamed reasoning, and replay does not repeat provider calls.

## 1:30–2:00 | C — Ask for human assistance

**Screen/actions:** Select C → run → step through exhaustion → REQUEST_HELP. No approval action.

**Label:** Bounded exhaustion; no fabricated court or price

**Narration:** In scenario C, neither eligible search produces a suitable court. The graph stops and requests human assistance. It does not invent a price, silently change the group time, or offer a booking approval for a nonexistent option. These scenarios demonstrate genuine control flow with a deterministic decision policy. They do not claim paid model inference or real provider inventory.

## 2:00–2:30 | Real local evidence — Pickleholic

**Screen/actions:** Show saved I-069 summary only; do not open provider or click Next.

**Label:** Historical I-069: 6 Nov 2026, 01:37:41 UTC

**Narration:** Separately, local Playwright reached Pickleholic for an approved Court Three slot. After specifically approved Next navigation, the real summary showed fifty dollars plus a one dollar twenty-five technology fee: fifty-one twenty-five total, with provider player count one. Currency code and separate tax were unknown. This historical screenshot is not current availability. No new provider navigation occurs in this video.

## 2:30–3:00 | Human-controlled handoff

**Screen/actions:** Show final control-loop and limitations card; refer to README and verification checklist.

**Label:** Source, tests, diagrams and limitations in the submission

**Narration:** The product keeps organiser approval, exact-context cached estimates, an assistant brief, and a tentative calendar download. The venue may require court and time reselection. Hosted planning, fictional demo replanning, and local browser evidence are separate capabilities. Read the verification checklist for release differences and remaining limits. The outcome is an explainable court plan and safe handoff, never an invented booking confirmation.

## Evidence references

- Hosted captures: scenario-A.png, scenario-B.png, scenario-C.png; current browser checks in verification.md.
- Historical provider: historical-pricing.png and historical-live-evidence.json.
- Architecture: architecture.svg; journey: user-flow.svg.
- Audio: local Windows synthetic speech, no cloud service or cloned voice.
