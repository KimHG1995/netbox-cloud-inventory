# Bilingual README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish equivalent Korean and English README files with a clear language switch while keeping English as the GitHub and package default.

**Architecture:** Move the current Korean document to `README.ko.md` and create a natural English translation at `README.md`. Both root-level files share the same section order, commands, identifiers, links, and warnings, while the existing package metadata continues to point to `README.md`.

**Tech Stack:** GitHub-flavored Markdown, Python package metadata, Git, GitHub CLI

## Global Constraints

- `README.md` is the default English document.
- `README.ko.md` preserves the complete Korean document.
- The first content line in both files is the language switch.
- Both files keep the same section order, commands, URLs, Parser profile IDs, and supported resource scope.
- `pyproject.toml` continues to use `readme = "README.md"`.
- Korean-only internal documents are labeled `(Korean)` in the English README.
- A feature or workflow change updates both README files in the same commit.
- Neither README uses the middle dot character.
- This change updates the existing `feature/local-workflow-split` branch and Draft PR instead of opening another PR.

---

### Task 1: Preserve the Korean README

**Files:**
- Move: `README.md` to `README.ko.md`
- Modify: `README.ko.md`

**Interfaces:**
- Consumes: the existing 290-line Korean README
- Produces: the canonical Korean project entry document at `README.ko.md`

- [ ] **Step 1: Move the existing Korean content without rewriting it**

Use an apply-patch move so Git records the document as a rename. Preserve every existing section, command, link, warning, and list item.

- [ ] **Step 2: Add the Korean language switch**

Insert immediately below the `# NetBox Cloud Inventory` heading:

```markdown
한국어 | [English](README.md)
```

- [ ] **Step 3: Add the bilingual README design link**

In the `설계문서` section add:

```markdown
- [한글과 영문 README 운영 설계](docs/superpowers/specs/2026-07-30-bilingual-readme-design.md)
```

- [ ] **Step 4: Verify the Korean document**

Run:

```bash
test -f README.ko.md
rg -n '^한국어 \\| \\[English\\]\\(README\\.md\\)$' README.ko.md
rg -n '2026-07-30-bilingual-readme-design\\.md' README.ko.md
```

Expected: all commands exit 0 and each `rg` prints exactly one match.

### Task 2: Create the English README

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: `README.ko.md`, `docs/manual-import.md`, `LICENSE`, `schemas/import-bundle-v1.schema.json`, the design documents
- Produces: the default GitHub and Python package README at `README.md`

- [ ] **Step 1: Add the English heading and language switch**

Start the file with:

```markdown
# NetBox Cloud Inventory

[한국어](README.ko.md) | English
```

- [ ] **Step 2: Translate the introduction and quick start**

Write natural English that preserves these facts exactly:

- The project is an open-source side project evaluating a centralized infrastructure inventory for fragmented AWS and NAVER Cloud Platform commercial and government environments.
- The current PoC prioritizes manual file collection and lookup.
- AWS and NCP API collectors are not implemented.
- Required tools remain Docker Compose v2, Python 3.12, uv, OpenSSL, and curl.
- `start_local.sh` starts an empty environment.
- `load_demo.sh` explicitly loads fixed synthetic data.
- `test_integration.sh` runs randomized development and CI verification.
- `poc_import.sh` remains a deprecated compatibility wrapper.
- `docker compose down` preserves data and `docker compose down -v` deletes NetBox data, Import history, and Artifacts.

Keep every command block and local URL byte-for-byte identical to the Korean document.

- [ ] **Step 3: Translate goals, scope, and collection paths**

Use these English section names in the same order:

```text
Goals
Initial Scope
Supported Providers and Environments
Collected Resources
Future Scope
Collection Methods
Future Automated Collection
Manual Upload
Processing Flow
```

Translate NCP `민간` as `commercial` and `공공` as `government`. Do not claim that the future automated collectors already exist.

- [ ] **Step 4: Translate identity, mapping, and safety sections**

Use these section names:

```text
Resource Identity
NetBox Mapping
Safe Synchronization Principles
Components
Security Principles
Project Status
Design Documents
License
```

Preserve the `cloud_uid` formula and examples exactly. Preserve all NetBox target type names and all safety thresholds, including three consecutive misses, seven days, 100 MB, 20 files, and 30 days.

- [ ] **Step 5: Label Korean-only documents**

Use these exact links in the English document:

