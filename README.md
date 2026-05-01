# EagleLib2KiCad
Service-pattern incubation repository for Eagle→KiCad library-management workflows.
## Why this repo exists
This repository is the implementation-heavy sandbox for evolving importer and library-manager services before convergence into jBOM.
## Service APIs (initial scaffold)
- `KiCadEnvironmentService`: discovers configured KiCad symbol/footprint libraries with nicknames and supports add/remove/rename lifecycle mutations on library tables.
- `KiCadLibraryContextService`: loads symbol + footprint contexts across multi-library sets and reports footprint closure (resolved/unresolved/ambiguous).
- `EagleLibraryContextService`: loads Eagle deviceset/device contexts from `.lbr` libraries.
- `LibraryMigrationAnalysisService`: importer-side evolving policy service for review-oriented migration analysis with pathway classification (`commodity_passive`, `ic_regulator_specialty`, `connector_switch_mechanical`, `schematic_annotation`, `uncategorized`), confidence tiers (`high`/`medium`/`low`), review queues (`none`/`standard`/`priority`), normalized symbol/package matching, and pin-count consistency checks.
## CLI workflow
`tools/eagle_to_kicad_converter.py` supports conversion plus optional migration analysis artifact generation during the same run.
- `--analyze-migration`: produce a JSON review-queue artifact from Eagle/KiCad contexts.
- `--analysis-output`: override output location (default: `<kicad-pretty>/<eagle-lib-stem>.migration-analysis.json`).
- `--kicad-config-home` / `--kicad-project-dir`: control KiCad library-table discovery scope.
## Planned convergence
When service semantics stabilize, a future issue can refactor and merge mature APIs into jBOM with minimal churn.
