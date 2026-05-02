Feature: E2K_PROVENANCE drift audit for curated symbols
  As a curated library maintainer
  I want lightweight provenance and drift classification
  So that I can detect local and upstream symbol changes with minimal metadata

  Background:
    Given an e2k CSV sandbox
    And workflow "library_curation" is selected

  Scenario: copied KiCad symbols include E2K_PROVENANCE
    Given an Eagle library contains symbols:
      | SymbolName | Role                 | PinCount |
      | R          | functional_component | 2        |
    And the Eagle library contains devices:
      | DeviceSet | Device | SymbolName | PackageName       | MappedPinCount |
      | RESISTOR  | R0603  | R          | R_0603_1608Metric | 2              |
    And a KiCad symbol corpus contains:
      | LibraryNickname | SymbolName | PinCount | SourceHash |
      | Device          | R          | 2        | H_ORIGIN_R |
    And a KiCad footprint corpus contains:
      | LibraryNickname | FootprintName     | PadCount |
      | Resistor_SMD    | R_0603_1608Metric | 2        |
    When I run e2k command "library-curation --profile curated_generation"
    Then the command should succeed
    And the curated symbol properties contain rows:
      | SymbolName | PropertyName   | PropertyValueFormat                                  |
      | R          | E2K_PROVENANCE | Device:R,H_ORIGIN_R,<localhash_excluding_provenance> |

  Scenario Outline: provenance audit classifies drift direction
    Given curated symbols contain:
      | SymbolName | E2K_PROVENANCE                          | CuratedHashNow   |
      | R          | Device:R,<OriginHash>,<StoredLocalHash> | <CuratedHashNow> |
    And source symbol hashes contain:
      | LibraryNickname | SymbolName | SourceHashNow   |
      | Device          | R          | <SourceHashNow> |
    When I run capability "provenance_audit" for workflow "library_curation"
    Then the command should succeed
    And the CSV output has rows where:
      | SymbolName | AuditStatus      |
      | R          | <ExpectedStatus> |

    Examples:
      | OriginHash | StoredLocalHash | SourceHashNow | CuratedHashNow | ExpectedStatus |
      | H1         | H2              | H1            | H2             | in_sync        |
      | H1         | H2              | H1            | H2_EDIT        | local_changed  |
      | H1         | H2              | H1_EDIT       | H2             | source_changed |
      | H1         | H2              | H1_EDIT       | H2_EDIT        | both_changed   |

  Scenario: symbols without E2K_PROVENANCE are unmanaged
    Given curated symbols contain:
      | SymbolName         | E2K_PROVENANCE | CuratedHashNow |
      | LOCAL_ONLY_SENSOR  |                | H_LOCAL_ONLY   |
    And source symbol hashes contain:
      | LibraryNickname | SymbolName | SourceHashNow |
      | Device          | R          | H_ANY         |
    When I run capability "provenance_audit" for workflow "library_curation"
    Then the command should succeed
    And the CSV output has rows where:
      | SymbolName         | AuditStatus |
      | LOCAL_ONLY_SENSOR  | unmanaged   |

  Scenario: unmanaged symbols can be rematched with explicit low confidence
    Given curated symbols contain:
      | SymbolName         | E2K_PROVENANCE | CuratedHashNow |
      | LOCAL_ONLY_SENSOR  |                | H_LOCAL_ONLY   |
    And source symbol hashes contain:
      | LibraryNickname | SymbolName  | SourceHashNow |
      | Sensor_Generic  | SENSOR_6PIN | H_SENS_6PIN   |
    And audit options are:
      | Option       | Value |
      | rematch_mode | true  |
    When I run capability "provenance_audit" for workflow "library_curation"
    Then the command should succeed
    And the CSV output has rows where:
      | SymbolName         | AuditStatus | RematchStatus | Confidence |
      | LOCAL_ONLY_SENSOR  | unmanaged   | rematched     | low        |
