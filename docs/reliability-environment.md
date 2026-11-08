# CI environment baseline

The merge gate installs one locked dependency set and one toolchain.
A separate workflow installs the floating set so drift stays visible
without becoming a required check.

## Merge-gate baseline

| Piece | Pin |
| --- | --- |
| Runner | `ubuntu-24.04` |
| Python | 3.10, 3.11, and 3.12, as the existing jobs already select |
| pip | 26.3 |
| Node | 22.23.4 |
| Chrome | 156.0.8078.47 |
| playwright-core | 1.64.1 |
| Python packages | `requirements/ci-constraints.txt` |

`requirements/ci-constraints.txt` was resolved for Python 3.10 on
manylinux and re-resolved to the same versions for 3.11 and 3.12.
A local 3.10 environment installed that file with
`pip install -c requirements/ci-constraints.txt -e ".[all,test]"`.
`pyproject.toml` still declares ranges for library users. The merge
gate and the tag release tests pass `-c requirements/ci-constraints.txt`,
so those ranges do not float in either workflow.

`pytest` is locked at 9.2.0. The historical Dash plugin warning is
already ignored in `pytest.ini`. The lock stops a later pytest from
moving the gate by itself.

## Package build

The source and wheel jobs in `ci.yml` and `release.yml` install
`build`, `setuptools`, and `setuptools-scm` with
`-c requirements/ci-constraints.txt`, set `PIP_CONSTRAINT` to that
file, and run `python -m build --no-isolation`. The backends are
`setuptools==84.2.1` and `setuptools-scm==8.3.1`. That setuptools-scm
release depends on `packaging`, `setuptools`, and, below Python 3.11,
`tomli`, which are already pinned. `build==1.6.1` and
`pyproject-hooks==1.3.3` are pinned in the same file. The jobs build
with those installed versions.

## Runner label and actual OS

`record_ci_environment.py` takes `--expected-runner` from the calling
workflow. The merge-gate jobs pass `ubuntu-24.04`. The
compatibility-drift workflow passes `ubuntu-latest`, the same label
as its `runs-on`. The JSON field `declared.runner` is that label.
`actual_os` is separate and comes from `platform` and
`/etc/os-release` (`id`, `version_id`, `pretty_name`).

The Camera WebGL job uploads both `browser-artifacts/` and
`ci-diagnostics-browser` with `if: always()`. `browser-artifacts/` is
the browser trace directory. `ci-diagnostics-browser` is the
environment JSON, the freeze, the JUnit file, and the failure
signatures.

## MolCrysKit

Every install job uses the git commit in `pyproject.toml`:

`f4f81afa4f2b57ad7fa8e88e4816cbcb96a30556`

That commit reports version `0.8.1.dev18`. The fast-job window check
still requires the installed version to be at least 0.8.0 and below
0.9.0. The window is a check on the pin. It is not a PyPI minimum
install. Job names say pinned molcrys-kit for that reason. PyPI
0.8.0 does not provide the BondRecord and connectivity APIs this
tree imports.

Each job writes `ci-diagnostics/ci-environment.json` and
`ci-freeze.txt`. The environment file includes the MolCrysKit version
and, for a VCS install, `direct_url.json` with the resolved commit.
Artifacts are uploaded with `if: always()`.

## Attempts and cancellations

`ci-diagnostics/ci-attempt.json` records `github.run_attempt`.

- `run_attempt` 1 is the first attempt.
- `run_attempt` greater than 1 is a rerun recovery, not a clean
  first-attempt success.
- A cancelled workflow is the concurrency group's
  `cancel-in-progress` behavior. It is not a test flake and it is
  not a rerun recovery.

JUnit failures are copied to `ci-diagnostics/failure-signatures.json`
as classname, test name, and the first line of the message. The
signature step does not decide pass or fail.

## Drift check

`.github/workflows/compatibility-drift.yml` runs on a weekly schedule
and on `workflow_dispatch`. It does not run on pull requests. It uses
`ubuntu-latest`, Node 22, Chrome `stable`, an unpinned
`playwright-core`, and `pip install -e ".[all,test]"` without the
constraints file. It launches Chrome and runs the fast non-browser
suite. It has no `continue-on-error`. It is not a required gate.

`docs-policy-warn` stays warn-only. Required test, browser, minimal,
benchmark, and package jobs do not use `continue-on-error`.

## Not claimed here

Five fresh Camera WebGL workers and a green required-job matrix are
CI results. This document does not treat a local lock install as
those runs. An intentional scene-delivery failure still fails the
browser job; the signature and diagnostic artifacts name the test.
