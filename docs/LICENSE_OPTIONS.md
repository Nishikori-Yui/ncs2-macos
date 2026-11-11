# Project license decision note

This note prepares the project-owner decision. It does not select or apply a
license. Until a root `LICENSE` file is committed, the public repository
remains a source preview and should not be described as an open-source release.

The two practical permissive candidates for this repository are:

| Option | What it gives downstream users | Extra obligations and tradeoffs | Fit for this repository |
| --- | --- | --- | --- |
| MIT | A short permission grant covering use, copying, modification, publication, distribution, sublicensing and sale, with preservation of the copyright and license notice | Very short and familiar; it does not include an express patent grant | Good if the goal is the smallest contributor and downstream notice surface |
| Apache-2.0 | Permissive copyright rights plus an express contributor patent license | Preserve license and attribution notices, mark changed files, and carry NOTICE material where applicable; includes a patent-litigation termination condition | Stronger fit if the native C++/Swift tooling is expected to receive outside contributions or be reused in products |

This comparison applies only to this repository's original source. It does not
change the licenses of OpenVINO, its submodules, libusb, or Intel firmware.
Those components remain governed by their own notices and distribution terms.
The third-party inventory is in `docs/THIRD_PARTY_REVIEW.md`.

The repository's current engineering recommendation is **Apache-2.0 as the
default candidate**, because this project contains native systems code and is
intended to accept future tooling contributions. This is a recommendation for
the owner to approve, not an automatic license selection. If the owner prefers
the shortest possible permissive notice, MIT is the alternative.

Before committing either option:

1. Replace the copyright holder placeholder with the owner identity to use.
2. Confirm that the owner controls or may license all original files in the
   public source candidate.
3. Keep third-party source notices separate from the project license.
4. Do not infer firmware or packaged-runtime redistribution permission from the
   project license.

References:

- [OSI MIT License](https://opensource.org/license/mit)
- [OSI Apache License 2.0](https://opensource.org/license/apache-2.0)
- [Third-party provenance review](THIRD_PARTY_REVIEW.md)
