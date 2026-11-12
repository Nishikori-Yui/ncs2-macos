# Project license decision note

The project owner selected the MIT License on 2026-11-12. The canonical text is
in the root [`LICENSE`](../LICENSE) file and applies to this repository's
original source. This note records the decision and its boundary; it does not
change the licenses of third-party components.

The two practical permissive candidates for this repository are:

| Option | What it gives downstream users | Extra obligations and tradeoffs | Fit for this repository |
| --- | --- | --- | --- |
| MIT | A short permission grant covering use, copying, modification, publication, distribution, sublicensing and sale, with preservation of the copyright and license notice | Very short and familiar; it does not include an express patent grant | Good if the goal is the smallest contributor and downstream notice surface |
| Apache-2.0 | Permissive copyright rights plus an express contributor patent license | Preserve license and attribution notices, mark changed files, and carry NOTICE material where applicable; includes a patent-litigation termination condition | Stronger fit if the native C++/Swift tooling is expected to receive outside contributions or be reused in products |

This comparison applies only to this repository's original source. It does not
change the licenses of OpenVINO, its submodules, libusb, or Intel firmware.
Those components remain governed by their own notices and distribution terms.
The third-party inventory is in `docs/THIRD_PARTY_REVIEW.md`.

The selected project license is **MIT**. It was chosen for the short,
permissive notice and low downstream friction. The MIT License does not provide
an express patent grant, and it does not authorize redistribution of OpenVINO,
libusb, Intel firmware, or any copied runtime. Those components remain subject
to their own notices and terms.

The decision record confirms:

1. The copyright holder in `LICENSE` is `Nishikori-Yui`.
2. The owner controls or may license all original files in the
   public source candidate.
3. Third-party source notices remain separate from the project license.
4. Firmware and packaged-runtime redistribution permission is not inferred from
   project license.

References:

- [OSI MIT License](https://opensource.org/license/mit)
- [OSI Apache License 2.0](https://opensource.org/license/apache-2.0)
- [Third-party provenance review](THIRD_PARTY_REVIEW.md)
