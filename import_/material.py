from __future__ import annotations

from typing import TYPE_CHECKING

from ..gltf.constants import TextureFilter, TextureWrap

if TYPE_CHECKING:
    import bpy
    from ..gltf.types import Gltf, TextureInfo, NormalTextureInfo
    from .texture import TextureImporter
    from ..importer import ImportSettings


EXT_MATERIALS_LAYERS = "CUSTOM_materials_layers"


class MaterialImporter:
    def __init__(
        self,
        gltf: "Gltf",
        texture_importer: "TextureImporter",
        settings: "ImportSettings",
    ) -> None:
        self.gltf = gltf
        self.texture_importer = texture_importer
        self.settings = settings
        self.blender_materials: dict[int, "bpy.types.Material"] = {}

    def import_all(self) -> None:
        if self.gltf.materials is None:
            return
        for i, gltf_mat in enumerate(self.gltf.materials):
            self.blender_materials[i] = self._import_material(i, gltf_mat)

    def get_blender_material(self, material_index: int) -> "bpy.types.Material | None":
        return self.blender_materials.get(material_index)

    def _import_material(self, index, gltf_mat) -> "bpy.types.Material":
        import bpy

        name = gltf_mat.name or f"Material_{index}"
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()

        principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (0, 0)
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)
        tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

        pbr = gltf_mat.pbr_metallic_roughness
        if pbr:
            self._apply_pbr(tree, principled, pbr)

        if gltf_mat.normal_texture:
            self._apply_normal_texture(tree, principled, gltf_mat.normal_texture)

        if gltf_mat.emissive_factor:
            r, g, b = gltf_mat.emissive_factor
            strength = max(r, g, b)
            if strength > 0:
                principled.inputs["Emission Color"].default_value = (
                    r / strength, g / strength, b / strength, 1.0,
                )
                principled.inputs["Emission Strength"].default_value = strength

        if gltf_mat.emissive_texture:
            self._apply_texture(tree, principled, "Emission Color", gltf_mat.emissive_texture, y_offset=-400)

        if gltf_mat.alpha_mode == "BLEND":
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "BLENDED"
        elif gltf_mat.alpha_mode == "MASK":
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "DITHERED"
            mat.alpha_threshold = gltf_mat.alpha_cutoff if gltf_mat.alpha_cutoff is not None else 0.5

        if gltf_mat.double_sided:
            mat.use_backface_culling = False
        else:
            mat.use_backface_culling = True

        # CUSTOM_materials_layers
        if gltf_mat.extensions and EXT_MATERIALS_LAYERS in gltf_mat.extensions:
            self._apply_layers(tree, gltf_mat.extensions[EXT_MATERIALS_LAYERS])

        # KHR_materials_unlit
        if gltf_mat.extensions and "KHR_materials_unlit" in gltf_mat.extensions:
            gltf_props = getattr(mat, "gltf_props", None)
            if gltf_props:
                gltf_props.unlit = True
            # Make it look unlit in viewport
            principled.inputs["Metallic"].default_value = 0.0
            principled.inputs["Roughness"].default_value = 1.0
            if "Specular IOR Level" in principled.inputs:
                principled.inputs["Specular IOR Level"].default_value = 0.0

        return mat

    def _apply_pbr(self, tree, principled, pbr) -> None:
        if pbr.base_color_factor:
            principled.inputs["Base Color"].default_value = tuple(pbr.base_color_factor[:4])
            if len(pbr.base_color_factor) > 3:
                principled.inputs["Alpha"].default_value = pbr.base_color_factor[3]

        if pbr.metallic_factor is not None:
            principled.inputs["Metallic"].default_value = pbr.metallic_factor

        if pbr.roughness_factor is not None:
            principled.inputs["Roughness"].default_value = pbr.roughness_factor

        if pbr.base_color_texture:
            self._apply_texture(tree, principled, "Base Color", pbr.base_color_texture, y_offset=0)

        if pbr.metallic_roughness_texture:
            self._apply_texture(tree, principled, "Metallic", pbr.metallic_roughness_texture, y_offset=-200)

    def _apply_texture(self, tree, principled, socket_name, texture_info, y_offset=0) -> None:
        if self.gltf.textures is None:
            return
        tex_index = texture_info.index
        if tex_index >= len(self.gltf.textures):
            return
        gltf_texture = self.gltf.textures[tex_index]
        if gltf_texture.source is None:
            return

        img = self.texture_importer.get_blender_image(gltf_texture.source)
        if img is None:
            return

        tex_node = tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.location = (-400, y_offset)

        if gltf_texture.sampler is not None and self.gltf.samplers:
            sampler = self.gltf.samplers[gltf_texture.sampler]
            if sampler.mag_filter == TextureFilter.NEAREST:
                tex_node.interpolation = "Closest"
            else:
                tex_node.interpolation = "Linear"
            if sampler.wrap_s == TextureWrap.CLAMP_TO_EDGE:
                tex_node.extension = "EXTEND"
            else:
                tex_node.extension = "REPEAT"

        self._apply_texture_transform(tree, tex_node, texture_info)

        tree.links.new(tex_node.outputs["Color"], principled.inputs[socket_name])

    def _apply_normal_texture(self, tree, principled, normal_info) -> None:
        if self.gltf.textures is None:
            return
        tex_index = normal_info.index
        if tex_index >= len(self.gltf.textures):
            return
        gltf_texture = self.gltf.textures[tex_index]
        if gltf_texture.source is None:
            return

        img = self.texture_importer.get_blender_image(gltf_texture.source)
        if img is None:
            return

        tex_node = tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.image.colorspace_settings.name = "Non-Color"
        tex_node.location = (-600, -600)

        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-300, -600)
        if normal_info.scale is not None:
            normal_map.inputs["Strength"].default_value = normal_info.scale

        self._apply_texture_transform(tree, tex_node, normal_info)

        tree.links.new(tex_node.outputs["Color"], normal_map.inputs["Color"])
        tree.links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])

    def _apply_texture_transform(self, tree, tex_node, texture_info) -> None:
        """Create Mapping + Texture Coordinate nodes for KHR_texture_transform."""
        if not hasattr(texture_info, "extensions") or not texture_info.extensions:
            return
        transform = texture_info.extensions.get("KHR_texture_transform")
        if transform is None:
            return

        offset = transform.get("offset", [0.0, 0.0])
        rotation = transform.get("rotation", 0.0)
        scale = transform.get("scale", [1.0, 1.0])

        # Convert glTF UV space back to Blender UV space (V is flipped)
        # offset_y_blender = 1 - scale_y_gltf - offset_y_gltf
        # rotation_blender = -rotation_gltf
        bl_offset_x = offset[0]
        bl_offset_y = 1.0 - scale[1] - offset[1]
        bl_rotation = -rotation
        bl_scale_x = scale[0]
        bl_scale_y = scale[1]

        tex_x = tex_node.location[0]
        tex_y = tex_node.location[1]

        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (tex_x - 200, tex_y)
        mapping.inputs["Location"].default_value = (bl_offset_x, bl_offset_y, 0.0)
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, bl_rotation)
        mapping.inputs["Scale"].default_value = (bl_scale_x, bl_scale_y, 1.0)

        tex_coord = tree.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (tex_x - 400, tex_y)

        tree.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])

    def _apply_layers(self, tree, ext: dict) -> None:
        """Rebuild a chain of glTF Material Layer group nodes feeding the
        Principled BSDF's Base Color, so the blend is visible in viewport.
        """
        from ..material_layer_nodes import ensure_layer_node_group

        layers = ext.get("layers") or []
        if not layers:
            return

        principled = next(
            (n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None,
        )
        if principled is None:
            return

        group = ensure_layer_node_group()

        # The base material's existing Base Color link (or default value)
        # becomes the input to the deepest layer's "Below Color". Capture and
        # disconnect it first.
        bc_socket = principled.inputs["Base Color"]
        previous_color_output = None
        previous_color_default = tuple(bc_socket.default_value)
        if bc_socket.is_linked:
            previous_link = bc_socket.links[0]
            previous_color_output = previous_link.from_socket
            tree.links.remove(previous_link)

        for i, layer in enumerate(layers):
            x = principled.location[0] - 400 - (len(layers) - i) * 350
            y = principled.location[1] + 400
            group_node = tree.nodes.new("ShaderNodeGroup")
            group_node.node_tree = group
            group_node.location = (x, y)
            name = layer.get("name")
            if name:
                group_node.label = name

            blend = layer.get("blendMode")
            if blend and blend.upper() != "MIX":
                group_node["blend_mode"] = blend.upper()

            pbr = layer.get("pbrMetallicRoughness") or {}

            bcf = pbr.get("baseColorFactor")
            if bcf and len(bcf) >= 4:
                group_node.inputs["Color"].default_value = (
                    bcf[0], bcf[1], bcf[2], bcf[3],
                )

            mf = pbr.get("metallicFactor")
            if mf is not None:
                group_node.inputs["Metallic"].default_value = float(mf)

            rf = pbr.get("roughnessFactor")
            if rf is not None:
                group_node.inputs["Roughness"].default_value = float(rf)

            # The first layer (i==0) is the BASE-most layer; its Below Color
            # inherits whatever previously fed Principled.Base Color.
            if i == 0:
                if previous_color_output is not None:
                    tree.links.new(previous_color_output, group_node.inputs["Below Color"])
                else:
                    group_node.inputs["Below Color"].default_value = previous_color_default
                previous_color_output = None
            elif previous_color_output is not None:
                tree.links.new(previous_color_output, group_node.inputs["Below Color"])
                previous_color_output = None

            self._apply_layer_texture(
                tree, group_node, "Color", pbr.get("baseColorTexture"),
                offset=(-400, 0),
            )
            self._apply_layer_texture(
                tree, group_node, "Metallic", pbr.get("metallicRoughnessTexture"),
                offset=(-400, -200),
            )
            self._apply_layer_normal(
                tree, group_node, layer.get("normalTexture"), offset=(-400, -400),
            )
            self._apply_layer_mask(
                tree, group_node, layer.get("mask"), offset=(-400, -600),
            )

            previous_color_output = group_node.outputs["Color"]

        # Topmost layer's Color → Principled.Base Color
        if previous_color_output is not None:
            tree.links.new(previous_color_output, principled.inputs["Base Color"])

    def _apply_layer_texture(
        self, tree, group_node, socket_name: str, tex_dict, offset,
    ) -> None:
        if not tex_dict:
            return
        tex_node = self._make_texture_node(tree, tex_dict, group_node, offset)
        if tex_node is None:
            return
        socket = group_node.inputs.get(socket_name)
        if socket is not None:
            tree.links.new(tex_node.outputs["Color"], socket)

    def _apply_layer_normal(self, tree, group_node, tex_dict, offset) -> None:
        if not tex_dict:
            return
        tex_node = self._make_texture_node(
            tree, tex_dict, group_node, offset, non_color=True,
        )
        if tex_node is None:
            return
        normal_map = tree.nodes.new("ShaderNodeNormalMap")
        normal_map.location = (
            group_node.location[0] + offset[0] + 200,
            group_node.location[1] + offset[1],
        )
        scale = tex_dict.get("scale")
        if scale is not None:
            normal_map.inputs["Strength"].default_value = float(scale)
        tree.links.new(tex_node.outputs["Color"], normal_map.inputs["Color"])
        socket = group_node.inputs.get("Normal")
        if socket is not None:
            tree.links.new(normal_map.outputs["Normal"], socket)

    def _apply_layer_mask(self, tree, group_node, mask, offset) -> None:
        if not mask:
            return
        socket = group_node.inputs.get("Mask")
        if socket is None:
            return

        source = (mask.get("source") or "TEXTURE").upper()
        channel = (mask.get("channel") or "R").upper()
        out_socket_name = "Alpha" if channel == "A" else "Color"

        if source == "TEXTURE":
            tex = mask.get("texture")
            tex_node = self._make_texture_node(
                tree, tex, group_node, offset, non_color=True,
            )
            if tex_node is None:
                return
            from_socket = (
                tex_node.outputs.get(out_socket_name)
                or tex_node.outputs["Color"]
            )
            if channel in ("G", "B"):
                sep = tree.nodes.new("ShaderNodeSeparateColor")
                sep.location = (
                    group_node.location[0] + offset[0] + 200,
                    group_node.location[1] + offset[1],
                )
                tree.links.new(tex_node.outputs["Color"], sep.inputs["Color"])
                ch_socket = sep.outputs.get(
                    {"R": "Red", "G": "Green", "B": "Blue"}[channel]
                )
                if ch_socket is not None:
                    tree.links.new(ch_socket, socket)
                    return
            tree.links.new(from_socket, socket)
            return

        if source == "VERTEX_COLOR":
            attr_name = mask.get("attribute") or "COLOR_0"
            vc = tree.nodes.new("ShaderNodeVertexColor")
            vc.layer_name = attr_name
            vc.location = (
                group_node.location[0] + offset[0],
                group_node.location[1] + offset[1],
            )
            from_socket = (
                vc.outputs["Alpha"] if channel == "A" else vc.outputs["Color"]
            )
            if channel in ("G", "B"):
                sep = tree.nodes.new("ShaderNodeSeparateColor")
                sep.location = (
                    group_node.location[0] + offset[0] + 200,
                    group_node.location[1] + offset[1],
                )
                tree.links.new(vc.outputs["Color"], sep.inputs["Color"])
                ch_socket = sep.outputs.get(
                    {"R": "Red", "G": "Green", "B": "Blue"}[channel]
                )
                if ch_socket is not None:
                    tree.links.new(ch_socket, socket)
                    return
            tree.links.new(from_socket, socket)

    def _make_texture_node(
        self, tree, tex_dict, anchor_node, offset, non_color: bool = False,
    ):
        """Create an Image Texture node from a textureInfo-shaped dict."""
        if not tex_dict or self.gltf.textures is None:
            return None
        idx = tex_dict.get("index")
        if idx is None or idx >= len(self.gltf.textures):
            return None
        gltf_tex = self.gltf.textures[idx]
        if gltf_tex.source is None:
            return None
        img = self.texture_importer.get_blender_image(gltf_tex.source)
        if img is None:
            return None

        tex_node = tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        if non_color:
            tex_node.image.colorspace_settings.name = "Non-Color"
        tex_node.location = (
            anchor_node.location[0] + offset[0],
            anchor_node.location[1] + offset[1],
        )

        if gltf_tex.sampler is not None and self.gltf.samplers:
            sampler = self.gltf.samplers[gltf_tex.sampler]
            if sampler.mag_filter == TextureFilter.NEAREST:
                tex_node.interpolation = "Closest"
            else:
                tex_node.interpolation = "Linear"
            if sampler.wrap_s == TextureWrap.CLAMP_TO_EDGE:
                tex_node.extension = "EXTEND"
            else:
                tex_node.extension = "REPEAT"

        # Apply KHR_texture_transform if present
        ext = tex_dict.get("extensions") or {}
        transform = ext.get("KHR_texture_transform")
        if transform:
            self._apply_texture_transform_dict(tree, tex_node, transform)

        return tex_node

    def _apply_texture_transform_dict(self, tree, tex_node, transform: dict) -> None:
        offset = transform.get("offset", [0.0, 0.0])
        rotation = transform.get("rotation", 0.0)
        scale = transform.get("scale", [1.0, 1.0])

        bl_offset_x = offset[0]
        bl_offset_y = 1.0 - scale[1] - offset[1]
        bl_rotation = -rotation
        bl_scale_x = scale[0]
        bl_scale_y = scale[1]

        tex_x = tex_node.location[0]
        tex_y = tex_node.location[1]

        mapping = tree.nodes.new("ShaderNodeMapping")
        mapping.location = (tex_x - 200, tex_y)
        mapping.inputs["Location"].default_value = (bl_offset_x, bl_offset_y, 0.0)
        mapping.inputs["Rotation"].default_value = (0.0, 0.0, bl_rotation)
        mapping.inputs["Scale"].default_value = (bl_scale_x, bl_scale_y, 1.0)

        tex_coord = tree.nodes.new("ShaderNodeTexCoord")
        tex_coord.location = (tex_x - 400, tex_y)

        tree.links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])
