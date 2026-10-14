# Red-flag typologies
SYNTHETIC DEMO: Northstar Pay (fictional). Reference guide for transaction-monitoring analysts. Use with AML Policy v4 (approved).

| Typology | Code | Typical pattern | Monitoring rule |
|---|---|---|---|
| Rapid movement of funds | rapid_movement | Burst of inbound transfers moved onward within days; the account acts as a pass-through | TM-R07 |
| New overseas counterparties | new_counterparties | Several senders first seen in the alert window, often in different jurisdictions | TM-R07 |
| Shell-company indicators | shell_company | Counterparty with a registered-agent address, no trading history or a newly registered website | Analyst review |
| Trade-profile mismatch | profile_mismatch | Payment references such as consulting fees or investments received by a trading company | Analyst review |
| Structuring | structuring | Repeated cash deposits just below S$20,000 on consecutive days | TM-R11 |
| Seasonal uplift (benign) | seasonal | Higher retail cash takings before public holidays, matching earlier years | TM-R03 |

## Rapid movement of funds
The strongest indicator is the combination of new senders and fast onward transfer. Compare the inbound total with the customer's expected monthly inbound value; a multiple above three is significant. Check whether the onward beneficiary is new and whether it is in a high-risk jurisdiction.

## New overseas counterparties
Look for shared details between senders (same IP address, same remittance contact, consecutive references). Several unrelated companies paying one SME within hours is unusual for a wholesale business.

## Benign explanations to rule out
Documented seasonal patterns, a known new contract with invoices on file, or a one-off asset sale with a sale agreement. Record the explanation and the documents that support it.
