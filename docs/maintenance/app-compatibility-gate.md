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

The currently pinned app source predates the cooperating implementation. Do not
edit the digest or bypass the check to make an image green. Merge/release the
cooperating app, use the normal verified image-release bridge to produce its OS
manifest PR, then pin that release. This gate intentionally blocks image builds
using the old pinned app. OS PRs #45 and #48 are branch dependencies; no PR is
merged or stable image published by this work.

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
