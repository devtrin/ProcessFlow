# Test fixtures

All content is fictional and contains no confidential data.

| Path | Purpose |
|---|---|
| `handoff/` | Fixtures copied unchanged from the design handoff: BPMN with and without DI, the XXE sample, the narrative PDF and its text mirror, the illustrative schema 0.1 and sample JSON. |
| `bpmn/purchase-with-data.bpmn` | `bpmn2:` prefix, three lanes, explicit data associations (source-explicit inputs/outputs), collaboration with a black-box pool and message flows, boundary timer event, collapsed sub-process, complex gateway, vendor extension, text annotation. |
| `bpmn/multi-process.bpmn` | Two independent processes linked only by message flows; parallel fork/join. |
| `bpmn/broken-refs.bpmn` | Duplicate id, dangling and cross-process sequence flows, an element outside the supported subset. |
| `bpmn/xss-labels.bpmn` | HTML and script in names; must render as text. |
| `bpmn/wrong-namespace.bpmn` | Uses the incorrect `http://omg.org` namespace from the original conversation. |
| `security/` | Billion-laughs, external DTD and malformed XML. Never parse these with an unhardened parser. |
| `inference/*.json` | **Hand-written** candidate outputs used to replay a model response in tests. They are not model output and say nothing about real extraction quality. `purchase-honest.json` follows the narrative and records the "another review" ambiguity as an uncertainty; `purchase-fabricated.json` contains an invented quote and a wrong page; `purchase-invented-loop.json` adds the loop the narrative does not support; `invalid-shape.json` and `no-steps.json` test validation. |
