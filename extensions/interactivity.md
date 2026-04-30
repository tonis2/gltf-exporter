# KHR_interactivity (minimal subset)

A behavior graph attached to a glTF node. Each graph is a small visual program
made of typed nodes (events, flow, math, actions) whose connections describe
control flow and data flow. The runtime executes the graph against the loaded
glTF document.

This addon implements a **minimal authoring subset** — enough to wire up
"on tick → write a node's translation," "on start → play animation," and
similar simple behaviors — and round-trips it with the importer.

## Extension placement

The extension lives at **two levels**:

```json
{
  "extensions": {
    "KHR_interactivity": {
      "types":        [ ... ],
      "declarations": [ ... ],
      "graphs":       [ { "nodes": [ ... ] }, ... ]
    }
  },
  "nodes": [
    {
      "name": "Cube",
      "extensions": {
        "KHR_interactivity": { "graph": 0 }
      }
    }
  ],
  "extensionsUsed": ["KHR_interactivity"]
}
```

The root `extensions.KHR_interactivity` carries the graph data. Each glTF
node that owns a behavior graph just references one by index. Multiple
glTF nodes can point at the same graph.

## Schema

### Root extension object

| Property | Type | Description |
|----------|------|-------------|
| `types` | array of type | Type signatures referenced by value entries |
| `declarations` | array of declaration | Operation declarations referenced by graph nodes |
| `graphs` | array of graph | The behavior graphs |

### Type

| Property | Type | Description |
|----------|------|-------------|
| `signature` | string | One of `"float"`, `"bool"`, `"int"` (this addon's subset) |

### Declaration

| Property | Type | Description |
|----------|------|-------------|
| `op` | string | The operation id, e.g. `"math/add"` |

### Graph

| Property | Type | Description |
|----------|------|-------------|
| `nodes` | array of graph-node | The nodes in this graph |

### Graph node

| Property | Type | Description |
|----------|------|-------------|
| `declaration` | int | Index into root `declarations` |
| `configuration` | array of config-entry | Static per-node properties |
| `values` | array of value-entry | Data-flow inputs |
| `flows` | array of flow-entry | Control-flow outputs |

### config-entry

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | The configuration slot name |
| `value` | array | The value (single-element list, e.g. `["/nodes/0/translation/0"]`) |

### value-entry

Either a literal or a reference to another node's value output.

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | The value input socket name |
| `value` | array | (literal) single-element list with the literal value |
| `type` | int | (literal) index into root `types` |
| `node` | int | (reference) source node index in this graph |
| `socket` | string | (reference) source output socket name |

### flow-entry

| Property | Type | Description |
|----------|------|-------------|
| `id` | string | The flow output socket name on the source node |
| `node` | int | Target node index in this graph |
| `socket` | string | Target flow input socket name on the target node |

## Supported node operations (this addon)

| `op` | Inputs (flow / values) | Outputs | Notes |
|------|------------------------|---------|-------|
| `event/onStart` | — | flow `out` | Fires once at graph start |
| `event/onTick`  | — | flow `out`, value `timeSinceLastTick` (float) | Fires every frame |
| `flow/sequence` | flow `in` | flow `0`, `1`, `2` | Fires outputs in order |
| `flow/branch`   | flow `in`, value `condition` (bool) | flow `true`, `false` | Routes by condition |
| `math/add`      | value `a`, `b` (float) | value `value` (float) | |
| `math/eq`       | value `a`, `b` (float) | value `value` (bool) | |
| `pointer/set`   | flow `in`, value `value` (float); config `pointer` (string) | flow `out`, `err` | Writes `value` to the JSON pointer |
| `animation/start` | flow `in`, value `animation` (int) | flow `out`, `err` | Starts the animation at index |

The `pointer` configuration on `pointer/set` is a JSON pointer into the loaded
document, e.g. `/nodes/3/translation/0` for the X component of node 3's
translation, or `/materials/0/pbrMetallicRoughness/baseColorFactor/0` for the
red component of a material's base color.

## Full example

"On every tick, set node 0's X translation to a constant 1.0":

```json
{
  "extensions": {
    "KHR_interactivity": {
      "types": [{"signature": "float"}, {"signature": "bool"}, {"signature": "int"}],
      "declarations": [
        {"op": "event/onTick"},
        {"op": "pointer/set"}
      ],
      "graphs": [{
        "nodes": [
          {
            "declaration": 0,
            "flows": [{"id": "out", "node": 1, "socket": "in"}]
          },
          {
            "declaration": 1,
            "configuration": [{"id": "pointer", "value": ["/nodes/0/translation/0"]}],
            "values": [{"id": "value", "value": [1.0], "type": 0}]
          }
        ]
      }]
    }
  },
  "nodes": [
    {
      "name": "Cube",
      "extensions": {"KHR_interactivity": {"graph": 0}}
    }
  ],
  "extensionsUsed": ["KHR_interactivity"]
}
```

## Authoring (Blender)

1. Select an object in the 3D viewport.
2. In Object Properties, expand **glTF Interactivity** and click the `+`
   button to create a new graph (or pick an existing one from the dropdown).
3. Switch a Node Editor area to the **Interactivity** tree type (the dropdown
   now lists Shader / Geometry / Compositor / **Interactivity**).
4. Use **Add → Interactivity** to drop event, flow, math, and action nodes.
5. Wire flow outputs (white sockets) to flow inputs to define sequencing.
   Wire value outputs to value inputs for data flow.
6. Set the `Pointer` field on `Pointer Set` nodes and any literal socket
   defaults you need.

Multiple objects can be assigned the same NodeTree — they share a single graph
in the exported glTF and are deduplicated automatically.

## Engine implementation guide

A minimal interpreter:

1. **Index nodes** by graph node index for fast lookup.
2. **Resolve values lazily**: when a node needs an input, look at the value
   entry — either it's a literal (use it directly) or a reference (recurse
   into the source node's pure compute, e.g. `math/add` reads its own inputs
   recursively).
3. **Execute flow eagerly**: when an event fires, follow the source node's
   flow output to its target node, run that node, then follow that node's
   flow output, and so on.
4. **Pointer writes** apply to the loaded glTF's JSON tree (or your runtime
   mirror of it). Re-uploading transforms / material parameters to the GPU
   is up to the engine.

## Fallback behavior

A viewer that doesn't implement KHR_interactivity will render the static scene
correctly because the extension lives entirely in `extensions`. Don't list it
in `extensionsRequired` — interactivity is graceful-fallback by nature.
