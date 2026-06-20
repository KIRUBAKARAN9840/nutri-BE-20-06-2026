# Architecture Diagrams — Render Outputs

Generated from [`../ARCHITECTURE.md`](../ARCHITECTURE.md) on 2026-05-13.

## What to use when

| Format | Best for | Example use |
|---|---|---|
| **`.png`** | Slides, screenshots, Slack/WhatsApp shares | Drop into PowerPoint/Keynote/Google Slides |
| **`.svg`** | Web, Notion, Figma, presentations needing zoom | Highest quality — scales infinitely without blur |
| **`.pdf`** | Email attachments, formal sharing | Open in any PDF reader, prints cleanly |
| **`.mmd`** | Source — to edit and re-render | Open in [mermaid.live](https://mermaid.live) to tweak |

## The five diagrams

| # | File | Audience | Shows |
|---|---|---|---|
| 1 | `01-executive-tier-view.*` | Investors, customers, CTO interviews | High-level: Edge → Security → App → Data → External |
| 2 | `02-network-vpc-topology.*` | DevOps, auditors, new engineers | VPC, subnets, AZs, NAT, IGW with real resource IDs |
| 3 | `03-request-lifecycle-sequence.*` | Engineers, technical interviews | Sequence diagram of read / payment / WebSocket paths |
| 4 | `04-background-processing.*` | Backend team | Celery queues, workers, EventBridge, Lambdas |
| 5 | `05-external-dependencies.*` | Compliance, risk reviews | Third-party SaaS map (Razorpay/OpenAI/Firebase/etc.) |

## Quick usage

```bash
# Open all PNGs at once on Mac
open *.png

# Drop the PNG into Google Slides:
#   1. New slide → Insert → Image → Upload from computer
#   2. Pick e.g. 01-executive-tier-view.png

# Edit a diagram:
#   1. Open the .mmd file in any text editor
#   2. Paste contents into https://mermaid.live
#   3. Tweak → download new PNG/SVG
```

## Re-render after editing

If you change `../ARCHITECTURE.md` and want fresh renders:

```bash
cd architecture-diagrams

# Extract Mermaid blocks
awk '
/^```mermaid$/ {capture=1; idx++; out="diagram-" idx ".mmd"; next}
/^```$/ && capture {capture=0; close(out); next}
capture {print > out}
' ../ARCHITECTURE.md

# Render each to PNG / SVG / PDF
for f in *.mmd; do
  npx --yes -p @mermaid-js/mermaid-cli mmdc -i "$f" -o "${f%.mmd}.png" -w 1600 -b white
  npx --yes -p @mermaid-js/mermaid-cli mmdc -i "$f" -o "${f%.mmd}.svg" -b white
  npx --yes -p @mermaid-js/mermaid-cli mmdc -i "$f" -o "${f%.mmd}.pdf" -b white --pdfFit
done
```

## Viewing the source `.md` as rendered diagrams (without exporting)

| Tool | Setup |
|---|---|
| **GitHub** | Push the repo — `.md` Mermaid blocks render natively in any GitHub viewer |
| **VSCode** | Install `Markdown Preview Mermaid Support` extension → Cmd+Shift+V on the `.md` file |
| **Notion** | Paste markdown into a Notion page; it renders Mermaid in code blocks |
| **mermaid.live** | Paste the `.mmd` contents — instant preview + export buttons |
| **Obsidian** | Native Mermaid support — just open the vault |
