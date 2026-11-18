# A13 daily challenge generation

The generator draws three distinct targets per student per local day. Each draw
uses 60% completion and 40% difficulty weighting when both types are available.
When yesterday has a full set, the new set includes at least one different target.

Existing sets keep their IDs and progress. If a set is incomplete, the generator
fills the missing slots without reusing an assigned target.
