---
title: Item Setup and Master Data SOP
doc_type: sop
vendor_id: null
store_scope: all
effective_date: 2026-06-15
version: 1
status: current
---
*(illustrative) A synthetic procedure written for a portfolio demo. Not any real company's procedure.*

## Required fields before an item goes live
A new item needs a master SKU, product name, category, vendor, unit cost, a shelf price ending in .99, a clean-standard status of Pass or Review, and a POS code mapped at every store that carries it. No cost, no price: an item without a unit cost does not go on sale.

## Price and cost changes
Price changes go live in the stores and the app on the same day. When a vendor cost increase drops a SKU's margin more than 5 points below its category target, the category lead proposes a new price within 3 days. A price below cost is treated as a keying error and fixed before the next sale.

## App catalog
Every item sold in stores is listed in the app within 2 days of setup, at the same price as the POS. Fail items come off the app with the shelf, within 48 hours.

## Weekly master data audit
Every Monday the analyst runs the master data audit: prices below cost, missing costs, margins under the floor after cost increases, unmapped and duplicate POS codes, vendor records missing terms, and app items out of sync. Each open issue gets an owner and a due date.
