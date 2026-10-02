# Documentation

Developer documentation for the strategy-token launchpad on Robinhood Chain. Everything
here is plain Markdown with relative links, so it reads the same on GitLab, GitHub or a
local checkout. Diagrams are [Mermaid](https://mermaid.js.org/), which both render
natively.

## Start here

| If you want to… | Read |
|---|---|
| understand what this is and how the pieces fit | [ARCHITECTURE.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/ARCHITECTURE.md) |
| integrate V2 curve launches, graduation and multi-asset trading | [V2_BONDING_CURVE.md](./V2_BONDING_CURVE.md) |
| review V2 multi-user trading, callback defenses and economic boundaries | [V2_ADVERSARIAL_REVIEW.md](./V2_ADVERSARIAL_REVIEW.md) |
| review V2 configurable graduation funding and fee-vault risks | [V2_DUAL_ENGINE_REVIEW.md](./V2_DUAL_ENGINE_REVIEW.md) |
| click through V2 allocation scenarios and rerun the pinned fork locally | [lab/README.md](../lab/README.md) |
| replay multi-wallet buy, sell, mixed, and opening-sniper scenarios | [V2_MARKET_SCENARIOS.md](./V2_MARKET_SCENARIOS.md) |
- [V2_LP_DEPTH_EXPERIMENT.md](V2_LP_DEPTH_EXPERIMENT.md) — LP share vs sale share: model sweep and contract measurement of the early-buyer exit, hook spike and LP fee.
| compare short uniform-price opening batches and min-out protection | [V2_BATCH_OPENING_EXPERIMENT.md](./V2_BATCH_OPENING_EXPERIMENT.md) |
| verify V2 fee-funded buyback and burn on a real-venue fork | [V2_PROFIT_FORK.md](./V2_PROFIT_FORK.md) |
| replay eight V2 market paths and six strategy parameter combinations on a real-venue fork | [V2_LOW_FREQUENCY_FORK.md](./V2_LOW_FREQUENCY_FORK.md) |
| see what a stock that trends up, down or sideways does to a V2 raise, its treasury and its token | [V2_TREND_SCENARIOS.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/V2_TREND_SCENARIOS.md) |
| build it, run the tests, and make a change safely | [DEVELOPMENT.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/DEVELOPMENT.md) |
| know what is trusted, what is defended, what is knowingly open | [SECURITY.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/SECURITY.md) |
| look up a function, selector, error or constant | [REFERENCE.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/REFERENCE.md) *(generated — do not edit by hand)* |
| rehearse or execute a deployment | [DEPLOYMENT.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/DEPLOYMENT.md) |
| deploy V2 on the public Robinhood Chain testnet (46630) for the team to test the front end with no real money | [TESTNET_V2.md](./TESTNET_V2.md) |
| list a stock, run the routine checks, read an alert | [OPERATIONS.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/OPERATIONS.md) |
| check a stock before a V2 listing or launch (graduation depth, gate, 0.05% sell chunk) | [`tools/v2_launch_check.py`](../tools/v2_launch_check.py), [V2_DEPLOYMENT_REHEARSAL.md](./V2_DEPLOYMENT_REHEARSAL.md#listing-check-first-live-run) |
| see what ships first and what waits for v2, and why | [ROADMAP.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/ROADMAP.md) |
| sell weekly covered calls from a treasury to a market maker (RFQ desk, runbook, oracle caveats, review record) | [COVERED_CALL_DESK.md](./COVERED_CALL_DESK.md) |
| review the proposed RHNVDA Earn vault, physical RFQ desks and deployment gates | [EARN_RFQ_LAUNCH.md](./EARN_RFQ_LAUNCH.md), [EARN_VAULT_IMPLEMENTATION.md](./EARN_VAULT_IMPLEMENTATION.md) |
| write the site, the announcement or the launch form | [LAUNCH_KIT.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/LAUNCH_KIT.md) |
| respond to an incident | [`../emergency/README.md`](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/emergency/README.md) |

**Prefer a site to a folder?** `pip install mkdocs-material mkdocs-static-i18n jieba`, then `tools/docs_site.py serve` -- search, a sidebar, an English / 中文 switch and
rendered Mermaid at http://127.0.0.1:8000, assembled from these same files (nothing is written for it, and the repo stays
the source of truth). Keep it internal: SECURITY, AUDIT and the emergency runbook are in it.

**New here?** ARCHITECTURE → DEVELOPMENT → SECURITY, in that order, is about forty minutes
and is enough to review a pull request.

## The one thing to internalise

**Everything launched is immutable.** A token, its treasury and the one `HedgeFunHook` every
strategy's pool runs on have no upgrade path and no parameter that can change; the token has
no owner, the treasury's owner can only point its votes (`setVoteDelegate` — reserved, not
live), and the hook's owner — the same one, the factory's — can repoint each pool's two
payout addresses (the creator's only after a public 14-day wait the creator can veto) and do
nothing else. A bug that ships in a token or treasury ships forever, for every strategy
launched while it was there; a bug in the hook ships for **every strategy at once**. That is why the audit's fixes
landed before the first launch, why every fix has a regression test that was confirmed to
fail without it, and why [SECURITY.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/SECURITY.md#rules-for-changing-the-code) has rules
for changing the code rather than suggestions.

## Evidence and analysis

These are records of work done, not living documentation: they say what was true when
they were written, and carry their dates.

| Document | What it is |
|---|---|
| [`../AUDIT.md`](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/AUDIT.md) | Security review, 2026-09-20: three adversarial passes, 43 Foundry reproductions, a go/no-go, and the measured chain facts each finding was sized against. Status banners record which rounds fixed what |
| [STOCK_TOKEN_ASSESSMENT.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/STOCK_TOKEN_ASSESSMENT.md) | What the real Robinhood stock token can do to a holder: beacon proxy, upgrader, deny-list, pause, `adminBurn`. Every row VERIFIED or INFERRED |
| [`../LISTING_CANDIDATES.md`](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/LISTING_CANDIDATES.md) | Which stocks can be listed (194 → 35 → 25), the first-wave decision, and what the feeds actually do across a weekend |
| [rule-backtest/](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/rule-backtest/README.md) | What a creator's `tp1`/`dip`/`stop` would have done on eleven listed stocks, hourly, replayed forwards and backwards. About the **rule's economics**, not about whether a stock can be listed |
| [`../POOL_SELECTION.md`](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/POOL_SELECTION.md) | A *different* product — a delta-neutral LP hedged with a Lighter perp. Shares the pool census |
| [robinhood-chain/](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/robinhood-chain/README.md) | Mirrors of three Robinhood Chain docs pages, each reconciled against what the chain actually does |
| [research/PONS_TAX_ELASTICITY.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/research/PONS_TAX_ELASTICITY.md) | Does the trade tax predict volume and graduation? 106,631 pons.family launches on our chain, 2026-09-16 to 09-27, at a creator-chosen 1-11% tax. The same creator does as well at 2-5% as at 1%; above 5% volume halves and graduation nearly stops. Evidence for the 1-2% cap decision |

## Keeping these docs true

- **[REFERENCE.md](https://github.com/keyuyuan/hedgefund/blob/983542cf93442d7309abf8290f2427235fd2ab93/docs/REFERENCE.md) is generated** from the compiler's ABI output and the
  source's NatSpec by `tools/gen_reference.py`. Change the NatSpec, not the Markdown, then
  regenerate. A missing description in the reference is a missing `///` in the source.
- **`python3 tools/check_docs.py`** fails if any relative link or heading anchor in any
  tracked Markdown file is broken, or if the reference is stale. Run it before pushing
  anything that renames a file, a heading or a function.
- **A PR that changes behaviour changes the doc that describes it, in the same PR.**
  ARCHITECTURE for how it works, SECURITY for what it defends or accepts, OPERATIONS and
  `emergency/` for anything an operator would do differently. The root
  [`README.md`](../README.md) is the project's front page and the historical record of
  what each adversarial pass found; these files are what a maintainer works from.
- **Numbers rot.** Pool depths, TVL, feed ages and test counts in the analysis documents
  are dated measurements. Re-run the tool rather than quoting the figure.
- Keep it portable: relative links only, no platform-specific Markdown extensions, fenced
  code blocks with a language tag.
