# NOMMAN (Nomenclature Management)

NOMMAN validates organism nomenclature for clinical microbiology laboratories to ensure report accuracy and compliance with **CAP MIC.11375** and **CLSI M64**.

## 🚀 Core Capabilities
*   **Multi-Source Inventory**: Consolidates names from multiple instruments/LIS into one inventory.
*   **Priority Validation**: Matches names against a hierarchy of authorities (User lists $\rightarrow$ LPSN).
*   **Automated Correction**: Identifies correct names, suggests replacements, or flags unmatched taxa.
*   **Domain Impact Assessment**: Alerts users if a name replacement changes an organism's membership in critical clinical/regulatory groups.

## ⚙️ Configuration (`config.toml`)
NOMMAN uses a TOML file to define authority priority (lower rank = higher priority) and file paths.

| Section | Key | Description |
| :--- | :--- | :--- |
| `auth_providers` | `assert_rank` / `_file` | User Assert List priority and path. |
| | `lpsn_rank` / `_file` | LPSN Offline DB priority and path. |
| `domains` | `domains_file` | Path to domain definitions for impact analysis. |
| `lpsn_api` | `username` | API username for higher-taxon expansion. *(Optional if not using this feature)*|

*Set rank to a negative integer (e.g., `-1`) to disable a provider.*  
*Priority is determined by the `rank` value in `config.toml` (lower numbers are processed first); names matched by a higher-priority authority are not passed to lower-priority ones.*

## 📚 Input Data Formats

### 1. User Assert List
Override authorities using two sections:
*   `[accept]`: Whitelist of names to mark as **Correct**.
*   `[reject]`: Blacklist. Use `||` for replacements (e.g., `Old Name || New Name`).

### 2. Domain Definitions
Groupings used to assess clinical impact.
*   **Simple Taxon**: `Staphylococcus aureus` (Case-sensitive match).
*   **Custom Group**: `Group Name || Sp1, Sp2` (User-defined labels).
*   **Higher Taxon**: `Caldilineales^` (Suffix `^` LPSN API expansion to all constituent genera and species. **Requires API credentials**).

### 3. Laboratory Inventory
*   **Single File**: A text file listing organism names.
*   **Filelist**: A CSV mapping system names to files (`SystemName, PathToFile`).

## 🛠 Usage

### Prerequisites
For secure LPSN API password storage, requires a system keyring (e.g., Windows Credential Manager, macOS Keychain, or Secret Service/KWallet on Linux).

### Installation
```bash
pipx install nomman-x.y.z.whl  # or use uv tool install
```

### Credential Management
NOMMAN uses the system keyring for LPSN API passwords:
*   `nomman --store_lpsn_pw`: Save password to keyring.
*   `nomman --clear_lpsn_pw`: Remove password from keyring.

### Running Classification
```bash
# Process a single file
nomman -i names.txt

# Process a multi-system CSV filelist
nomman -l list.csv

# Options
nomman -i names.txt -g custom_domains.txt -f html -o report.html
```
**Key Flags:**
*   `-g`: Override default domains file.
*   `-f`: Output format (`md` or `html`). Defaults to `md`.
*   `-o`: Save report to a specific file (otherwise prints to stdout).
*   `-v` / `-b`: Verbose or Brief logging.
*   `-t`: Perform a standalone LPSN API test query.

## 📊 Understanding the Report (Markdown version)
The report provides:
1.  **Execution Metadata**: Timestamp, platform, and command used.
2.  **Authority Summary**: Checksums and summaries of the reference databases used.
3.  **Classification Tables**:
    *   **Matched**: Taxon name, reporting systems, status (Adopted/Rejected), reason, suggested replacement, and Domain Impact.
        *   *Domain Impact*: Alerts if a replacement name moves an organism into or out of a clinical group (e.g., losing a 'CLSI M100' grouping due to a reclassification).
    *   **Unmatched**: Taxa not found in any authority.