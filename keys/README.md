# Application-release key (D-UPDATE-001)

Appliances install an Epilykos update from Settings → Updates only if its manifest is signed with this key. The public half, `app-manifest.pub`, is pinned into every OS image by `post-build.sh`. The private half lives only in the `APP_MANIFEST_SIGNING_KEY` repository secret. It is not the RAUC release CA (C-UPDATE-001).

Create the pair once, on your own computer:

```bash
openssl genpkey -algorithm ed25519 -out app-manifest.key
openssl pkey -in app-manifest.key -pubout -out keys/app-manifest.pub
```

1. Commit `keys/app-manifest.pub`.
2. Paste the whole of `app-manifest.key` into **Settings → Secrets and variables → Actions → New repository secret**, named `APP_MANIFEST_SIGNING_KEY`.
3. Store the key file somewhere safe and offline, then delete it from the working tree. `.gitignore` refuses `*.key`.

The `App manifest publish` workflow then signs `manifests/<channel>.yaml` after every merge and publishes it on the `app-dev` or `app-stable` release.

Only images built after the public key is committed can verify updates. Older images keep their preloaded app until they are reflashed.

Changing the key means every appliance in the field stops accepting updates until it gets an OS image that pins the new key.
