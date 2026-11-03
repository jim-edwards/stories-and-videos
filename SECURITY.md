# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use GitHub's private reporting instead:
on the repository's **Security** tab choose **Report a vulnerability**. You will get a reply as soon as
the maintainer can manage, and a fix is released as a new patch version.

## Supported versions

Only the newest release (the `latest` image tag, and the `1` tag while it is the 1.x line) gets fixes.
Images are rebuilt every week so operating-system fixes arrive without a code change; pull regularly.

## How releases are protected

* Images are built only by the GitHub Actions workflows in this repository, never by hand.
* Each image is signed with Sigstore/cosign (keyless) and carries an SBOM and build provenance.
  The README shows how to verify a signature.
* Dependencies and the base image are updated automatically (Dependabot), and every change is scanned
  with Trivy and CodeQL before it can be merged. The published image is re-scanned daily.
