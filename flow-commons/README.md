# Flow Commons (POC)

People learn how the system really works one flow at a time. Flow Commons collects that knowledge as structured flow files and works out how the flows connect.

## The loop

1. **Describe.** A person fills in `templates/flow-contribution-template.md` in plain words, in any language.
2. **Convert.** An AI turns the notes into a flow file (YAML). Use "Convert with Claude" in the app, or paste `templates/ai-prompt.md` plus the notes into any AI tool. The app's version of the prompt also lists the library's known states, screens and roles, so new flows reuse the same names and connect to existing ones.
3. **Import.** The app validates the file and shows its impact before saving: which flows it connects to, which gaps it fills, which new mappings it creates.
4. **Analyse.** On every change the library recomputes:
   - **Handoffs.** A state one flow produces (`SalesOrder.status = Confirmed`) that another flow starts from or waits for. These become the lines on the system map and the "continue the journey" links in the walkthrough.
   - **Aliases.** The same screen ID under different names, and role names that match once abbreviations are expanded ("Sales Mgr" = "Sales Manager"). High-confidence matches apply automatically. The rest wait for someone to accept or reject them.
   - **Field mappings.** Different input labels declared as the same entity field (`maps: Customer.code`).
   - **Gaps.** States some flow needs that no flow produces yet. These are listed as "Wanted", with a pre-filled template so someone can contribute the missing flow.

## The key design choice

Flows connect through **entity states** (`Entity.field = Value`) and **screen IDs**, not by linking flows to each other by hand. A contributor only describes what they know: "my flow starts when X and ends with Y". The connections appear once someone else's flow produces X or consumes Y. That's what lets the map grow without anyone curating it.

## Files

```
flow-commons/
├── index.html                          the app (published as a claude.ai Artifact)
├── samples/*.yaml                      6 example contributions (order-to-cash), seeded into the app
└── templates/
    ├── flow-contribution-template.md   what contributors download and fill in
    └── ai-prompt.md                    prompt for any AI tool (the app adds the live vocabulary)
```

## Flow file (schemaVersion 2)

See `samples/02-sales-order-entry.yaml` for a full example. Fields that drive the analysis:

| Field | Purpose |
|---|---|
| `trigger`, `outcome` | States before and after the flow. Used for flow-to-flow handoffs. |
| `steps[].produces`, `steps[].requires` | Step-level states. Used for handoffs in the middle of a flow, such as a sub-flow for approval. |
| `steps[].screen.id` | Matches the same screen across contributions. |
| `steps[].inputs[].maps` | Ties an on-screen label to an entity field. |
| `type: decision` + `branches` | Splits in the path. |

## Next steps beyond the POC

- Move storage and analysis to a backend (for example ASP.NET Core with PostgreSQL or a graph DB) once there are hundreds of flows. The analysis functions in `index.html` (`validate`, `normalize`, `analyze`) port directly.
- Review workflow for imports (draft → reviewed), and versioning per flow.
- Embedding-based matching for states and screens with different spellings, in addition to the exact and abbreviation rules.
- Export the whole library as Markdown or JSON for RAG.
