# hb-market-simulator Design Documents

This directory contains the architectural analysis, interface proposals, and
sub-package extraction roadmap for the hummingbot market simulation framework.

## Document Structure

- `architecture/` — Deep analysis of hummingbot's current architecture
  - `00-executive-summary.md` — Overview and key findings
  - `01-strategy-v2-dependency-graph.md` — Full component dependency map
  - `02-connector-interface-analysis.md` — ConnectorBase decomposition
  - `03-event-system-analysis.md` — PubSub, events, forwarders
  - `04-data-flow-analysis.md` — Market data pipeline (WS → strategy)

- `proposals/` — Sub-package extraction proposals
  - `00-extraction-roadmap.md` — Prioritized list of future sub-packages
  - `01-market-simulator-core.md` — This package's design
  - `02-connector-protocols.md` — Protocol-based ConnectorBase decomposition
  - `03-websocket-data-feed.md` — Standalone WS data feed package
  - `04-order-management.md` — Order lifecycle sub-package

- `analysis/` — Prior work and gap analysis
  - `01-dev-sandboxing-review.md` — Review of dev/sandboxing branch
  - `02-backtesting-gaps.md` — Current backtesting limitations
  - `03-paper-trade-analysis.md` — PaperTradeExchange review
