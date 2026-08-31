# Finite views of periodic chains, layers, and frameworks

Use this page when an infinite periodic topology must be shown as a finite,
readable image. The finite window is part of the scientific representation and
must be selected from canonical periodic bond records, not screen proximity.

## First identify periodic dimensionality

For every canonical crossing bond, record the integer image shift
`S = (sa, sb, sc)` that maps one endpoint to its nearest bonded image. Reduce
the nonzero shifts to an independent translation basis. Its rank classifies the
displayed bonded component:

- rank 0: finite molecule or cluster;
- rank 1: chain;
- rank 2: layer;
- rank 3: framework.

Do not infer this rank from apparent shape, lattice lengths, or the camera.

## Default finite-window policy

Start from one central crystallographic cell or one explicitly selected central
fragment. For each canonical bond crossing that window boundary, add only the
nearest image endpoint required to draw that bond. Include the opposite
direction when both ends of a low-dimensional periodic object must be visible.
This is the **one-hop crossing-bond closure**.

One-hop closure is preferred over copying complete cells because it:

- preserves every central-cell site exactly once;
- shows that the bonded object continues across the boundary;
- minimizes added atoms and occlusion;
- avoids an arbitrary left/right bias;
- does not imply that a finite endpoint is chemically terminated.

If an added endpoint belongs to a finite molecular fragment, add the complete
fragment image rather than one atom. For an infinite framework, add only the
canonical image endpoints or the smallest connected image patch needed by the
requested figure.

## When a larger window is justified

Use `-N ... 0 ... +N` repeats only when the request concerns a repeat pattern,
correlation length, defect spacing, phonon phase, or another property that
requires multiple periods. Record `N`, the translation basis, and why one-hop
closure was insufficient. Do not replicate whole cells solely for visual
symmetry.

For Gamma-point displacement modes, every periodic image inherits the same
source-atom vector. For non-Gamma modes, apply the wave-vector phase to each
image; copying identical arrows is wrong.

## Keep chemistry and appearance separate

- Use MolCrysKit canonical site, fragment, and PBC bond records.
- Use one shared `bond_scale` for source topology and displayed bonds when the
  structure is uniformly compressed or expanded.
- `bond_radius`, `atom_scale`, wireframe, opacity, and camera do not change
  connectivity.
- Do not add, delete, or reconnect bonds based on projected overlap.
- Do not use `cluster` to erase PBC semantics before deriving the finite window.

## Required provenance and checks

Record:

- central-cell/source atom count;
- displayed atom and bond counts;
- canonical crossing records and integer image shifts;
- translation rank and basis;
- image atoms or complete fragments added;
- finite-window policy (`one-hop`, `strict cell`, or explicit repeats);
- global `bond_scale` and any exceptional pair rules;
- false-contact counts and missing intended-bond counts.

Inspect the final image for one-sided continuation, duplicated central atoms,
screen-spanning bonds, excessive replicas, clipped arrowheads, and endpoints
that could be misread as chemical termination. Label the image or caption when
the finite truncation is not visually self-evident.
