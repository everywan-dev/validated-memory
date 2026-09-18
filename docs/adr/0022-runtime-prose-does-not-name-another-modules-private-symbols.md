# Runtime prose does not name another module's private symbols

Runtime comments and docstrings may describe a cross-module precondition,
effect, or stable public boundary, but they do not identify that boundary by a
single-leading-underscore symbol owned by another `validated_memory` module.
A private spelling is free to change and its continued existence would not
make the prose true; naming it turns an explanation into an undocumented
dependency.

When the explanation needs a durable pointer, it names a public interface or a
versioned document or ADR. Otherwise it states the invariant directly. A
same-module private name remains useful local navigation. Tests are outside
this rule because their verification arguments may need to identify the exact
mechanism they exercise; Python protocol dunders and explicitly qualified
external-library names are outside the package boundary too.

A structural test reuses the runtime docstring and comment scanner, derives
private-symbol ownership from the package's syntax, and rejects a private name
owned only by another module. An explicit external import remains external even
when its local alias collides with a package module or class. An internal star
import makes exact static ownership unavailable and is therefore refused by the
same gate rather than silently guessed. The accepted tree has neither forbidden
citations nor internal star imports. This extends ADR 0010's mechanical
documentary-reference policy without making private symbols public or requiring
any private spelling to continue to exist.
