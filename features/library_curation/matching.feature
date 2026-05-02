Feature: library_curation matching behavior and variant collapse
  As a KiCad migration engineer
  I want matching outcomes to be explicit and safe
  So that variant explosion is reduced without hiding ambiguity

  Background:
    Given an e2k CSV sandbox
    And workflow "library_curation" is selected

  Scenario: safe symbol collapse reduces Eagle device and package combinatorics
    Given an Eagle library contains symbols:
      | SymbolName | Role                 | PinCount |
      | REG_X      | functional_component | 8        |
    And the Eagle library contains devices:
      | DeviceSet   | Device | SymbolName | PackageName | MappedPinCount |
      | REGULATOR_X | A      | REG_X      | DIP8        | 8              |
      | REGULATOR_X | B      | REG_X      | SOIC8       | 8              |
      | REGULATOR_X | C      | REG_X      | TSSOP8      | 8              |
      | REGULATOR_X | D      | REG_X      | MSOP8       | 8              |
    And a KiCad symbol corpus contains:
      | LibraryNickname   | SymbolName | PinCount | DefaultFootprint |
      | Regulator_Linear  | REG_X      | 8        |                  |
    And a KiCad footprint corpus contains:
      | LibraryNickname  | FootprintName       | PadCount |
      | Package_DIP      | DIP-8_W7.62mm       | 8        |
      | Package_SO       | SOIC-8_3.9x4.9mm    | 8        |
      | Package_SO       | TSSOP-8_3x3mm       | 8        |
      | Package_SO       | MSOP-8_3x3mm        | 8        |
    When I run e2k command "library-curation --profile curated_generation"
    Then the command should succeed
    And the mapping summary for deviceset "REGULATOR_X" should contain:
      | Metric                    | Value |
      | input_device_variants     | 4     |
      | curated_symbol_variants   | 1     |
      | covered_package_variants  | 4     |
      | unresolved_variants       | 0     |

  Scenario: ambiguous symbol candidates are queued and not auto-approved
    Given an Eagle library contains symbols:
      | SymbolName | Role                 | PinCount |
      | MCU_X      | functional_component | 64       |
    And the Eagle library contains devices:
      | DeviceSet | Device | SymbolName | PackageName | MappedPinCount |
      | MCU_X     | QFP64  | MCU_X      | TQFP64      | 64             |
    And a KiCad symbol corpus contains:
      | LibraryNickname | SymbolName | PinCount | DefaultFootprint                    |
      | MCU_ST          | STM32F103  | 64       | Package_QFP:LQFP-64_10x10mm_P0.5mm |
      | MCU_NXP         | LPC1768    | 64       | Package_QFP:LQFP-64_10x10mm_P0.5mm |
    And a KiCad footprint corpus contains:
      | LibraryNickname | FootprintName          | PadCount |
      | Package_QFP     | LQFP-64_10x10mm_P0.5mm | 64       |
    When I run e2k command "library-curation --profile curated_generation"
    Then the command should succeed
    And the decision report contains rows:
      | DeviceKey   | Classification | ReviewQueue |
      | MCU_X:QFP64 | ambiguous      | priority    |
    And no approved curated mapping should exist for device "MCU_X:QFP64"

  Scenario: inline override can force transform_eagle over available KiCad reuse
    Given an Eagle library contains symbols:
      | SymbolName | Role                 | PinCount |
      | R          | functional_component | 2        |
    And the Eagle library contains devices:
      | DeviceSet | Device | SymbolName | PackageName       | MappedPinCount |
      | RESISTOR  | R0603  | R          | R_0603_1608Metric | 2              |
    And a KiCad symbol corpus contains:
      | LibraryNickname | SymbolName | PinCount |
      | Device          | R          | 2        |
    And a KiCad footprint corpus contains:
      | LibraryNickname | FootprintName     | PadCount |
      | Resistor_SMD    | R_0603_1608Metric | 2        |
    And matching policy overrides are:
      | Key                  | Value |
      | prefer_kicad_symbols | false |
    When I run e2k command "library-curation --profile curated_generation"
    Then the command should succeed
    And the decision report contains rows:
      | DeviceKey      | Classification   |
      | RESISTOR:R0603 | override_applied |
    And the curated symbol for device "RESISTOR:R0603" should have origin "transformed_eagle"
