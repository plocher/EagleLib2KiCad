# EagleLib2KiCad

Eagle→KiCad library-management workflows.

## Project intent

This project is the beginnings of a general KiCad library manager, focused on the meta-issues related to lifecycle, evolution and maintenance.  If KiCad's existing symbol and footprint library editors manipulate the content of the libraries under their domains, this tool manages the organization and relationships of the corpus itself.

It can be understood by examoning one of its high level user stories:

 * An experienced Eagle Cad user would like to begin using KiCad.  They have an existing set of mature Eagle projects and associated currated Eagle libraries, and are comfortable using the Eagle environment.  However,  they have little experience with KiCad, and are not conversent in the structural differences between the two ecosystems.
 * KiCad already has a simple mechanism to import an existing Eagle Project into KiCad.  This import process effectively creates four artifacts - a KiCad schematic, a KiCad PCB, a KiCad Symbol library and a KiCad Footprint library.  An Example:
   * /Users/jplocher/Dropbox/KiCad/projects/MRCS/cpNode-ProMini
      *   cpNode-ProMini.kicad_sch
      *   cpNode-ProMini.kicad_pcb
      *   cpNode-ProMini.pretty
      *   cpNode-ProMini-eagle-import.kicad_sym
* This brute force import is technically correct - the kicad project is as complete and usable as was the original Eagle one.  It also exposes a subtle problem - the new KiCad project isn't using the KiCad-ecosystem provided symbols and footprints, leaving the user in ignorance of the vast corpus of KiCad Symbol and Footprint resources available to them.

This project addresses this gap via a handful of capabilities:
1. When importing an Eagle library, 
   * it is aware of the structural differences (Symbols/Devices/Packages -vs- Symbols/Footprints) and the KiCad corpus, and uses that information to check for duplication
   * It identifes matching KiCad symbols and Footprints that can be used instead of replicating Eagle duplicates
   * it supports the concept of a currated KiCad symbol library that supports a mix of KiCad-proven and Eagle-imported Symbols and Footprints
   * it allows pattern-based renaming, searching and copying of Symbols and Footprints, both as part of an Eagle lib to KiCad import as well as part of general library meta-maintenance.
2. When importing an Eagle Project,
   * it can replace Eagle-components with preferred KiCad ones, taking care of maintaining wire/netlist correctness and PCB trace and location correctness.
3. It works correctly and seamlessly with KiCad's existing tools, including the library tables and nicknames.

## Tangible goals

 * convert an existing Eagle lib to a high quality KiCad Symbol library and minimal KiCad Footprint library by finding and using equivalent KiCad Symbols instead of Eagle copies when possible, and by linking those symbols with equivalent KiCad Footprints when possible, or creating new KiCad versions when not.
 * Import an existing Eagle project, extracting the symbols it uses into a set of KiCad libraries as above, and converting the Eagle schematic and pcb into KiCad ones that use these new symbols and footprints


## Why this repo exists
This repository is the implementation-heavy sandbox for evolving importer and library-manager services before convergence into jBOM.  It lives alongside two related repos:
  1. The KiCad source repo: `/Users/jplocher/Dropbox/workspace/kicad`
     * The reference source for KiCad
  2. The jBOM repo: `/Users/jplocher/Dropbox/KiCad/jBOM`
     * The reference source for a KiCad BOM and inventory management tool
     * We wish to be strongly influenced by its TDD/BDD functional test and service API design patterns
     * At some point, both EagleLib2KiCad and jBOM will evolve into KiCad plugins or add-on collaborative ecosystem tools.

## Current limitations of this repo
 * It is immature and incomplete
 * It has a GUI POC that explores lib import and renaming concepts in ./tools
 * It has the beginnings of service APIS:
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
When service semantics stabilize, a future issue should be able to refactor and merge mature APIs into jBOM with minimal churn.
