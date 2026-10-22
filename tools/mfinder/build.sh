#!/bin/bash
# Build the mfinder 1.2 command-line binary for this machine: copy the pristine vendored source
# (tools/mfinder/src) to tools/mfinder/build, apply seed.patch, compile to tools/mfinder/bin/mfinder.
#
# Source files and flags as in the original Makefile (tools/mfinder/src/Makefile): -O3 -DUNIX=1.
# The compiler warns that common.h redefines INFINITY (as 0x0fffffff, after <math.h>); mfinder's
# own value is the one in effect, as its authors intended.
#
# Usage: tools/mfinder/build.sh
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
build="$here/build"
rm -rf "$build"
mkdir -p "$build" "$here/bin"
cp "$here"/src/*.c "$here"/src/*.h "$build"/
patch --quiet -d "$build" -p1 < "$here/seed.patch"

sources=(main.c clustering.c grassberger.c hash.c list.c mat.c metropolis.c motif_ids.c output.c
         permutation.c prob.c random.c results.c role.c stubs.c switches.c)
(cd "$build" && cc -O3 -DUNIX=1 -Wno-macro-redefined -o "$here/bin/mfinder" "${sources[@]}" -lm)

echo "built $here/bin/mfinder"
shasum -a 256 "$here/bin/mfinder"
