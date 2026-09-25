# EpilykosOS

Appliance OS for [Epilykos](https://github.com/ashipaek0/epilykos) on the Raspberry Pi 5 — **Track 2** of the project. Track 1, the ordinary Docker Compose deployment, lives in the application repository and is not governed here (invariant `I-001`).

This repository will hold the contracts, the OS image build, RAUC configuration, Quadlets and provisioning. It consumes the Epilykos application **only as OCI images pinned by digest**, never as source.

## Status

Pre-Stage 0. The contracts are at **v0.4-draft**; nothing is built yet. Start with [`contracts/EPILYKOS-OS-CONTRACTS.md`](contracts/EPILYKOS-OS-CONTRACTS.md). The Stage 0 gate needs the boot-chain spike (`C-BOOT-000`) and the decisions due at Stage 0 (`D-PLATFORM-001`, `D-BOOT-002`, `D-RECOVERY-001`, `D-STORAGE-001`).

## Branches and releases

| Branch | Purpose |
|---|---|
| `dev` | Integration. Developer images and the `dev` channel only. |
| `main` | Stable releases only, merged from `dev` after review. |

The application repository follows the same model. Stable appliance releases pin only application images built from the application's `main` branch (`I-009`, `C-RELEASE-001`).

## Keeping in step with the application

The application repository notifies this one after every image build; a workflow here opens a pull request (`app-sync/dev` or `app-sync/stable`) that pins the new image digests. **Review and merge that PR to accept the release** — nothing is merged automatically. Details: §0 of the contract.

One-time setup:

1. Create a **fine-grained personal access token** (GitHub → Settings → Developer settings → Fine-grained tokens): repository access *only* `ashipaek0/epilykos-os`, permission **Contents: Read and write**.
2. In **`ashipaek0/epilykos`** → Settings → Secrets and variables → Actions, add it as `EPILYKOS_OS_DISPATCH_TOKEN`.
3. In **this** repository → Settings → Actions → General → Workflow permissions, tick **Allow GitHub Actions to create and approve pull requests**.

Without the token the application builds still succeed; they just don't notify this repository. A missed release can be replayed from the **Actions → App release sync → Run workflow** form by pasting the payload shown in the application build log.

## Layout

```text
contracts/
  epilykos-os-contracts.yaml      source of truth (machine-readable registry)
  narrative.md                    prose sections of the contract document
  EPILYKOS-OS-CONTRACTS.md        GENERATED from the two files above
  archive/                        superseded revisions (v0.3)
manifests/                        dev.yaml / stable.yaml application manifests (written by the sync)
release/digest-records/           dev.json / main.json image digest records (JSON lines)
tools/
  validate_contracts.py           `make contracts` rules (§8)
  sync_app_release.py             applies an app-image-published payload
  render_contracts.py             regenerates the .md
  test_validate_contracts.py      proves every rule actually fails
  test_sync_app_release.py        bridge self-tests
evidence/                         per-stage evidence (§10)
```

Directories the validator checks as soon as they exist: `quadlets/`, `manifests/`, `release/digest-records/`.


## Working on the contracts

```bash
pip install pyyaml
# edit contracts/epilykos-os-contracts.yaml (and narrative.md for prose)
make render      # regenerate the Markdown document
make contracts   # validate — this is what CI runs
make test        # validator self-tests
```

A change that conflicts with a contract must change the contract through review or be rejected (§11).
