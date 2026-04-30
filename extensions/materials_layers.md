# CUSTOM_materials_layers

A custom glTF extension that adds an ordered stack of additional material layers blended on top of the base material. Each layer carries its own PBR textures and is masked by either a texture channel or a vertex color attribute.

The base material is unchanged — viewers that don't understand the extension fall back to rendering the base material correctly. The extension carries the *extra* layers; the base layer is whatever sits in `pbrMetallicRoughness` / `normalTexture` on the material itself.

## Use cases

- Terrain blending: grass base + gravel/dirt/snow layered on top via splat maps or vertex paint
- Surface weathering: clean wall + dirt/rust layered on top
- Decals baked into a single material instead of overlapping geometry

## Extension placement

The extension is a **material-level** extension.

```json
{
  "materials": [
    {
      "name": "Ground",
      "pbrMetallicRoughness": {
        "baseColorTexture": { "index": 0 }
      },
      "normalTexture": { "index": 1 },
      "extensions": {
        "CUSTOM_materials_layers": {
          "layers": [ ... ]
        }
      }
    }
  ],
  "extensionsUsed": ["CUSTOM_materials_layers"]
}
```

## Schema

### Extension object

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `layers` | array of layer | Yes | Ordered list of layers, applied bottom-to-top over the base material |

The base material is layer 0 conceptually — the first entry in `layers` is layer 1, blended over it, the next is layer 2 over that, etc.

