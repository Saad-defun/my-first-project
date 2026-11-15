# Job 001: analysis of the sample's cut-sheet details (chain 1)

*Read-only analysis of `CAD Files\001\SAMPLE.dxf`, 2026-11-15, to design the detail templates
for chain 2. Values from the sample are test data; none go into code or recipes.*

## Layout
- 4 details, titles on `C-INW-PLN-ANNO-N-SEWER`, one row, left to right: HC-417, 418, 419, 420.
- Spacing 12.108 / 12.110 / 12.109 m (median 12.109). The next title would sit at
  x 484714.070, same y. (`detail_layout` tool.)
- Each detail spans about 12.1 m wide and from 7.73 m below to 0.5 m above its title.

## Details differ by connection, not only by type
| HC | Type | Tag slots in the detail | Pipe objects |
|---|---|---|---|
| 417 | A | no FROM1 / FROM2 (head of the chain) | 199 |
| 418 | A | FROM1 | 199 |
| 419 | B (junction) | FROM1 and FROM2 | 313 |
| 420 | A | no FROM1 / FROM2 (head of a branch) | 252 |

- Type A details (417, 418, 420): 49 to 57 of the 57 structure objects (rings, concrete,
  cover, wall, grit bonding, bars, title, viewport, section marks) are identical relative to
  the title. Type B (419) shares almost none with type A.
- GTAGA writes only the slots an HC needs and leaves any other slot untouched. A template copied
  from an HC with a FROM1 slot would keep its old FROM1 text on an HC without an upstream pipe.
  So a template must match the HC's connections too, or unused slots must be removed or blanked.

## Fields
- 7 or 8 field texts per detail (29 in all): "FRML ...", "IL ..." (inlet, outlet, HC IL),
  "TO ..." / "FROM END CAP - ..." on TAGING, the TOSLOPE copy, and on HC-419 the cover level.
- Every field reads a tag object (TO, TOIL, FCFROM, FCFROMIL, TOSLOPE, CL260), and **all 29
  point to a tag in their own detail**. Copying a whole detail in one go keeps them working;
  after GTAGA fills the tags, updating the fields shows the new values.
- The "60.025" cover level in HC-419's detail is a field on CL260 (60.225 - 0.200), so it
  follows CL automatically. It is not a separate 0.460 rule.

## Depth
- The depth dimension shows S-D: 1.35 in all three type A details, 1.43 in the type B detail.
  S-D = CL - IL - outlet pipe diameter.
- All three type A HCs have the same depth (1.35), so the sample cannot show which parts of a
  type A detail move when the depth changes. Type B has one detail only.
- Concrete heights differ between A (0.977 / 0.994) and B (1.189 / 1.200), by more than the
  depth difference (0.08), so type B is not simply a stretched type A.

## What this means for templates (decisions needed)
1. **Template set.** By type and by connection (A head, A middle, B junction, ...), or one
   template per type with unused slots removed or blanked.
2. **Stretch rule.** Which band of each section moves with depth: needs the drawing standard or
   a second sample with type A details at other depths.
