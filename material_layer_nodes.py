"""Shader node groups used by the glTF layered material extension.

The `glTF Material Layer` group blends its `Color` input over the `Below Color`
input using `Mask` as factor, so the result is visible in Blender's viewport.
Chain layers by feeding one layer's `Color` output into the next layer's
`Below Color` input, then connect the topmost layer's `Color` output to the
Principled BSDF's `Base Color`. The exporter reads the chain back out as glTF
CUSTOM_materials_layers.

Metallic / Roughness / Normal sockets are present on the group so the user can
author per-layer PBR values, but they are not internally wired — they are
metadata read by the exporter only. Connect them directly into the Principled
BSDF if you need per-layer PBR preview.
"""
from __future__ import annotations

import bpy


LAYER_NODE_GROUP_NAME = "glTF Material Layer"


def ensure_layer_node_group() -> "bpy.types.NodeTree":
    """Create the layer node group if it doesn't already exist."""
    existing = bpy.data.node_groups.get(LAYER_NODE_GROUP_NAME)
    if existing is not None:
        return existing

    group = bpy.data.node_groups.new(LAYER_NODE_GROUP_NAME, "ShaderNodeTree")

    # --- Inputs ---
    below = group.interface.new_socket(
        name="Below Color", in_out="INPUT", socket_type="NodeSocketColor",
    )
    below.default_value = (1.0, 1.0, 1.0, 1.0)

    color = group.interface.new_socket(
        name="Color", in_out="INPUT", socket_type="NodeSocketColor",
    )
    color.default_value = (1.0, 1.0, 1.0, 1.0)

    metallic = group.interface.new_socket(
        name="Metallic", in_out="INPUT", socket_type="NodeSocketFloat",
    )
    metallic.default_value = 0.0
    metallic.min_value = 0.0
    metallic.max_value = 1.0

    roughness = group.interface.new_socket(
        name="Roughness", in_out="INPUT", socket_type="NodeSocketFloat",
    )
    roughness.default_value = 0.5
    roughness.min_value = 0.0
    roughness.max_value = 1.0

    normal = group.interface.new_socket(
        name="Normal", in_out="INPUT", socket_type="NodeSocketVector",
    )
    normal.default_value = (0.0, 0.0, 1.0)

    mask = group.interface.new_socket(
        name="Mask", in_out="INPUT", socket_type="NodeSocketFloat",
    )
    mask.default_value = 1.0
    mask.min_value = 0.0
    mask.max_value = 1.0

    # --- Outputs ---
    group.interface.new_socket(
        name="Color", in_out="OUTPUT", socket_type="NodeSocketColor",
    )

    # --- Internal graph: mix(Below Color, Color, Mask) ---
    in_node = group.nodes.new("NodeGroupInput")
    in_node.location = (-300, 0)
    out_node = group.nodes.new("NodeGroupOutput")
    out_node.location = (300, 0)

    mix = group.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.clamp_factor = True
    mix.location = (0, 0)

    group.links.new(in_node.outputs["Mask"], mix.inputs["Factor"])
    # ShaderNodeMix RGBA inputs are named "A" (index 6) and "B" (index 7).
    # Use names to be version-agnostic.
    a_socket = next(s for s in mix.inputs if s.name == "A" and s.type == "RGBA")
    b_socket = next(s for s in mix.inputs if s.name == "B" and s.type == "RGBA")
    out_color = next(s for s in mix.outputs if s.name == "Result" and s.type == "RGBA")
    group.links.new(in_node.outputs["Below Color"], a_socket)
    group.links.new(in_node.outputs["Color"], b_socket)
    group.links.new(out_color, out_node.inputs["Color"])

    return group


class NODE_OT_add_gltf_material_layer(bpy.types.Operator):
    """Add a glTF Material Layer node to the active shader graph."""
    bl_idname = "node.add_gltf_material_layer"
    bl_label = "glTF Material Layer"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (
            space is not None
            and space.type == "NODE_EDITOR"
            and getattr(space, "tree_type", "") == "ShaderNodeTree"
            and space.edit_tree is not None
        )

    def execute(self, context):
        group = ensure_layer_node_group()
        tree = context.space_data.edit_tree
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = group
        node.label = "glTF Material Layer"
        node.location = context.space_data.cursor_location
        for n in tree.nodes:
            n.select = False
        node.select = True
        tree.nodes.active = node
        return {"FINISHED"}


def menu_func_add(self, context):
    space = context.space_data
    if space is None or getattr(space, "tree_type", "") != "ShaderNodeTree":
        return
    self.layout.operator(
        NODE_OT_add_gltf_material_layer.bl_idname,
        text="glTF Material Layer",
        icon="NODE_MATERIAL",
    )


def register() -> None:
    bpy.utils.register_class(NODE_OT_add_gltf_material_layer)
    bpy.types.NODE_MT_add.append(menu_func_add)


def unregister() -> None:
    bpy.types.NODE_MT_add.remove(menu_func_add)
    bpy.utils.unregister_class(NODE_OT_add_gltf_material_layer)
