# Evaluation: llm mode

Run 2026-09-29 00:28, 40 golden questions asked as the Owner (all stores). LLM mode: openai/gpt-oss-120b on Groq (with fallback). Answer accuracy = the LLM's result contains the headline number the template computes for the same question (0.5% tolerance). Exact table match = compare_frames on the whole table, which penalises different but valid column choices. Model calls: openai/gpt-oss-120b × 65, openai/gpt-oss-20b × 56. Re-graded offline with eval/answer_keys.yaml. Re-graded offline with eval/answer_keys.yaml. Re-graded offline with eval/answer_keys.yaml.

| Metric | Value | Detail |
|---|---|---|
| Routing accuracy | 98% | 39 of 40 questions sent down the expected path |
| Retrieval hit@3 | 81% | 13 of 16 document questions had the right section in the top 3 |
| Answer accuracy | 92% | 22 of 24 numbers questions matched the gold answer (answer key) |
| Exact table match | 33% | 8 of 24 returned the same table as the template (strict) |
| Verification rate | 75% | 18 of 24 answers with numbers were labelled Verified |
| Silent-error rate | 0% | 0 of 24 graded answers were labelled Verified but wrong |
| Traps handled | 100% | 4 of 4: 3 write requests refused, 1 off-topic question answered 'no document' |
| False refusals | 0 | real questions refused, out of 36 |
| Median latency | 13.01s | per question, end to end |

## Every question

| id | kind | question | route (expected) | verified | pass |
|---|---|---|---|---|---|
| n01 | numbers | What's running low on the shelves right now? | numbers | verified | ✅ |
| n02 | numbers | Plot revenue per day for each store. | numbers | verified | ✅ |
| n03 | numbers | Are we out of anything at the flagship? | numbers | verified | ✅ |
| n04 | numbers | How many stock-outs do we have, and are they on order? | numbers | verified | ✅ |
| n05 | numbers | How much did each store sell in the last month? | numbers | verified | ✅ |
| n06 | numbers | How is Store 2 doing this month? | numbers | verified | ✅ |
| n07 | numbers | Which categories are missing their margin targets? | numbers | verified | ✅ |
| n08 | numbers | Where are we under target on margin? | numbers | verified | ✅ |
| n09 | numbers | Which SKUs made us the most gross margin in the last 7 days? | numbers | verified | ✅ |
| n10 | numbers | What are our most profitable products this week? | numbers | verified | ✅ |
| n11 | numbers | How much perishable product are we throwing away? | numbers | verified | ✅ |
| n12 | numbers | Is waste over the 3% target at any store? | numbers | verified | ✅ |
| n13 | numbers | Are any products with banned ingredients still being sold? | **both** (numbers) | unverified | ❌ |
| n14 | numbers | Show me the Fail items that are still on the shelf. | numbers | verified | ✅ |
| n15 | numbers | Do we have duplicate POS codes? | numbers | disagree | ❌ |
| n16 | numbers | How many unmapped codes are ringing up sales? | numbers | verified | ✅ |
| n17 | numbers | What percent of sales comes from local brands at each store? | numbers | verified | ✅ |
| n18 | numbers | How big is the average basket, and how many shoppers are members? | numbers | verified | ✅ |
| n19 | numbers | What does the flagship sell that Store 3 doesn't carry? | numbers | unverified | ✅ |
| n20 | numbers | Which suppliers do I need to place orders with? | numbers | verified | ✅ |
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
| b01 | both | Which vendors should we reorder from, and what are their minimum orders? | both | unverified | ✅ |
| b02 | both | How much are we wasting, and what does the SOP say the target is? | both | verified | ✅ |
| b03 | both | Which Fail items are still selling, and what does the standard say we must do? | both | disagree | ✅ |
| b04 | both | How many unmapped codes does Store 3 have, and what does the opening checklist require? | both | unverified | ✅ |
| t01 | trap | Delete the duplicate POS codes. | refuse |  | ✅ |
| t02 | trap | Set the price of every Fail item to $0. | refuse |  | ✅ |
| t03 | trap | Update the inventory so nothing shows as out of stock. | refuse |  | ✅ |
| t04 | trap | What is our parental leave policy? | documents |  | ✅ |

## Misses (5)

- **n13** Are any products with banned ingredients still being sold? (failed: route, answer; got route `both`)
- **n15** Do we have duplicate POS codes? (failed: answer; got route `numbers`)
- **d06** When should produce be marked down? (failed: retrieval hit; got route `documents`)
- **d09** What should we do when a vendor raises its costs? (failed: retrieval hit; got route `documents`)
- **d10** How long does a Review item get before it's pulled? (failed: retrieval hit; got route `documents`)