### Layer object

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `name` | string | No | Identifier for tooling |
| `pbrMetallicRoughness` | object | No | PBR inputs for this layer (same shape as glTF's `pbrMetallicRoughness`) |
| `normalTexture` | normalTextureInfo | No | Per-layer normal map |
| `mask` | object | Yes | Where this layer is visible |
| `blendMode` | string | No | How to blend with the layer below. Default `"MIX"` |

A layer with no `pbrMetallicRoughness` and no `normalTexture` is a no-op. At least one PBR channel should be present.

#### pbrMetallicRoughness

Same shape as the [glTF 2.0 pbrMetallicRoughness](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#reference-material-pbrmetallicroughness) object. All fields optional.

| Property | Type | Description |
|----------|------|-------------|
| `baseColorFactor` | array of 4 numbers | RGBA tint (default `[1,1,1,1]`) |
| `baseColorTexture` | textureInfo | Albedo texture |
| `metallicFactor` | number | (default `1.0`) |
| `roughnessFactor` | number | (default `1.0`) |
| `metallicRoughnessTexture` | textureInfo | Combined MR texture (G=roughness, B=metallic, per glTF convention) |

`KHR_texture_transform` is supported on each `textureInfo` and is the recommended way to encode per-layer UV tiling (e.g., gravel tiles 4× while grass tiles 1×).

### mask object

Defines the per-pixel weight `m ∈ [0,1]` used to blend this layer over what's below.

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `source` | string | Yes | `"TEXTURE"` or `"VERTEX_COLOR"` |
| `channel` | string | No | One of `"R"`, `"G"`, `"B"`, `"A"`. Default `"R"` |
| `texture` | textureInfo | If `source = "TEXTURE"` | Mask texture |
| `attribute` | string | No | Vertex color attribute name when `source = "VERTEX_COLOR"`. Default `"COLOR_0"` |
| `invert` | boolean | No | If true, use `1 - m`. Default `false` |

**Channel packing.** A single 4-channel mask texture can drive up to four layers — layer A reads `R`, layer B reads `G`, etc. This is the standard splat-map technique.

### blendMode

How the masked layer is composited over the layer below. `m` is the mask value, `c_below` is the surface color from layers beneath, `c_layer` is this layer's color.

| Value | Formula | Notes |
|-------|---------|-------|
| `"MIX"` | `lerp(c_below, c_layer, m)` | Default. Works for albedo, metallic, roughness; for normals, use reoriented-normal blending — see implementation notes |
| `"ADD"` | `c_below + c_layer * m` | Useful for emissive accents, light-leak decals |
| `"MULTIPLY"` | `lerp(c_below, c_below * c_layer, m)` | Useful for darkening passes (dirt, AO decals) |

Implementations MAY support a subset; if a `blendMode` is unrecognized, fall back to `"MIX"`.

## Full example

Grass base, gravel layer masked by a splat texture's R channel, with the gravel tiled 4×:

```json
{
  "materials": [
    {
      "name": "Ground",
      "pbrMetallicRoughness": {
        "baseColorTexture": { "index": 0 },
        "metallicRoughnessTexture": { "index": 1 },
        "roughnessFactor": 0.9
      },
      "normalTexture": { "index": 2 },
      "extensions": {
        "CUSTOM_materials_layers": {
          "layers": [
            {
              "name": "Gravel",
              "pbrMetallicRoughness": {
                "baseColorTexture": {
                  "index": 3,
                  "extensions": {
                    "KHR_texture_transform": { "scale": [4.0, 4.0] }
                  }
                },
                "metallicRoughnessTexture": {
                  "index": 4,
                  "extensions": {
                    "KHR_texture_transform": { "scale": [4.0, 4.0] }
                  }
                }
              },
              "normalTexture": {
                "index": 5,
                "extensions": {
                  "KHR_texture_transform": { "scale": [4.0, 4.0] }
                }
              },
              "mask": {
                "source": "TEXTURE",
                "texture": { "index": 6 },
                "channel": "R"
              },
              "blendMode": "MIX"
            }
          ]
        }
      }
    }
  ],
  "extensionsUsed": ["CUSTOM_materials_layers"]
}
```

## Engine implementation guide

### Minimal shader

```glsl
// Sample base
vec4 base_color  = texture(u_baseColor, uv) * u_baseColorFactor;
float metallic   = texture(u_metallicRough, uv).b * u_metallicFactor;
float roughness  = texture(u_metallicRough, uv).g * u_roughnessFactor;
vec3  normal     = sampleNormalMap(u_normal, uv);

// Apply each layer in order
for (int i = 0; i < u_layerCount; ++i) {
    Layer L = u_layers[i];
    float m = sampleMask(L);                 // 0..1
    if (L.invert) m = 1.0 - m;

    vec4 lc = texture(L.baseColor, L.uv) * L.baseColorFactor;
    float lm = texture(L.metallicRough, L.uv).b * L.metallicFactor;
    float lr = texture(L.metallicRough, L.uv).g * L.roughnessFactor;
    vec3  ln = sampleNormalMap(L.normal, L.uv);

    if (L.blendMode == ADD) {
        base_color.rgb += lc.rgb * m;
    } else if (L.blendMode == MULTIPLY) {
        base_color.rgb = mix(base_color.rgb, base_color.rgb * lc.rgb, m);
    } else { // MIX
        base_color = mix(base_color, lc, m);
        metallic   = mix(metallic, lm, m);
        roughness  = mix(roughness, lr, m);
        normal     = blendNormalsRNM(normal, ln, m);
    }
}
```

### Mask sampling

```glsl
float sampleMask(Layer L) {
    if (L.source == TEXTURE)      return texture(L.maskTex, L.maskUV)[L.channel];
    if (L.source == VERTEX_COLOR) return v_color[L.channel];
    return 0.0;
}
```

The vertex color attribute is whatever the engine binds to `v_color` for the named `attribute` (default `COLOR_0`, glTF's standard vertex color).

### Normal blending

Linear-mixing tangent-space normals produces wrong results — the magnitude shrinks. Use Reoriented Normal Mapping (RNM) or Whiteout blending. RNM:

```glsl
vec3 blendNormalsRNM(vec3 n1, vec3 n2, float t) {
    vec3 n2_blended = mix(vec3(0,0,1), n2, t);
    vec3 t_n = n1 * vec3(2,2,2) + vec3(-1,-1,0);
    vec3 u_n = n2_blended * vec3(-2,-2,2) + vec3(1,1,-1);
    return normalize(t_n * dot(t_n, u_n) - u_n * t_n.z);
}
```

For `t = 0` you get `n1`; for `t = 1` you get `n2`. Cheap fallback if you don't care about correctness: `normalize(mix(n1, n2, t))`.

### Performance

- Layer count is part of the shader permutation. Generate variants for 0, 1, 2, … layers, or use a uniform loop with branching.
- Texture sampling cost dominates: a 3-layer material with full PBR per layer is **12+ texture fetches per pixel**. Consider:
  - Sharing UV transforms across a layer's textures
  - Using vertex color masks instead of texture masks where possible (free vs. one fetch)
  - Skipping layers when `m < epsilon` for the whole triangle (compute on CPU per-mesh, not per-pixel)
- Channel-pack masks: one RGBA splat texture drives up to 4 layers.

### Interaction with other extensions

| Extension | How it interacts |
|-----------|-----------------|
| `KHR_texture_transform` | Supported per-textureInfo inside layer textures and inside `mask.texture`. Use it for per-layer tiling |
| `KHR_materials_unlit` | If the base material is unlit, layers are blended into the unlit color. No lighting either way |
| `KHR_materials_emissive_strength` | Applies to the base material only — layer emission is not in scope for v1 |

### Fallback behavior

A viewer that does not implement this extension will render the base material correctly because the extension data lives entirely in `extensions`. The glTF validator accepts unknown extensions when listed in `extensionsUsed` (not `extensionsRequired`); this extension SHOULD be listed in `extensionsUsed` only.

## Authoring (Blender)

This addon ships a node group called **`glTF Material Layer`**. The group internally mixes its `Color` input over the `Below Color` input using `Mask` as factor and outputs the blended `Color`, so **the blend is visible live in Blender's viewport**.

To author a layered material:

1. Build the base layer's color source (Image Texture, RGB node, or just leave the default).
2. Add a `glTF Material Layer` node (`Add → glTF Material Layer` in the shader editor).
3. Wire it as a chain link:
   - `Below Color` ← base color source (or the previous layer's `Color` output)
   - `Color` ← this layer's color (Image Texture or RGB)
   - `Mask` ← Image Texture (texture mask) or Color Attribute (vertex color mask)
   - `Metallic` / `Roughness` / `Normal` — set per-layer values (these are emitted to glTF but do not affect Blender's preview)
4. Connect the **topmost** layer's `Color` output to the Principled BSDF's `Base Color`.
5. To stack more layers, repeat: feed layer N's `Color` into layer N+1's `Below Color`.

The exporter walks back from `Principled.Base Color` through each `glTF Material Layer` node's `Below Color`, gathering the chain in base→top order. The deepest source becomes the base material's `pbrMetallicRoughness`; each layer node becomes one entry in the `layers` array.

For PBR channels other than color (metallic/roughness/normal), the layer group does not internally blend — only the Principled BSDF's own metallic/roughness/normal inputs drive Blender's viewport preview. Per-layer PBR is still emitted to glTF for your runtime to consume.

`blendMode` defaults to `MIX`. To set a different mode, add a custom property `blend_mode` on the group node (`"ADD"`, `"MULTIPLY"`).
