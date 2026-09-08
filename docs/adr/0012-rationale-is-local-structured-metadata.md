# Rationale is local structured metadata

Accepted; records the rationale contract already shipped in v1.5.0. A rationale
keeps one question, at least two qualified options and exactly one chosen option
inside its knowledge unit; rejected options are neither false claims nor
superseded units. Options have no independent identity, references or anchors:
making them graph nodes would introduce resolution and cycle semantics merely
to explain a local choice, while `supersedes` remains the only unit relation.