```markdown
[Manual import operations guide (Korean)](docs/manual-import.md)
[NetBox Cloud Inventory design (Korean)](docs/superpowers/specs/2026-07-28-netbox-cloud-inventory-design.md)
[Bilingual README operations design (Korean)](docs/superpowers/specs/2026-07-30-bilingual-readme-design.md)
[Import Bundle JSON Schema](schemas/import-bundle-v1.schema.json)
```

- [ ] **Step 6: State the synchronization rule**

After the language switch, include a short maintenance note:

```markdown
Documentation maintenance: workflow and feature changes must update both `README.md` and `README.ko.md` in the same commit.
```

Add the equivalent Korean sentence after the language switch in `README.ko.md`:

```markdown
문서 운영 원칙: 실행 절차와 기능 변경은 `README.md`와 `README.ko.md`를 같은 커밋에서 함께 수정합니다.
```

### Task 3: Verify Parity, Package Metadata, and Publish the PR Update

**Files:**
- Verify: `README.md`
- Verify: `README.ko.md`
- Verify: `pyproject.toml`
- Verify: `docs/superpowers/specs/2026-07-30-bilingual-readme-design.md`
- Verify: `docs/superpowers/plans/2026-07-30-bilingual-readme.md`
- External update: existing Draft PR #1

**Interfaces:**
- Consumes: both completed README files and the current feature branch
- Produces: one bilingual documentation commit pushed to the existing PR

- [ ] **Step 1: Compare structural parity**

Run:

```bash
uv run python - <<'PY'
from pathlib import Path
import re

documents = {
    name: Path(name).read_text(encoding="utf-8")
    for name in ("README.md", "README.ko.md")
}

def code_blocks(text: str) -> list[str]:
    return re.findall(r"```[^\n]*\n(.*?)\n```", text, flags=re.DOTALL)

def parser_profiles(text: str) -> list[str]:
    pattern = r"`((?:aws|ncp|canonical)\.[a-z0-9_.]+\.v1)`"
    return sorted(set(re.findall(pattern, text)))

assert code_blocks(documents["README.md"]) == code_blocks(
    documents["README.ko.md"]
)
assert parser_profiles(documents["README.md"]) == parser_profiles(
    documents["README.ko.md"]
)
assert len(parser_profiles(documents["README.md"])) == 6
for required in (
    "README.md",
    "README.ko.md",
    "start_local.sh",
    "load_demo.sh",
    "test_integration.sh",
):
    assert all(required in text for text in documents.values()), required
PY
```

Expected: the command exits 0 without output.

- [ ] **Step 2: Validate relative Markdown links**

Run:

```bash
uv run python - <<'PY'
from pathlib import Path
import re

root = Path.cwd()
for readme_name in ("README.md", "README.ko.md"):
    text = (root / readme_name).read_text(encoding="utf-8")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        if target.startswith(("http://", "https://", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        resolved = root / relative_target
        assert resolved.exists(), f"{readme_name}: missing {target}"
PY
```

Expected: the command exits 0 without output.

- [ ] **Step 3: Verify package metadata and prohibited text**

Run:

```bash
rg -n '^readme = "README\\.md"$' pyproject.toml
rg -n $'\u00b7' README.md README.ko.md
git diff --check
```

Expected: the metadata match prints once, the prohibited character search prints nothing, and the diff check exits 0.

- [ ] **Step 4: Run the project verification suite**

Run:

```bash
uv run ruff check src tests scripts
uv run mypy src
uv run pytest tests/unit tests/api tests/scripts -q
uv run python scripts/export_import_schema.py --check
```

Expected: every command exits 0.

- [ ] **Step 5: Commit the bilingual documentation**

Run:

```bash
git add README.md README.ko.md docs/superpowers/specs/2026-07-30-bilingual-readme-design.md docs/superpowers/plans/2026-07-30-bilingual-readme.md
git commit -m "docs: add Korean and English readmes"
```

- [ ] **Step 6: Push the existing feature branch**

Run:

```bash
git push origin feature/local-workflow-split
```

- [ ] **Step 7: Update and verify Draft PR #1**

Update the existing PR body to mention:

- English is now the default `README.md`.
- Korean documentation is available at `README.ko.md`.
- Both files contain language switches and equivalent commands.
- The verification result from Step 4.

Verify:

```bash
gh pr view 1 --repo KimHG1995/netbox-cloud-inventory --json url,isDraft,baseRefName,headRefName,statusCheckRollup
```

Expected: the URL is PR #1, the PR remains Draft, base is `main`, head is `feature/local-workflow-split`, and the new Quality check is visible.
