# Enforcing the shared maintenance application contract

This follows OS PR #48 / app PR #177. A label alone was previously only a
capability declaration: an older image could still be preloaded or installed
and ignore the shared lock. These changes enforce the declaration at the image
build and application installation boundaries.

## Checks

The actual preloaded OCI archive's raw configuration must declare Linux/arm64
and `config.Labels["io.epilykos.maintenance-lock"] = "flock-v1"`. Missing, unknown,
malformed and wrong-platform values fail the real board post-build hook before
it writes image.env. The config bytes that passed validation are also the bytes
hashed to obtain the image ID. A failed skopeo inspection cannot turn into the
hash of empty output through a shell pipeline.

Before creating an app update job, while holding the shared lease, inspect the
running container. Require the protocol label, a running state, exactly the
expected maintenance-lock environment setting, and the writable bind from
`/data/epilykos/app-data` to `/app/data`. Reject duplicate/shadowed lock settings
or a different mount, because those would lock different inodes. A rejection
occurs before pulling or restarting.

After pulling a signed-manifest candidate and before activation, inspect its
image config and require the same protocol, Linux/arm64 and a valid image ID.
An incompatible candidate is not restarted or promoted; the existing running
app remains. Its downloaded cache may remain for later safe cache accounting;
this change does not prune rollback images to make space.

These checks validate cooperation among trusted releases. They are not secure
boot or proof that malicious code obeys its own label.

## Rollout dependency

The cooperating app was released to `dev` on 2026-10-20 through app PR #187
(including the prerequisite app PR #177). App commit
`a35183cd57fa8260922e71ff1c566d5daccb3368` has exactly the source tree of the
reviewed PR head `3304f787ee32d46b2548b9e117923453cf879751`.

The normal verified release bridge merged OS PR #51. `manifests/dev.yaml`
now pins `1.0.1-dev+a35183c`, sequence 12, to:

```
irunmole/epilykos@sha256:3fcffa0716c09b4e29b2413bfb3f12f8e5131cded75aef07ec87dd75001ef211
```

This is the multi-platform index digest recorded by the successful
[app build](https://github.com/ashipaek0/Epilykos/actions/runs/39390628562),
not a digest derived from a mutable tag. The source verifier authenticated it
against that run's immutable `image-digest-epilykos` artifact (ID 12249636580).
The [bridge](https://github.com/ashipaek0/epilykos-os/actions/runs/39391183511)
and [signed publication](https://github.com/ashipaek0/epilykos-os/actions/runs/39391231742)
both passed; the downloaded `app-dev` manifest signature was independently
verified with `keys/app-manifest.pub`.

Independent registry reads verified the SHA-256 chain from that index to its
Linux/arm64 manifest and raw config. The actual config declares `flock-v1`,
matches the CI commit/version/build identity, and passes
`tools/check_app_compatibility.py`, producing image ID
`1993d18ebdcfa23d9d0df176b759cfa9f8c67c0cb5c6fe33e1579d946587b9ae`.
The compatibility branch includes this real pin. The legacy image remains
incompatible; no gate exception was added. OS PRs #45 and #48 remain branch
dependencies. This dev release does not promote stable or establish Pi boot
acceptance.

Existing DATA may carry promoted application state from an older OS. Adoption of
that state still needs the OS update preflight in U02; it must not be treated as
accepted v1 evidence merely because a fresh-image preload passed. The existing
runtime restart recovery and app/database migration compatibility remain
separate unfinished work.

## Evidence

The real post-build fixture rejected missing protocol, wrong platform and
malformed config only after the fix (all three were accepted before it).
The updater integration fixture refused incompatible running configurations
before pull/job creation and incompatible candidates before restart. It retains
real signatures, replay checks, socket dispatch and promotion/rollback tests.
Additional tests cover shadow mounts, duplicate environment values, malformed
inspection data and timeout errors. No physical boot or complete image build is
claimed by these fixtures.

The inspected field shapes follow primary Podman documentation:
[image inspect](https://docs.podman.io/en/latest/markdown/podman-image-inspect.1.html)
and [container inspect](https://docs.podman.io/en/latest/markdown/podman-container-inspect.1.html).
Installation-size budgets are not inferred from image labels or compressed
registry sizes. Current signed manifests do not contain those bounds; producing
and authenticating them remains release-pipeline work before host budget
admission can be completed.
