# imgoofy:)

An empty Cydia/APT repository, ready for its first package.

## Add packages

1. Put your `.deb` files directly in `packages/`.
2. Double-click **Build Repo.cmd** on Windows, or run `python build_repo.py`.
3. Upload your changes to the GitHub repository. Its Actions workflow rebuilds and publishes `public/` automatically.

You can also upload `.deb` files directly into the `packages` folder on GitHub. No local build is needed for that route. Nothing here installs a package on your PC or phone.

Do not rename an IPA to DEB. These are packages for compatible jailbroken devices. The package's architecture, dependencies, and firmware requirements remain unchanged. `iphoneos-arm` packages target rootful iOS; `iphoneos-arm64` packages target rootless iOS. Hosting both does not convert either one.

## GitHub Pages setup

This repository is hosted at https://github.com/totallyrealn-dev/imgoofy. Its source files include `.github/workflows/publish.yml`. Do not upload unrelated files from the parent workspace.

Under **Settings → Pages → Build and deployment → Source**, choose **GitHub Actions**. Run **Build and publish Cydia repo** from the Actions tab if needed.

The source address is:

`https://totallyrealn-dev.github.io/imgoofy/`

In Cydia: **Sources → Edit → Add**, then paste that URL. An empty package list is expected until the first package is uploaded. For an APT sources file, the flat repository format is:

```text
deb https://totallyrealn-dev.github.io/imgoofy/ ./
```

## What is generated

`public/` contains the landing page, `Release`, `Packages`, gzip/bzip2/xz indexes, and content-addressed copies of packages. Release and package metadata include SHA-256 and SHA-512 checksums, plus legacy checksums for older clients. Only `public/` is deployed by the workflow.

This starter is **unsigned**. Hashes detect changed downloads but are not an author signature. Signing with a repository GPG key can be added separately. Never publish a private signing key. The builder refuses to replace previously signed output without a new signing step.

The Windows-compatible builder reads control archives compressed with gzip, xz, or bzip2, and uncompressed control archives. Other control compression, including zstd, produces a clear error instead of an incomplete index. No package payload or maintainer script is executed. Duplicate package/version/architecture combinations are rejected.

Removing a package from `packages/` removes it from the next index. Local `public/debs/` retains old content-addressed files to avoid deleting downloads still used by clients; GitHub Actions publishes a clean build each time.

## Checks

```text
python -m unittest discover -s tests -v
```

The tests use temporary synthetic packages only. They are never included in the public repository.

References: [Cydia repository format](https://www.saurik.com/packaging.html), [Theos packaging](https://theos.dev/docs/packaging), [GitHub Pages](https://docs.github.com/en/pages).
