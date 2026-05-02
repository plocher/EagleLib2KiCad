Feature: library_curation workflow profiles
  As a migration engineer
  I want profile-specific behavior for library_curation
  So that I can choose advisory, fidelity, or curated output intentionally

  Background:
    Given an e2k CSV sandbox
    And workflow "library_curation" is selected
    And an Eagle library contains symbols:
      | SymbolName      | Role                 | PinCount |
      | R               | functional_component | 2        |
      | TPS7A47         | functional_component | 5        |
      | AGND            | schematic_annotation | 1        |
      | UNIQUE_SENSOR   | functional_component | 6        |
    And the Eagle library contains devices:
      | DeviceSet      | Device  | SymbolName    | PackageName          | MappedPinCount |
      | RESISTOR       | R0603   | R             | R_0603_1608Metric    | 2              |
      | TPS7A47        | SOT223  | TPS7A47       | SOT223               | 5              |
      | AGND           | default | AGND          | NONE                 | 0              |
      | UNIQUE_SENSOR  | A       | UNIQUE_SENSOR | UNIQUE_SENSOR_PAD    | 6              |
    And a KiCad symbol corpus contains:
      | LibraryNickname   | SymbolName | PinCount | DefaultFootprint                      |
      | Device            | R          | 2        | Resistor_SMD:R_0603_1608Metric       |
      | Regulator_Linear  | TPS7A47    | 5        | Package_TO_SOT_SMD:SOT-223-3_TabPin2 |
      | power             | GNDA       | 1        |                                       |
    And a KiCad footprint corpus contains:
      | LibraryNickname     | FootprintName       | PadCount |
      | Resistor_SMD        | R_0603_1608Metric   | 2        |
      | Package_TO_SOT_SMD  | SOT-223-3_TabPin2   | 5        |

  Scenario: advisory_matching emits decisions and review queue only
    When I run e2k command "library-curation --profile advisory_matching"
    Then the command should succeed
    And no curated symbol library should be produced
    And no curated footprint library should be produced
    And the CSV output has rows where:
      | DeviceKey          | Classification     | ReviewQueue |
      | RESISTOR:R0603     | exact_match        | none        |
      | TPS7A47:SOT223     | exact_match        | none        |
      | AGND:default       | semantic_match     | standard    |
      | UNIQUE_SENSOR:A    | unresolved_package | standard    |

  Scenario: curated_generation emits curated symbol and minimal footprint outputs
    Given role filtering overrides are:
      | ExcludedRole          |
      | schematic_annotation  |
    When I run e2k command "library-curation --profile curated_generation"
    Then the command should succeed
    And a curated symbol library should be produced
    And a curated footprint library should be produced
    And the curated symbol library contains rows:
      | SymbolName     | Origin            |
      | R              | copied_kicad      |
      | TPS7A47        | copied_kicad      |
      | UNIQUE_SENSOR  | transformed_eagle |
    And the curated symbol library should not contain symbol "AGND"
    And the curated footprint library contains rows:
      | FootprintName      |
      | UNIQUE_SENSOR_PAD  |

  Scenario: fidelity_conversion preserves complete Eagle representation
    When I run e2k command "library-curation --profile fidelity_conversion"
    Then the command should succeed
    And the converted symbol output should represent all Eagle symbols:
      | SymbolName    |
      | R             |
      | TPS7A47       |
      | AGND          |
      | UNIQUE_SENSOR |
    And the converted footprint output should represent all Eagle packages:
      | PackageName        |
      | R_0603_1608Metric  |
      | SOT223             |
      | UNIQUE_SENSOR_PAD  |

  Scenario: curated_generation is deterministic for identical inputs and policy
    When I run e2k command "library-curation --profile curated_generation" twice
    Then the command should succeed
    And the curated symbol outputs should be byte-identical
    And the curated footprint outputs should be byte-identical
    And the decision reports should be byte-identical
