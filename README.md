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

## Layout

```text
contracts/
  epilykos-os-contracts.yaml      source of truth (machine-readable registry)
  narrative.md                    prose sections of the contract document
  EPILYKOS-OS-CONTRACTS.md        GENERATED from the two files above
  archive/                        superseded revisions (v0.3)
tools/
  validate_contracts.py           `make contracts` rules (§8)
  render_contracts.py             regenerates the .md
  test_validate_contracts.py      proves every rule actually fails
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
