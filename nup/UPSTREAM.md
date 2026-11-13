# Vendored copy of nup

Copied from <https://github.com/a55uka/nup> (`tools/nup/nup`) at commit
`d40d9cfa50792ba8021d2967d994d1b1c25d62b0`. MIT licensed, see `LICENSE`.

## Local changes

- `pack.py`: `zc.compressobj(size=fnode.size)` instead of `zc.compressobj()`.
  Without it, files over 256 MiB (the streaming path) were written as zstd frames
  with no content size, which the SPEC codec table forbids. Upstream issue #23, PR #29.
- `format.py`: `NPDM = 1` instead of `NPDM = 0`. Every metadata TLV type was one lower than
  SPEC section 5, so written metadata was mislabelled and spec-conformant files misread.
  Upstream PRs #22 and #28. Containers written with the shifted IDs need repacking.

Both are open upstream as of 2026-11-06. Drop these changes once they land.

Keep this list up to date when refreshing the copy.
