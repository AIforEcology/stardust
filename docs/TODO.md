# To do

Open work for Project Stardust. Recurring items come first, then one-off tasks. Tick items off in the pull request that completes them. Anyone running their own Core can use the recurring items as an operations checklist.

## Recurring

### Weekly: review the tech operations fee

**When:** every week, at the start of the week. **Owner:** AIforE operations.

The tech operations fee ([middleware README](../middleware/README.md#tech-operations-fee)) must be seen to cover operating costs, and no more than needed. Review it weekly so the rate follows real costs and order volume.

1. **Update the budget.** Check that `STARDUST_OPERATING_COST_MONTHLY_USD` still matches actual monthly costs: hosting, software, provider vetting and verification time.
2. **Get last week's report:**

   ```bash
   curl -s -H "X-Stardust-Admin-Token: $STARDUST_ADMIN_TOKEN" \
     "http://127.0.0.1:8080/v1/admin/fees?since=$(date -u -v-7d +%Y-%m-%dT00:00:00Z)"
   ```

   (On Linux, use `date -u -d '7 days ago' +%Y-%m-%dT00:00:00Z`.)
3. **Read it.**
   - `coverage_pct`: the share of the week's operating budget that fees covered.
   - `orders`, `provider_payouts_usd` and `fees_usd`: volume and income.
   - `failed_orders`: look into any.
4. **Decide.**
   - **Keep** the rate if coverage is between 90% and 110%, or there are too few orders to judge.
   - **Consider lowering it** if coverage has been above 110% for four weeks running.
   - **Consider raising it** if coverage has been below 90% for four weeks running.
   - Change the rate only for a trend, not for a single week. Stability is easier on subscribers.
5. **Apply a change**, if any.
   - Set `STARDUST_TECH_OPS_FEE_PCT` in the Core's configuration. On the macOS service, that's `service.env`; then re-run `middleware/scripts/install-macos-service.sh`.
   - Quotes already issued keep their rate, and past orders never change.
   - Confirm that `GET /v1/fees/terms` shows the new rate.
6. **Log the review** in the table below, even when nothing changes.

| Week of | Orders | Fees (USD) | Coverage | Rate | Decision |
|---|---|---|---|---|---|
| 2026-09-21 | 0 | 0.00 | n/a | 8% | Keep. No orders yet; budget not set |

## One-off

### Decisions

- [ ] **Counsel review of the fee structure.** Confirm with counsel the tech operations fee invoiced under a technology-services agreement, and its unrelated-business-income treatment ([operating model](architecture/impact-credits.md#operating-model-technology-and-connection-not-financial-brokerage)).
- [ ] **Set the operating budget.** Set `STARDUST_OPERATING_COST_MONTHLY_USD` so the weekly review has a coverage figure.

### Build (spec v1.2 plan, [impact-credits.md](architecture/impact-credits.md))

- [ ] Step 1: contract tests
- [ ] Step 2: measure all five dimensions
- [ ] Step 3: Net Impact Ledger (read-only)
- [ ] Step 4: provider categories (REN, WRR, HRC, RCY)
- [ ] First connector: CarbonPlan OffsetsDB, to verify carbon retirements (read-only)

### Testing and fixes

- [ ] Long-running test of the scheduled pricing refresh (`STARDUST_PRICING_REFRESH=<hours>`)
- [ ] Browser extension:
  - [ ] Confirm the streaming marker on ChatGPT and Gemini
  - [ ] Fix Gemini's ~3% output overcount from source chips
  - [ ] Fix the stale running total after a missed turn
