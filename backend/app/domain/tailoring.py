"""Non-destructive variant resolution.

A variant never copies or edits the baseline. It stores overrides keyed by
(node_id, item_id) and user-added items. ``resolve`` builds a new graph object
for display/export; the baseline object passed in is not mutated.
"""

from __future__ import annotations

from ..models import (
    AddedItem,
    Direction,
    ItemOrigin,
    ItemOverride,
    ItemState,
    ProcessGraph,
    VariantChanges,
)


class ChangeRejected(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def validate_changes(baseline: ProcessGraph, changes: VariantChanges) -> VariantChanges:
    nodes = {n.id: n for n in baseline.nodes}
    base_items = {(n.id, i.id) for n in baseline.nodes for i in (*n.inputs, *n.outputs)}
    seen_over: set[tuple[str, str]] = set()
    overrides: list[ItemOverride] = []
    for o in changes.overrides:
        key = (o.node_id, o.item_id)
        if key not in base_items:
            raise ChangeRejected(f"Override targets unknown baseline item '{o.item_id}' on node '{o.node_id}'.")
        if key in seen_over:
            raise ChangeRejected(f"More than one override for item '{o.item_id}'.")
        if o.name is not None and not o.name.strip():
            raise ChangeRejected("An item name cannot be empty.")
        seen_over.add(key)
        # Drop no-op overrides so 'restore' leaves no residue.
        if o.state is None and o.name is None and o.description is None:
            continue
        overrides.append(o)
    added: list[AddedItem] = []
    all_ids = {i for _, i in base_items}
    for a in changes.added:
        if a.node_id not in nodes:
            raise ChangeRejected(f"Added item targets unknown node '{a.node_id}'.")
        if a.item.origin != ItemOrigin.user_added:
            raise ChangeRejected("Items added in a variant must have origin 'user_added'.")
        if a.item.id in all_ids:
            raise ChangeRejected(f"Item id '{a.item.id}' is already used.")
        if not a.item.name.strip():
            raise ChangeRejected("An item name cannot be empty.")
        all_ids.add(a.item.id)
        added.append(a)
    return VariantChanges(overrides=overrides, added=added)


def resolve(baseline: ProcessGraph, changes: VariantChanges) -> ProcessGraph:
    g = baseline.model_copy(deep=True)
    over = {(o.node_id, o.item_id): o for o in changes.overrides}
    for n in g.nodes:
        for items in (n.inputs, n.outputs):
            for it in items:
                o = over.get((n.id, it.id))
                if not o:
                    continue
                if o.state is not None:
                    it.state = o.state
                if o.name is not None or o.description is not None:
                    it.edited_from_id = it.id
                    it.name = o.name if o.name is not None else it.name
                    it.description = o.description if o.description is not None else it.description
    by_id = {n.id: n for n in g.nodes}
    for a in changes.added:
        target = by_id[a.node_id]
        (target.inputs if a.item.direction == Direction.input else target.outputs).append(a.item.model_copy(deep=True))
    g.provenance = {**g.provenance, "resolved_variant": True}
    return g


def summarise_diff(old: VariantChanges, new: VariantChanges, baseline: ProcessGraph) -> list[str]:
    names = {(n.id, i.id): i.name for n in baseline.nodes for i in (*n.inputs, *n.outputs)}
    labels = {n.id: n.label or n.id for n in baseline.nodes}
    out: list[str] = []
    o_over = {(o.node_id, o.item_id): o for o in old.overrides}
    n_over = {(o.node_id, o.item_id): o for o in new.overrides}
    for key in sorted(set(o_over) | set(n_over)):
        a, b = o_over.get(key), n_over.get(key)
        item = names.get(key, key[1])
        step = labels.get(key[0], key[0])
        if b is None:
            out.append(f"Restored '{item}' on '{step}' to baseline")
            continue
        if (a.state if a else None) != b.state:
            if b.state == ItemState.excluded:
                out.append(f"Tailored out '{item}' on '{step}'" + (f" ({b.reason})" if b.reason else ""))
            elif a and a.state == ItemState.excluded:
                out.append(f"Re-included '{item}' on '{step}'")
        if (a.name if a else None) != b.name and b.name is not None:
            out.append(f"Renamed '{item}' to '{b.name}' on '{step}'")
        if (a.description if a else None) != b.description and b.description is not None:
            out.append(f"Edited description of '{item}' on '{step}'")
    o_add = {a.item.id: a for a in old.added}
    n_add = {a.item.id: a for a in new.added}
    for iid, a in n_add.items():
        step = labels.get(a.node_id, a.node_id)
        prev = o_add.get(iid)
        if prev is None:
            out.append(f"Added {a.item.direction.value} '{a.item.name}' to '{step}'")
        elif prev.item != a.item:
            if prev.item.state != a.item.state:
                out.append(f"{'Tailored out' if a.item.state == ItemState.excluded else 'Re-included'} "
                           f"user item '{a.item.name}' on '{step}'")
            if prev.item.name != a.item.name or prev.item.description != a.item.description:
                out.append(f"Edited user item '{a.item.name}' on '{step}'")
    for iid, a in o_add.items():
        if iid not in n_add:
            out.append(f"Removed user-added item '{a.item.name}' from '{labels.get(a.node_id, a.node_id)}'")
    return out or ["No changes"]
