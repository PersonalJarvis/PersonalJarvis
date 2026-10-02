---
title: "Spend & Tokens"
slug: costs
summary: "Understand recorded usage, compare providers, and trace spending to individual runs."
section: "Everyday use"
section_order: 2
order: 10
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-10-01
phase: "-"
audience: end-user
tags: [costs, tokens, providers, usage]
related: [tasks-and-reminders, automations, providers-and-api-keys]
---

Open **Spend & Tokens** to see which recorded activity used tokens and how its
cost was accounted for. The view brings together usage from voice sessions,
missions, and agent chats, including supported Agentic IDE records.

It reads the activity already stored by the app. It does not maintain a second
ledger, change your provider account, or make a model request to measure usage.

## Start with the Scope

Before comparing totals, check the selected area, time range, and billing filter.
They determine which records appear in every panel below them.

1. Open **Spend & Tokens** from the app's navigation or profile menu.
2. Choose the activity area you want to inspect.
3. Select the last **7**, **30**, or **90** days, or the full available history.
4. Keep the billing filter on all usage for an overview.
5. Choose the displayed currency, **USD** or **EUR**.

Changing currency changes the presentation of the same records. It does not
convert a payment or change the currency used by your provider.

The euro display uses the conversion rate available to the app. A provider's
invoice may use a different exchange rate or include taxes and other charges.

## Read the Headline Totals

The summary tiles show spending, tokens, and recorded activity for the current
scope. Read their supporting labels as well as the large number.

Usage can have different billing treatments:

| Treatment | How to interpret it |
|---|---|
| Billed usage | Recorded API usage with a monetary cost |
| Subscription usage | Usage attributed to an existing subscription seat |
| Local or unpriced usage | Activity that may have tokens without a known API price |

Subscription-covered usage is separated from directly billed usage. A displayed
usage value is not necessarily an additional charge on top of your subscription.

A zero or missing cost is not proof that the operation consumed no resources.
Some providers do not report enough information to price an operation.

## Follow a Provider or Model

The breakdown table is interactive. A row selects a filter that affects the
totals, trend chart, model details, and line items together.

1. Choose a breakdown dimension such as **Provider** or **Model**.
2. Click the row you want to investigate.
3. Check the active filter chips below the controls.
4. Add another filter to narrow the result further.
5. Remove a chip, or click the selected row again, to clear that choice.

For example, select one provider and then one model to inspect that combination
within your chosen date range. Compare the same date range when switching models.

The breakdown can also group by role or activity surface. These answer different
questions: which kind of work ran, and where in the app it originated.

Switching the top-level activity area clears row-level filters that might belong
only to the previous area. The time range and search query remain available.

## Use the Trend Chart

The chart shows how the selected usage changed over time. Hover a point or bar
for its detail, and use the available chart controls to choose what to compare.

Chart selections can narrow the same data shown in the tables. If a table looks
unexpectedly small after using the chart, inspect the active filters first.

An empty interval means no matching record was available to the view. It does
not prove that another application or an unconnected provider account was idle.

## Open a Day's Report

The daily list groups recorded activity by local calendar day. Open a day to see
its totals, breakdown, and the underlying entries together.

Use the day report when a total increased unexpectedly:

1. Find the day where the change occurred.
2. Open its report.
3. Compare the providers, models, and activity types for that day.
4. Inspect the largest matching entries.
5. Return to the overview before comparing a different period.

The report follows the view's current filters. A provider filter can make a day
look smaller than the all-provider total you saw earlier.

## Inspect Individual Entries

Line items let you move from an aggregate total to the recorded work behind it.
Use search and the available sort choices to find recent, costly, or token-heavy
activity. Load more entries when the first page does not contain the record.

A line item can narrow the view to its associated work. Keep an eye on the filter
chips so that later comparisons are made with the scope you intended.

The source run or conversation remains the place to read what happened. This
view focuses on usage and attribution rather than reproducing a transcript.

## Understand Token Counts and Rates

Input, cached-input, and output tokens have different meanings and may have
different prices. Cached tokens can contribute to the total while costing less
than newly processed input.

Open the rates panel to inspect the pricing information available for recorded
models. A known rate, an estimate, and an unknown rate should not be interpreted
as equally precise measurements.

Model names alone do not establish the final charge. Provider, billing treatment,
cached usage, and the recorded price source all matter.

The view is useful for comparing activity inside Personal Jarvis. Your provider's
billing dashboard remains the authority for its invoice and account balance.

## Troubleshooting

- **No entries:** expand the date range and remove active filters.
- **Tokens without cost:** check whether the price source is unknown or the work
  ran locally or through a subscription.
- **Unexpectedly low totals:** verify the selected activity area and billing filter.
- **A recent run is absent:** refresh after the run finishes recording its usage.
- **A provider invoice differs:** compare the same period and account, including
  activity outside this app and charges not represented by model usage.

Refresh reloads existing usage data. It does not send a paid test request.

## How It Fits Together

The view aggregates existing usage records, applies your filters, and presents
the matching totals and entries. The underlying run history and provider
connections remain the source of activity and pricing information.

## Check That It Works

1. Choose a date range containing a recorded run.
2. Filter to its provider and confirm the filter chip appears.
3. Open a matching entry or day report.
4. Remove the filter and confirm the broader overview returns.

This check only reads existing records; it does not require a new model call.

## From a Terminal

The `jarvis costs` command group reads the same running application's cost API.
Use `jarvis costs --help` to discover the supported summaries and filters.

## Next Steps

For scheduled activity, see [Automations](automations). To create a specific
one-off task, see [Tasks and Reminders](tasks-and-reminders).
