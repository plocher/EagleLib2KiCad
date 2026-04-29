# EagleLib2KiCad
Service-pattern incubation repository for Eagle→KiCad library-management workflows.
## Why this repo exists
This repository is the implementation-heavy sandbox for evolving importer and library-manager services before convergence into jBOM.
## Service APIs (initial scaffold)
- `KiCadEnvironmentService`: discovers configured KiCad symbol/footprint libraries with nicknames and supports add/remove/rename lifecycle mutations on library tables.
- `KiCadLibraryContextService`: loads symbol + footprint contexts across multi-library sets and reports footprint closure (resolved/unresolved/ambiguous).
- `EagleLibraryContextService`: loads Eagle deviceset/device contexts from `.lbr` libraries.
- `LibraryMigrationAnalysisService`: importer-side evolving policy service for review-oriented migration analysis.
## Planned convergence
When service semantics stabilize, a future issue can refactor and merge mature APIs into jBOM with minimal churn.
