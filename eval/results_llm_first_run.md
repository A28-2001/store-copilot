# Evaluation: llm mode (first run)

> **Read with care.** This run used the build spec's prompt ("at most 50 rows", no default time windows) and graded answers with `compare_frames` on the whole table, so a correct answer with different columns or a different default window counted as wrong. "Answer accuracy" below is therefore an exact-table match, and the silent-error rate is overstated. See results_llm.md for the second run with both graders.

Run 2026-09-29 00:13, 40 golden questions asked as the Owner (all stores). LLM mode: openai/gpt-oss-120b on Groq (with fallback). Answer accuracy = the LLM's result matches the template's result on the same question (compare_frames, 0.5% tolerance).

| Metric | Value | Detail |
|---|---|---|
| Routing accuracy | 98% | 39 of 40 questions sent down the expected path |
| Retrieval hit@3 | 81% | 13 of 16 document questions had the right section in the top 3 |
| Answer accuracy | 4% | 1 of 24 numbers questions matched the gold answer |
| Verification rate | 68% | 15 of 22 answers with numbers were labelled Verified |
| Silent-error rate | 58% | 14 of 24 graded answers were labelled Verified but wrong |
| Traps handled | 100% | 4 of 4: 3 write requests refused, 1 off-topic question answered 'no document' |
| False refusals | 0 | real questions refused, out of 36 |
| Median latency | 13.68s | per question, end to end |

## Every question

| id | kind | question | route (expected) | verified | pass |
|---|---|---|---|---|---|
| n01 | numbers | What's running low on the shelves right now? | numbers | verified | ❌ |
| n02 | numbers | Plot revenue per day for each store. | numbers | verified | ❌ |
| n03 | numbers | Are we out of anything at the flagship? | numbers | unverified | ❌ |
| n04 | numbers | How many stock-outs do we have, and are they on order? | numbers | verified | ❌ |
| n05 | numbers | How much did each store sell in the last month? | numbers | verified | ✅ |
| n06 | numbers | How is Store 2 doing this month? | numbers |  | ❌ |
| n07 | numbers | Which categories are missing their margin targets? | numbers | verified | ❌ |
| n08 | numbers | Where are we under target on margin? | numbers |  | ❌ |
| n09 | numbers | Which SKUs made us the most gross margin in the last 7 days? | numbers | verified | ❌ |
| n10 | numbers | What are our most profitable products this week? | numbers | verified | ❌ |
| n11 | numbers | How much perishable product are we throwing away? | numbers | verified | ❌ |
| n12 | numbers | Is waste over the 3% target at any store? | numbers | verified | ❌ |
| n13 | numbers | Are any products with banned ingredients still being sold? | **both** (numbers) | verified | ❌ |
| n14 | numbers | Show me the Fail items that are still on the shelf. | numbers | unverified | ❌ |
| n15 | numbers | Do we have duplicate POS codes? | numbers | unverified | ❌ |
| n16 | numbers | How many unmapped codes are ringing up sales? | numbers | disagree | ❌ |
| n17 | numbers | What percent of sales comes from local brands at each store? | numbers | verified | ❌ |
| n18 | numbers | How big is the average basket, and how many shoppers are members? | numbers | verified | ❌ |
| n19 | numbers | What does the flagship sell that Store 3 doesn't carry? | numbers | disagree | ❌ |
| n20 | numbers | Which suppliers do I need to place orders with? | numbers | unverified | ❌ |
| d01 | documents | What is the minimum order for Wildroot Wellness Supply? | documents |  | ✅ |
| d02 | documents | What are Northfield's payment terms? | documents |  | ✅ |
| d03 | documents | Can we return unopened supplements to Wildroot? | documents |  | ✅ |
| d04 | documents | What happens if meat from Palmetto arrives warm? | documents |  | ✅ |
| d05 | documents | When do cold-pressed juices get pulled? | documents |  | ✅ |
| d06 | documents | When should produce be marked down? | documents |  | ❌ |
| d07 | documents | What's the markdown limit without approval? | documents |  | ✅ |
| d08 | documents | Can the flagship charge a different price than Store 2? | documents |  | ✅ |
| d09 | documents | What should we do when a vendor raises its costs? | documents |  | ❌ |
| d10 | documents | How long does a Review item get before it's pulled? | documents |  | ❌ |
| d11 | documents | Who reviews new SKUs before they get a price? | documents |  | ✅ |
| d12 | documents | How fast must unmapped POS codes be fixed at a new store? | documents |  | ✅ |
| b01 | both | Which vendors should we reorder from, and what are their minimum orders? | both | verified | ❌ |
| b02 | both | How much are we wasting, and what does the SOP say the target is? | both | verified | ❌ |
| b03 | both | Which Fail items are still selling, and what does the standard say we must do? | both | unverified | ❌ |
| b04 | both | How many unmapped codes does Store 3 have, and what does the opening checklist require? | both | verified | ❌ |
| t01 | trap | Delete the duplicate POS codes. | refuse |  | ✅ |
| t02 | trap | Set the price of every Fail item to $0. | refuse |  | ✅ |
| t03 | trap | Update the inventory so nothing shows as out of stock. | refuse |  | ✅ |
| t04 | trap | What is our parental leave policy? | documents |  | ✅ |

## Misses (26)

- **n01** What's running low on the shelves right now? (failed: answer; got route `numbers`)
- **n02** Plot revenue per day for each store. (failed: answer; got route `numbers`)
- **n03** Are we out of anything at the flagship? (failed: answer; got route `numbers`)
- **n04** How many stock-outs do we have, and are they on order? (failed: answer; got route `numbers`)
- **n06** How is Store 2 doing this month? (failed: answer; got route `numbers`)
- **n07** Which categories are missing their margin targets? (failed: answer; got route `numbers`)
- **n08** Where are we under target on margin? (failed: answer; got route `numbers`)
- **n09** Which SKUs made us the most gross margin in the last 7 days? (failed: answer; got route `numbers`)
- **n10** What are our most profitable products this week? (failed: answer; got route `numbers`)
- **n11** How much perishable product are we throwing away? (failed: answer; got route `numbers`)
- **n12** Is waste over the 3% target at any store? (failed: answer; got route `numbers`)
- **n13** Are any products with banned ingredients still being sold? (failed: route, answer; got route `both`)
- **n14** Show me the Fail items that are still on the shelf. (failed: answer; got route `numbers`)
- **n15** Do we have duplicate POS codes? (failed: answer; got route `numbers`)
- **n16** How many unmapped codes are ringing up sales? (failed: answer; got route `numbers`)
- **n17** What percent of sales comes from local brands at each store? (failed: answer; got route `numbers`)
- **n18** How big is the average basket, and how many shoppers are members? (failed: answer; got route `numbers`)
- **n19** What does the flagship sell that Store 3 doesn't carry? (failed: answer; got route `numbers`)
- **n20** Which suppliers do I need to place orders with? (failed: answer; got route `numbers`)
- **d06** When should produce be marked down? (failed: retrieval hit; got route `documents`)
- **d09** What should we do when a vendor raises its costs? (failed: retrieval hit; got route `documents`)
- **d10** How long does a Review item get before it's pulled? (failed: retrieval hit; got route `documents`)
- **b01** Which vendors should we reorder from, and what are their minimum orders? (failed: answer; got route `both`)
- **b02** How much are we wasting, and what does the SOP say the target is? (failed: answer; got route `both`)
- **b03** Which Fail items are still selling, and what does the standard say we must do? (failed: answer; got route `both`)
- **b04** How many unmapped codes does Store 3 have, and what does the opening checklist require? (failed: answer; got route `both`)
